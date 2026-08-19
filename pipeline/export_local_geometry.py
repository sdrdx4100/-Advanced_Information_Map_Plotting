"""One-off (re-run when routes change) export of a compact, self-contained
geometry bundle for the local_only confidential-CSV viewer, so that app no
longer needs to sit nested inside this repo or share this repo's Python env.

Reads only already-public, already-committed files under public/data/ (the
same data the deployed map itself serves) and writes a single JSON bundle:

    {
      "routes": ["東名", "新東名", ...],
      "facilities": {"東名": {"小牧IC": [lon, lat], ...}, ...},
      "chains": {"東名": {"上り": [[[lon, lat, cum_dist], ...], ...], ...}, ...}
    }

"chains" values are a list of chains (usually one per direction, but kept as
a list since a route can have disconnected fragments); each chain is an
ordered list of [lon, lat, cum_dist_m] points, built from the *-250 window's
segment endpoints (250m vertex spacing is already what the public map itself
renders at for these routes, so it's plenty for placing a CSV's distance
values on the map — this isn't the gradient-measurement geometry, just a
positioning reference).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
import urllib.request
import zipfile

HERE = os.path.dirname(__file__)
PUBLIC_DATA_DIR = os.path.join(HERE, "..", "public", "data")
OUT_PATH = os.path.join(HERE, "local_only", "geometry_bundle.json")
DEFINITIONS_PATH = os.path.join(HERE, "local_only", "facility_definitions.json")

N06_ZIP_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N06/N06-22/N06-22_GML.zip"
N06_CACHE_DIR = os.path.join(HERE, "data", "raw", "n06-22")
N06_ZIP_PATH = os.path.join(N06_CACHE_DIR, "N06-22_GML.zip")
N06_JOINT_PATH = os.path.join(N06_CACHE_DIR, "UTF-8", "N06-22_Joint.geojson")

# N06 points are drawn at the connection itself, while our route reference is
# sampled from the OSM carriageway. 300 m covers interchange geometry without
# broadly pulling in unrelated crossings in dense urban areas.
OFFICIAL_SNAP_MAX_M = 300.0
ENDPOINT_SNAP_MAX_M = 750.0
ENDPOINT_CHAINAGE_M = 1000.0


MAX_MERGE_GAP_M = 15000.0


def _dist_m(p1, p2):
    lat_avg = math.radians((p1[1] + p2[1]) / 2)
    dx = math.radians(p2[0] - p1[0]) * math.cos(lat_avg)
    dy = math.radians(p2[1] - p1[1])
    return math.sqrt(dx * dx + dy * dy) * 6371000.0


def _ensure_n06_joint_data():
    """Download/cache MLIT's official IC/JCT point layer when necessary."""
    if os.path.exists(N06_JOINT_PATH):
        return N06_JOINT_PATH
    os.makedirs(N06_CACHE_DIR, exist_ok=True)
    if not os.path.exists(N06_ZIP_PATH):
        print(f"downloading official MLIT N06 joint data: {N06_ZIP_URL}")
        urllib.request.urlretrieve(N06_ZIP_URL, N06_ZIP_PATH)
    with zipfile.ZipFile(N06_ZIP_PATH) as archive:
        wanted = next(
            name for name in archive.namelist()
            if name.replace("\\", "/").endswith("UTF-8/N06-22_Joint.geojson")
        )
        archive.extract(wanted, N06_CACHE_DIR)
    return N06_JOINT_PATH


def _canonical_name(name, kind):
    name = unicodedata.normalize("NFKC", str(name or "")).strip()
    name = re.sub(r"\s+", "", name)
    name = name.replace("インターチェンジ", "IC").replace("ジャンクション", "JCT")
    if kind == "SIC":
        name = name.replace("スマートIC", "SIC")
    suffix = {"IC": "IC", "SIC": "SIC", "JCT": "JCT"}.get(kind)
    if suffix and not re.search(r"(?:IC|SIC|JCT)$", name, re.IGNORECASE):
        name += suffix
    return name


def _infer_kind(name, kind=None):
    if kind and kind != "OTHER":
        return kind
    normalized = unicodedata.normalize("NFKC", str(name or ""))
    checks = (
        ("JCT", ("JCT", "ジャンクション")),
        ("SIC", ("SIC", "スマートIC", "スマートインターチェンジ")),
        ("IC", ("IC", "インターチェンジ")),
        ("SA", ("SA", "サービスエリア")),
        ("PA", ("PA", "パーキングエリア")),
    )
    for inferred, patterns in checks:
        if any(pattern in normalized for pattern in patterns):
            return inferred
    return kind or "OTHER"


def _facility_aliases(name, kind):
    aliases = {name, unicodedata.normalize("NFKC", name)}
    if kind == "IC" and name.endswith("IC"):
        aliases.add(name[:-2] + "インターチェンジ")
    elif kind == "SIC" and name.endswith("SIC"):
        aliases.add(name[:-3] + "スマートIC")
        aliases.add(name[:-3] + "スマートインターチェンジ")
    elif kind == "JCT" and name.endswith("JCT"):
        aliases.add(name[:-3] + "ジャンクション")
    return sorted(a for a in aliases if a)


def load_official_joints():
    path = _ensure_n06_joint_data()
    with open(path, "r", encoding="utf-8") as f:
        fc = json.load(f)
    kind_by_code = {"1": "IC", "2": "SIC", "3": "JCT"}
    latest = {}
    for feat in fc.get("features", []):
        p = feat.get("properties", {})
        if str(p.get("N06_014")) != "9999":
            continue
        kind = kind_by_code.get(str(p.get("N06_019")))
        if not kind:
            continue
        coords = feat.get("geometry", {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        name = _canonical_name(p.get("N06_018"), kind)
        if not name:
            continue
        item = {
            "name": name,
            "kind": kind,
            "coordinates": [float(coords[0]), float(coords[1])],
            "source": "国土数値情報 N06",
            "source_id": p.get("N06_015"),
            "opened": p.get("N06_012"),
            "aliases": _facility_aliases(name, kind),
        }
        # The current (end=9999) record is unique in principle; retaining the
        # newest opening/update value makes the merge deterministic if not.
        identity = p.get("N06_015") or f"{name}|{coords[0]:.5f}|{coords[1]:.5f}"
        old = latest.get(identity)
        if old is None or (item["opened"] or 0) >= (old["opened"] or 0):
            latest[identity] = item
    return list(latest.values())


def _nearest_position(chains_by_direction, lon, lat, max_snap_m):
    matches = {}
    for direction, chains in chains_by_direction.items():
        direction_matches = []
        for chain_index, chain in enumerate(chains):
            best = None
            for point in chain:
                dist = _dist_m((lon, lat), point)
                if best is None or dist < best[0]:
                    best = (dist, point[2])
            near_endpoint = (
                best is not None
                and (
                    best[1] <= ENDPOINT_CHAINAGE_M
                    or chain[-1][2] - best[1] <= ENDPOINT_CHAINAGE_M
                )
            )
            allowed_distance = ENDPOINT_SNAP_MAX_M if near_endpoint else max_snap_m
            if best is not None and best[0] <= allowed_distance:
                direction_matches.append({
                    "chain": chain_index,
                    "chainage_m": round(best[1], 1),
                    "snap_distance_m": round(best[0], 1),
                })
        if direction_matches:
            matches[direction] = sorted(
                direction_matches,
                key=lambda item: (item["snap_distance_m"], item["chain"]),
            )
    return matches


def _route_bbox(chains_by_direction, padding=0.01):
    points = [p for chains in chains_by_direction.values() for chain in chains for p in chain]
    if not points:
        return None
    return (
        min(p[0] for p in points) - padding,
        min(p[1] for p in points) - padding,
        max(p[0] for p in points) + padding,
        max(p[1] for p in points) + padding,
    )


def _inside_bbox(point, bbox):
    return bbox and bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def _facility_id(route_slug, name):
    digest = hashlib.sha1(f"{route_slug}|{name}".encode("utf-8")).hexdigest()[:12]
    return f"{route_slug}-{digest}"


def _is_filename_facility(name, kind):
    """Return true for one unambiguous IC/SIC/JCT name.

    OSM sometimes stores combined labels such as ``Foo SA;Foo SIC`` or
    ``Bar JCT/IC``. They remain available as map aliases, but must not become
    generated CSV boundaries because they duplicate the official point and
    make longest-name filename parsing ambiguous.
    """
    if kind not in {"IC", "SIC", "JCT"}:
        return False
    if any(separator in name for separator in (";", "/", "、", ",", "・")):
        return False
    return bool(re.search(r"(?:IC|SIC|JCT)$", name))


def merge_fragments(fragments, max_gap_m=MAX_MERGE_GAP_M):
    """Way-stitching (in the main pipeline) often leaves a direction split
    into several disconnected chains — small node mismatches, a short
    differently-tagged stretch, etc. For chainage lookups over a long IC-to-IC
    span, both ICs need to land on the *same* chain, so greedily reconnect
    fragments end-to-end by nearest endpoint (trying both orientations),
    rebuilding one continuous cum_dist per merged chain. Fragments with no
    neighbor within max_gap_m stay separate rather than being joined wrong."""
    remaining = [[list(p) for p in f] for f in fragments if len(f) >= 2]
    if not remaining:
        return []
    remaining.sort(key=lambda f: f[-1][2], reverse=True)

    merged = []
    while remaining:
        spine = remaining.pop(0)
        changed = True
        while changed and remaining:
            changed = False
            best = None  # (list_index, attach_at, reverse, gap)
            s_start, s_end = spine[0], spine[-1]
            for i, frag in enumerate(remaining):
                f_start, f_end = frag[0], frag[-1]
                for attach_at, reverse, gap in (
                    ("end", False, _dist_m(s_end, f_start)),
                    ("end", True, _dist_m(s_end, f_end)),
                    ("start", False, _dist_m(s_start, f_end)),
                    ("start", True, _dist_m(s_start, f_start)),
                ):
                    if best is None or gap < best[3]:
                        best = (i, attach_at, reverse, gap)
            if best is None or best[3] > max_gap_m:
                break
            i, attach_at, reverse, gap = best
            frag = [list(p) for p in remaining.pop(i)]
            if reverse:
                total = frag[-1][2]
                frag.reverse()
                for p in frag:
                    p[2] = total - p[2]
            if attach_at == "end":
                offset = spine[-1][2] + gap
                for p in frag:
                    p[2] += offset
                spine.extend(frag)
            else:
                frag_len = frag[-1][2]
                shift = frag_len + gap
                for p in spine:
                    p[2] += shift
                spine = frag + spine
            changed = True
        merged.append(spine)
    return merged


def build_chains_for_route(slug: str):
    path = os.path.join(PUBLIC_DATA_DIR, f"road-segments-{slug}-250.geojson")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        fc = json.load(f)

    # group by (direction, chain_rank), each group is one continuous chain
    groups: dict[tuple[str, int], list] = {}
    for feat in fc["features"]:
        p = feat["properties"]
        key = (p["direction"], p["chain_rank"])
        groups.setdefault(key, []).append(feat)

    by_direction: dict[str, list] = {}
    for (direction, _rank), feats in groups.items():
        feats.sort(key=lambda f: f["properties"]["cum_dist_start"])
        points: list[list[float]] = []
        for i, feat in enumerate(feats):
            p = feat["properties"]
            coords = feat["geometry"]["coordinates"]
            start_cd, end_cd = p["cum_dist_start"], p["cum_dist_end"]
            n = len(coords)
            for j, (lon, lat) in enumerate(coords):
                cd = start_cd + (end_cd - start_cd) * (j / (n - 1) if n > 1 else 0.0)
                # avoid a duplicate point at segment boundaries
                if points and i > 0 and j == 0:
                    continue
                points.append([round(lon, 6), round(lat, 6), round(cd, 1)])
        by_direction.setdefault(direction, []).append(points)

    for direction in by_direction:
        by_direction[direction] = merge_fragments(by_direction[direction])
    return by_direction


def main():
    with open(os.path.join(PUBLIC_DATA_DIR, "routes.json"), "r", encoding="utf-8") as f:
        routes = json.load(f)

    official_joints = load_official_joints()
    bundle = {
        "schema_version": 2,
        "routes": [],
        "facilities": {},
        "chains": {},
        "facility_definitions_file": "facility_definitions.json",
    }
    definitions = {
        "schema_version": 1,
        "official_data_as_of": "2022-12-31",
        "sources": [
            {
                "name": "国土数値情報 高速道路時系列データ N06",
                "url": N06_ZIP_URL,
                "role": "IC・SIC・JCTの公式名称、種別、代表座標",
            },
            {
                "name": "OpenStreetMap",
                "role": "2022年以降の施設およびSA・PA等の補完",
            },
        ],
        "routes": {},
    }
    for r in routes:
        key, slug = r["key"], r["slug"]
        bundle["routes"].append(key)

        chains = build_chains_for_route(slug)
        bundle["chains"][key] = chains

        fpath = os.path.join(PUBLIC_DATA_DIR, f"facilities-{slug}.geojson")
        merged = {}
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                ffc = json.load(f)
            for feat in ffc["features"]:
                lon, lat = feat["geometry"]["coordinates"]
                props = feat.get("properties", {})
                kind = _infer_kind(props.get("name"), props.get("kind"))
                name = _canonical_name(props.get("name"), kind)
                if not name:
                    continue
                merged[name] = {
                    "name": name,
                    "kind": kind,
                    "coordinates": [float(lon), float(lat)],
                    "source": "OpenStreetMap",
                    "source_id": None,
                    "opened": None,
                    "aliases": _facility_aliases(name, kind),
                }

        # Official points take precedence when the same canonical name exists,
        # and supplement facilities that the OSM-only bbox query missed.
        bbox = _route_bbox(chains)
        for joint in official_joints:
            lon, lat = joint["coordinates"]
            if not _inside_bbox((lon, lat), bbox):
                continue
            positions = _nearest_position(chains, lon, lat, OFFICIAL_SNAP_MAX_M)
            if positions:
                merged[joint["name"]] = dict(joint)

        facility_records = []
        facility_lookup = {}
        for name, item in sorted(merged.items()):
            lon, lat = item["coordinates"]
            positions = _nearest_position(chains, lon, lat, OFFICIAL_SNAP_MAX_M)
            if not positions:
                # Keep the old bundle contract conservative: a filename can
                # only resolve when the facility actually lands on this route.
                continue
            aliases = sorted(set(item.get("aliases", [])) | {name})
            record = {
                "id": _facility_id(slug, name),
                "name": name,
                "kind": item["kind"],
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "source": item["source"],
                "source_id": item.get("source_id"),
                "opened": item.get("opened"),
                "aliases": aliases,
                "positions": positions,
            }
            facility_records.append(record)
            for alias in aliases:
                facility_lookup[alias] = [record["lon"], record["lat"]]
        bundle["facilities"][key] = facility_lookup

        filename_facilities = [
            facility for facility in facility_records
            if _is_filename_facility(facility["name"], facility["kind"])
        ]

        sequences = {}
        adjacent_segments = {}
        for direction, direction_chains in chains.items():
            sequences[direction] = []
            adjacent_segments[direction] = []
            for chain_index, _chain in enumerate(direction_chains):
                ordered = []
                for facility in filename_facilities:
                    candidates = [
                        pos for pos in facility["positions"].get(direction, [])
                        if pos["chain"] == chain_index
                    ]
                    if candidates:
                        best = min(candidates, key=lambda pos: pos["snap_distance_m"])
                        ordered.append((best["chainage_m"], facility["name"], facility["kind"]))
                ordered.sort()
                names = [item[1] for item in ordered]
                if names:
                    sequences[direction].append(names)
                for start, end in zip(ordered, ordered[1:]):
                    adjacent_segments[direction].append({
                        "from": start[1],
                        "to": end[1],
                        "label": f"{start[1]}→{end[1]}",
                        "suggested_filename": f"{key}_{start[1]}→{end[1]}.csv",
                        "chain": chain_index,
                        "start_chainage_m": start[0],
                        "end_chainage_m": end[0],
                    })

        definitions["routes"][key] = {
            "slug": slug,
            "facilities": filename_facilities,
            "sequences": sequences,
            "adjacent_segments": adjacent_segments,
        }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, separators=(",", ":"))
    with open(DEFINITIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(definitions, f, ensure_ascii=False, indent=2)

    size_mb = os.path.getsize(OUT_PATH) / 1_000_000
    print(f"wrote {OUT_PATH} ({size_mb:.2f} MB), {len(bundle['routes'])} routes")
    total_facilities = sum(len(route["facilities"]) for route in definitions["routes"].values())
    total_segments = sum(
        len(items)
        for route in definitions["routes"].values()
        for items in route["adjacent_segments"].values()
    )
    print(
        f"wrote {DEFINITIONS_PATH} with {total_facilities} route-facility records "
        f"and {total_segments} directed adjacent segments"
    )


if __name__ == "__main__":
    main()

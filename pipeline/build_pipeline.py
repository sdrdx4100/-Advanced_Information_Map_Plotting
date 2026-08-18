"""Main pipeline: OSM raw ways -> stitched per-direction centerlines -> 25m
resampling -> GSI DEM elevation -> tunnel/bridge-aware noise handling ->
100m-window gradient -> GeoJSON + QC report.

See README.md in this folder for the full write-up of method and caveats.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict

import numpy as np
from pyproj import Geod

sys.path.insert(0, os.path.dirname(__file__))
from gsi_dem import sample_elevation  # noqa: E402
from fetch_osm import ROUTE_DEFS, fetch_facilities  # noqa: E402

GEOD = Geod(ellps="WGS84")
HERE = os.path.dirname(__file__)
RAW_DIR = os.path.join(HERE, "data", "raw")
OUT_DIR = os.path.join(HERE, "data", "output")
os.makedirs(OUT_DIR, exist_ok=True)

SAMPLE_INTERVAL_M = 25.0
GRADE_WINDOW_M = float(os.environ.get("GRADE_WINDOW_M", 100.0))
GRADE_WINDOW_STEPS = round(GRADE_WINDOW_M / SAMPLE_INTERVAL_M)
OUT_SUFFIX = f"-{int(GRADE_WINDOW_M)}"
SMOOTH_WINDOW_PTS = 5  # ~125m rolling median for noise reduction before interpolation
ANOMALY_THRESHOLD_PCT = 7.0  # flag, don't silently drop
DESPIKE_WINDOW_PTS = 9  # ~225m window for outlier detection (Hampel-style)
DESPIKE_ABS_FLOOR_M = 8.0  # never flag a deviation smaller than this as a spike
DESPIKE_MAD_MULT = 5.0  # or this many MADs above the floor, whichever is larger
PORTAL_BUFFER_M = 200.0  # normal-tagged points this close to a tunnel/bridge are
# excluded as elevation anchors too: ground rises fast right at a portal cut or
# an embankment approach, and that climb is real but not the road's own profile.

# Reference point to establish 上り(toward Tokyo)/下り(away) direction: for any
# route that's part of Japan's Tokyo-centered expressway network (which is
# effectively all of them, even ones nowhere near Gotemba), geodesic distance
# to a fixed Tokyo-ward point monotonically increases as you move away from
# Tokyo along the route, so one reference point works network-wide.
TOKYO_WARD_REF = (138.9407, 35.2861)  # Gotemba JCT

ROUTES = list(ROUTE_DEFS.keys())

FACILITY_KIND_PATTERNS = [
    ("JCT", ["JCT", "ジャンクション"]),
    ("SIC", ["スマートIC", "SIC"]),
    ("IC", ["IC", "インターチェンジ"]),
    ("SA", ["SA", "サービスエリア"]),
    ("PA", ["PA", "パーキングエリア"]),
]


def geod_dist(lon1, lat1, lon2, lat2):
    _, _, d = GEOD.inv(lon1, lat1, lon2, lat2)
    return d


def load_ways(name):
    with open(os.path.join(RAW_DIR, f"ways_{ROUTE_DEFS[name]['slug']}.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    ways = []
    for el in data["elements"]:
        if el.get("type") != "way" or "geometry" not in el or None in [
            pt for pt in el["geometry"] if pt is None
        ]:
            continue
        geom = [(p["lon"], p["lat"]) for p in el["geometry"] if p]
        if len(geom) < 2 or len(el.get("nodes", [])) != len(el["geometry"]):
            continue
        tags = el.get("tags", {})
        structure = "tunnel" if tags.get("tunnel") else "bridge" if tags.get("bridge") else "normal"
        structure_name = tags.get("tunnel:name") or tags.get("bridge:name")
        d_start = geod_dist(geom[0][0], geom[0][1], *TOKYO_WARD_REF)
        d_end = geod_dist(geom[-1][0], geom[-1][1], *TOKYO_WARD_REF)
        # oneway traffic flows node[0] -> node[-1] by OSM convention; heading toward
        # Gotemba (Tokyo side) = 上り, heading away = 下り.
        direction = "上り" if d_end < d_start else "下り"
        ways.append(
            {
                "id": el["id"],
                "nodes": el["nodes"],
                "geom": geom,
                "structure": structure,
                "structure_name": structure_name,
                "direction": direction,
            }
        )
    return ways


def stitch_chains(ways):
    """Group ways into maximal connected polylines by shared endpoint node ids.
    Returns list of chains; each chain is a list of point dicts with lon/lat/
    structure/structure_name carried from the source way."""
    endpoint_map = defaultdict(list)
    for i, w in enumerate(ways):
        endpoint_map[w["nodes"][0]].append(i)
        endpoint_map[w["nodes"][-1]].append(i)

    used = [False] * len(ways)
    chains = []

    def pt_list(way, points):
        return [
            {
                "lon": lon,
                "lat": lat,
                "structure": way["structure"],
                "structure_name": way["structure_name"],
            }
            for (lon, lat) in points
        ]

    for i in range(len(ways)):
        if used[i]:
            continue
        used[i] = True
        chain_nodes = list(ways[i]["nodes"])
        chain_pts = pt_list(ways[i], ways[i]["geom"])

        changed = True
        while changed:
            changed = False
            last_node = chain_nodes[-1]
            for j in endpoint_map[last_node]:
                if used[j]:
                    continue
                wj = ways[j]
                if wj["nodes"][0] == last_node:
                    chain_nodes += wj["nodes"][1:]
                    chain_pts += pt_list(wj, wj["geom"][1:])
                elif wj["nodes"][-1] == last_node:
                    chain_nodes += list(reversed(wj["nodes"]))[1:]
                    chain_pts += pt_list(wj, list(reversed(wj["geom"]))[1:])
                else:
                    continue
                used[j] = True
                changed = True
                break

        changed = True
        while changed:
            changed = False
            first_node = chain_nodes[0]
            for j in endpoint_map[first_node]:
                if used[j]:
                    continue
                wj = ways[j]
                if wj["nodes"][-1] == first_node:
                    chain_nodes = wj["nodes"][:-1] + chain_nodes
                    chain_pts = pt_list(wj, wj["geom"][:-1]) + chain_pts
                elif wj["nodes"][0] == first_node:
                    chain_nodes = list(reversed(wj["nodes"]))[:-1] + chain_nodes
                    chain_pts = pt_list(wj, list(reversed(wj["geom"]))[:-1]) + chain_pts
                else:
                    continue
                used[j] = True
                changed = True
                break

        chains.append(chain_pts)
    return chains


def resample_25m(chain_pts):
    """Resample a chain of points (each with lon/lat/structure) at fixed
    25m geodesic arc-length intervals. Returns list of dicts with lon, lat,
    structure, structure_name, cum_dist."""
    coords = [(p["lon"], p["lat"]) for p in chain_pts]
    seg_dist = [0.0]
    for k in range(1, len(coords)):
        seg_dist.append(geod_dist(*coords[k - 1], *coords[k]))
    cum = np.cumsum(seg_dist)
    total = cum[-1]
    if total < SAMPLE_INTERVAL_M:
        return []

    targets = np.arange(0.0, total, SAMPLE_INTERVAL_M)
    out = []
    seg_idx = 0
    for d in targets:
        while seg_idx < len(cum) - 2 and cum[seg_idx + 1] < d:
            seg_idx += 1
        c0, c1 = cum[seg_idx], cum[seg_idx + 1]
        t = 0.0 if c1 == c0 else (d - c0) / (c1 - c0)
        lon = coords[seg_idx][0] + t * (coords[seg_idx + 1][0] - coords[seg_idx][0])
        lat = coords[seg_idx][1] + t * (coords[seg_idx + 1][1] - coords[seg_idx][1])
        src = chain_pts[seg_idx]
        out.append(
            {
                "lon": lon,
                "lat": lat,
                "structure": src["structure"],
                "structure_name": src["structure_name"],
                "cum_dist": float(d),
            }
        )
    return out


def despike(values, window=DESPIKE_WINDOW_PTS):
    """Hampel-style outlier rejection on a sequence that may contain None
    gaps (non-'normal' points). A road on a steep natural slope (e.g. narrow
    coastal shelves like Yui/Okitsu) can sit only a few meters from terrain
    that rises or drops tens of meters — exactly the case the DEM-vs-road
    divergence warning in the spec calls out, and it isn't limited to
    OSM-tagged bridge/tunnel ways. Points that jump far outside their local
    neighborhood are treated as terrain contamination and dropped (None),
    to be filled by the same interpolation used for tunnel/bridge gaps."""
    n = len(values)
    out = list(values)
    half = window // 2
    for i in range(n):
        if values[i] is None:
            continue
        lo, hi = max(0, i - half), min(n, i + half + 1)
        neighborhood = [values[k] for k in range(lo, hi) if k != i and values[k] is not None]
        if len(neighborhood) < 3:
            continue
        med = float(np.median(neighborhood))
        mad = float(np.median(np.abs(np.array(neighborhood) - med)))
        threshold = max(DESPIKE_ABS_FLOOR_M, DESPIKE_MAD_MULT * mad * 1.4826)
        if abs(values[i] - med) > threshold:
            out[i] = None
    return out


def rolling_median(values, window):
    n = len(values)
    out = [None] * n
    half = window // 2
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        seg = [v for v in values[lo:hi] if v is not None]
        if seg:
            out[i] = float(np.median(seg))
    return out


def attach_elevation(points):
    """Mutates points in place: adds 'elevation_raw', 'dem_dataset', then
    smooths 'normal' points and interpolates tunnel/bridge/missing points,
    filling 'elevation' (final) and 'quality' ('measured'/'estimated')."""
    for p in points:
        if p["structure"] == "normal":
            elev, dataset = sample_elevation(p["lon"], p["lat"])
            p["elevation_raw"] = elev
            p["dem_dataset"] = dataset
        else:
            p["elevation_raw"] = None
            p["dem_dataset"] = None

    buf_pts = max(1, round(PORTAL_BUFFER_M / SAMPLE_INTERVAL_M))
    is_structure = [p["structure"] != "normal" for p in points]
    near_structure = [
        any(is_structure[max(0, i - buf_pts) : min(len(points), i + buf_pts + 1)])
        for i in range(len(points))
    ]

    raw_normal = [
        p["elevation_raw"] if (p["structure"] == "normal" and not near_structure[i]) else None
        for i, p in enumerate(points)
    ]
    despiked = despike(raw_normal)
    n_despiked = sum(1 for a, b in zip(raw_normal, despiked) if a is not None and b is None)
    smoothed = rolling_median(despiked, SMOOTH_WINDOW_PTS)

    known_idx = [i for i, v in enumerate(smoothed) if v is not None]
    for i, p in enumerate(points):
        if smoothed[i] is not None:
            p["elevation"] = smoothed[i]
            p["quality"] = "measured"
        else:
            p["elevation"] = None
            p["quality"] = "estimated"

    if not known_idx:
        for p in points:
            p["elevation"] = 0.0
        return n_despiked

    for i, p in enumerate(points):
        if p["elevation"] is not None:
            continue
        before = max([k for k in known_idx if k <= i], default=None)
        after = min([k for k in known_idx if k >= i], default=None)
        if before is None:
            p["elevation"] = smoothed[after]
        elif after is None:
            p["elevation"] = smoothed[before]
        elif before == after:
            p["elevation"] = smoothed[before]
        else:
            d0, d1 = points[before]["cum_dist"], points[after]["cum_dist"]
            t = 0.0 if d1 == d0 else (p["cum_dist"] - d0) / (d1 - d0)
            p["elevation"] = smoothed[before] + t * (smoothed[after] - smoothed[before])
    return n_despiked


def classify_facility(name):
    for kind, patterns in FACILITY_KIND_PATTERNS:
        if any(pat in name for pat in patterns):
            return kind
    return "OTHER"


def load_facilities(bbox):
    data = fetch_facilities(bbox)  # cache-only in practice; fetch_osm.py pre-populates it
    raw = []
    for el in data["elements"]:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        if el["type"] == "node":
            lon, lat = el["lon"], el["lat"]
        else:
            c = el.get("center")
            if not c:
                continue
            lon, lat = c["lon"], c["lat"]
        direction = None
        clean = name
        for d in ("（上り）", "(上り)"):
            if d in clean:
                direction = "上り"
                clean = clean.replace(d, "")
        for d in ("（下り）", "(下り)"):
            if d in clean:
                direction = "下り"
                clean = clean.replace(d, "")
        raw.append(
            {
                "name": clean.strip(),
                "direction": direction,
                "kind": classify_facility(name),
                "lon": lon,
                "lat": lat,
            }
        )

    # dedupe by (clean name, kind): average coordinates of duplicates
    groups = defaultdict(list)
    for f in raw:
        groups[(f["name"], f["kind"])].append(f)
    out = []
    for (name, kind), items in groups.items():
        out.append(
            {
                "name": name,
                "kind": kind,
                "lon": float(np.mean([i["lon"] for i in items])),
                "lat": float(np.mean([i["lat"] for i in items])),
            }
        )
    return out


def assign_facility_chainage(points, facilities, max_snap_m=250.0):
    """For each facility, find nearest resampled point on this chain (by
    coordinate) and record its cum_dist if within max_snap_m."""
    assigned = []
    if not points:
        return assigned
    lons = np.array([p["lon"] for p in points])
    lats = np.array([p["lat"] for p in points])
    for f in facilities:
        # cheap planar pre-filter then exact geod distance on the closest few
        d2 = (lons - f["lon"]) ** 2 + (lats - f["lat"]) ** 2
        idx = int(np.argmin(d2))
        dist = geod_dist(points[idx]["lon"], points[idx]["lat"], f["lon"], f["lat"])
        if dist <= max_snap_m:
            assigned.append({"name": f["name"], "kind": f["kind"], "cum_dist": points[idx]["cum_dist"]})
    assigned.sort(key=lambda a: a["cum_dist"])
    return assigned


def from_to_at(assigned, d):
    prev_name, next_name = None, None
    for a in assigned:
        if a["cum_dist"] <= d:
            prev_name = a["name"]
        elif next_name is None:
            next_name = a["name"]
            break
    return prev_name, next_name


def build_segments(route, direction, points, assigned_facilities, chain_rank):
    """Bucket resampled points into GRADE_WINDOW_M (100m) segments and
    compute signed gradient using the endpoints of each bucket. chain_rank=0
    marks the largest (main) connected chain for this route+direction, used
    by the frontend to pick which chain feeds the corridor profile chart."""
    segments = []
    n = len(points)
    step = GRADE_WINDOW_STEPS
    seg_id = 0
    for i in range(0, n - step, step):
        p0, p1 = points[i], points[i + step]
        horiz = p1["cum_dist"] - p0["cum_dist"]
        if horiz <= 0:
            continue
        grade = (p1["elevation"] - p0["elevation"]) / horiz * 100.0
        bucket = points[i : i + step + 1]
        structures = {b["structure"] for b in bucket}
        if "tunnel" in structures:
            structure = "tunnel"
        elif "bridge" in structures:
            structure = "bridge"
        else:
            structure = "normal"
        quality = "measured" if all(b["quality"] == "measured" for b in bucket) else "estimated"
        mid = (p0["cum_dist"] + p1["cum_dist"]) / 2.0
        frm, to = from_to_at(assigned_facilities, mid)
        anomaly = abs(grade) > ANOMALY_THRESHOLD_PCT
        if anomaly:
            # Physically implausible for a limited-access expressway even after
            # despiking/portal-buffering; don't present it with false confidence.
            quality = "estimated"
        segments.append(
            {
                "id": f"{route}-{direction}-{chain_rank}-{seg_id}",
                "route": route,
                "direction": direction,
                "from": frm,
                "to": to,
                "grade": round(grade, 2),
                "elevation_start": round(p0["elevation"], 1),
                "elevation_end": round(p1["elevation"], 1),
                "quality": quality,
                "structure": structure,
                "chain_rank": chain_rank,
                "cum_dist_start": round(p0["cum_dist"], 1),
                "cum_dist_end": round(p1["cum_dist"], 1),
                "anomaly": anomaly,
                "coordinates": [[b["lon"], b["lat"]] for b in bucket],
            }
        )
        seg_id += 1
    return segments


def main():
    all_segments = []
    all_points_by_key = {}
    matched_facility_names_by_route = defaultdict(set)
    qc = {"routes": {}, "anomalies": []}

    fac_cache = {}

    for route in ROUTES:
        ways = load_ways(route)
        by_dir = defaultdict(list)
        for w in ways:
            by_dir[w["direction"]].append(w)

        bbox = ROUTE_DEFS[route]["bbox"]
        if bbox not in fac_cache:
            fac_cache[bbox] = load_facilities(bbox)
        facilities = fac_cache[bbox]

        for direction, dir_ways in by_dir.items():
            chains = stitch_chains(dir_ways)
            chains.sort(key=len, reverse=True)
            print(f"[{route}/{direction}] {len(dir_ways)} ways -> {len(chains)} chain(s), "
                  f"largest {len(chains[0])} pts", flush=True)

            chain_all_segments = []
            grades_this_dir = []
            chain_rank = 0
            for chain_i, chain in enumerate(chains):
                if len(chain) < 5:
                    continue
                pts = resample_25m(chain)
                if len(pts) < GRADE_WINDOW_STEPS + 1:
                    continue
                print(f"  chain {chain_i}: {len(pts)} resampled pts, fetching elevation...", flush=True)
                n_despiked = attach_elevation(pts)
                qc.setdefault("despiked_points", 0)
                qc["despiked_points"] += n_despiked
                assigned = assign_facility_chainage(pts, facilities)
                matched_facility_names_by_route[route].update((a["name"], a["kind"]) for a in assigned)
                segs = build_segments(route, direction, pts, assigned, chain_rank)
                chain_all_segments.extend(segs)
                grades_this_dir.extend([s["grade"] for s in segs])
                all_points_by_key[(route, direction, chain_rank)] = pts
                chain_rank += 1

            all_segments.extend(chain_all_segments)

            if grades_this_dir:
                qc["routes"].setdefault(route, {})[direction] = {
                    "segment_count": len(grades_this_dir),
                    "max_grade": round(max(grades_this_dir, key=abs), 2),
                    "max_abs_grade": round(max(abs(g) for g in grades_this_dir), 2),
                    "mean_abs_grade": round(float(np.mean(np.abs(grades_this_dir))), 2),
                }
            for s in chain_all_segments:
                if s["anomaly"]:
                    qc["anomalies"].append(
                        {"id": s["id"], "grade": s["grade"], "structure": s["structure"],
                         "from": s["from"], "to": s["to"]}
                    )

    # known published reference values (NEXCO) for sanity comparison, where we
    # have one; routes without a citation just get the computed figures.
    published_refs = {"東名": "4%級", "新東名": "静岡区間で約2%"}
    qc["reference_check"] = {
        route: {"published_max_grade_pct": published_refs.get(route, "未確認"), "computed": qc["routes"].get(route, {})}
        for route in ROUTES
    }

    # Segments and facilities are written per route (not one combined file) so
    # the frontend can fetch only the routes the user has selected instead of
    # downloading the whole network on first load.
    segments_by_route = defaultdict(list)
    for s in all_segments:
        segments_by_route[s["route"]].append(s)

    for route in ROUTES:
        segments_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {k: v for k, v in s.items() if k != "coordinates"},
                    "geometry": {"type": "LineString", "coordinates": s["coordinates"]},
                }
                for s in segments_by_route.get(route, [])
            ],
        }
        slug = ROUTE_DEFS[route]["slug"]
        with open(os.path.join(OUT_DIR, f"road-segments-{slug}{OUT_SUFFIX}.geojson"), "w", encoding="utf-8") as f:
            json.dump(segments_fc, f, ensure_ascii=False)

        # Only keep facilities that actually snapped onto this route's chains
        # (assign_facility_chainage checked real proximity to resampled road
        # points); the raw bbox query also picks up junctions on unrelated
        # roads (e.g. the Chuo expressway near Iida) inside the same box.
        bbox = ROUTE_DEFS[route]["bbox"]
        route_facilities = [
            f for f in fac_cache[bbox] if (f["name"], f["kind"]) in matched_facility_names_by_route[route]
        ]
        facilities_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"name": f["name"], "kind": f["kind"]},
                    "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]},
                }
                for f in route_facilities
            ],
        }
        with open(os.path.join(OUT_DIR, f"facilities-{slug}.geojson"), "w", encoding="utf-8") as f:
            json.dump(facilities_fc, f, ensure_ascii=False)

    with open(os.path.join(OUT_DIR, f"qc_report{OUT_SUFFIX}.json"), "w", encoding="utf-8") as f:
        json.dump(qc, f, ensure_ascii=False, indent=2)

    # Corridor profile (elevation + grade vs. distance) for the main chain of
    # each route+direction, used by the frontend's bottom profile panel.
    profiles_by_route = defaultdict(dict)
    for (route, direction, chain_rank), pts in all_points_by_key.items():
        if chain_rank != 0:
            continue
        grades = []
        step = GRADE_WINDOW_STEPS
        for i in range(0, len(pts) - step, step):
            p0, p1 = pts[i], pts[i + step]
            horiz = p1["cum_dist"] - p0["cum_dist"]
            grades.append(round((p1["elevation"] - p0["elevation"]) / horiz * 100.0, 2) if horiz > 0 else 0.0)
        elevations = [round(p["elevation"], 1) for p in pts]
        ascent = float(np.sum(np.diff(elevations).clip(min=0)))
        profiles_by_route[route][f"{route}_{direction}"] = {
            "route": route,
            "direction": direction,
            "length_km": round(pts[-1]["cum_dist"] / 1000.0, 1),
            "elevation_m": elevations,
            "grade_pct": grades,
            "max_elevation_m": round(max(elevations), 1),
            "min_elevation_m": round(min(elevations), 1),
            "total_ascent_m": round(ascent, 0),
            "max_abs_grade_pct": round(
                max((abs(g) for g in grades if abs(g) <= ANOMALY_THRESHOLD_PCT), default=0.0), 2
            ),
        }
    for route in ROUTES:
        slug = ROUTE_DEFS[route]["slug"]
        with open(os.path.join(OUT_DIR, f"profiles-{slug}{OUT_SUFFIX}.json"), "w", encoding="utf-8") as f:
            json.dump(profiles_by_route.get(route, {}), f, ensure_ascii=False)

    # Route manifest: tells the frontend what's fetchable (and under what
    # filename slug/color) without hardcoding route names at build time.
    with open(os.path.join(OUT_DIR, "routes.json"), "w", encoding="utf-8") as f:
        json.dump(
            [
                {
                    "key": route,
                    "slug": ROUTE_DEFS[route]["slug"],
                    "color": ROUTE_DEFS[route].get("color", "#297a58"),
                    "default": ROUTE_DEFS[route].get("default", False),
                }
                for route in ROUTES
            ],
            f, ensure_ascii=False, indent=2,
        )

    print(f"\nTotal segments: {len(all_segments)}")
    for route in ROUTES:
        print(f"  {route}: {len(segments_by_route.get(route, []))} segments, "
              f"{len(matched_facility_names_by_route[route])} facilities")
    print(f"Anomalies (>|{ANOMALY_THRESHOLD_PCT}%|): {len(qc['anomalies'])}")
    print("QC summary:", json.dumps(qc["routes"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

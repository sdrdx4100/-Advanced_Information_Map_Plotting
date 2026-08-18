"""Fetch expressway geometry and facility points from OpenStreetMap via the
Overpass API. Used as the road-centerline source (auxiliary/primary geometry
source per the requirements doc) since GSI's height-attributed centerline does
not have confirmed national/complete coverage for these corridors.

Caches raw Overpass responses to data/raw/ so repeated pipeline runs don't hammer
the public API.

Routes are matched by OSM `name` (not `ref`): several expressways share a route
ref with their "sister" road (東名/名神 are both ref=E1, 新東名/新名神 are both
ref=E1A), so ref alone can't tell them apart.

Registering a new route only requires its OSM `name` — no manual bbox. Ways are
fetched name-only (no bbox filter), and the bbox used for the (necessarily
spatial) facilities query is derived from the fetched geometry and cached
alongside it. This is what makes adding routes cheap at 10-20+ scale: no one
has to eyeball a bounding box per route.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time

import requests

HEADERS = {
    "User-Agent": "road-gradient-map-prototype/0.1 (research; contact: sdrdx3xd3@gmail.com)"
}
# Nationwide name-only queries (no bbox) run much longer server-side than the
# bbox-restricted queries this pipeline used to make, and the main instance
# 504s on them under everyday load. Try mirrors in order rather than hammering
# one host with retries alone; maps.mail.ru answered in ~15s in testing when
# overpass-api.de/kumi.systems/private.coffee all 504'd on the same query.
OVERPASS_URLS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

RAW_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

BBOX_PAD_DEG = 0.05  # ~5km padding around a route's own mapped extent


def auto_color(slug: str) -> str:
    """Deterministic, reasonably distinct color per route so nobody has to
    hand-pick hex codes for 10-20+ entries. Fixed sat/lightness for a
    consistent, non-garish palette; hue comes from a hash of the slug."""
    hue = int(hashlib.sha1(slug.encode("utf-8")).hexdigest(), 16) % 360
    sat, light = 45, 38  # matches the muted-but-legible feel of the original hand-picked colors
    import colorsys

    r, g, b = colorsys.hls_to_rgb(hue / 360, light / 100, sat / 100)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


# key: internal route name (also used in output filenames via "slug")
# osm_name: exact OSM `name` tag to match (no bbox needed; see module docstring)
# group: display grouping in the frontend route picker (operator is a rough
#   proxy since exact jurisdiction boundaries mid-route aren't worth encoding)
# color / bbox: optional manual overrides; auto-generated/derived if omitted
ROUTE_DEFS = {
    "東名": {"slug": "tomei", "osm_name": "東名高速道路", "group": "NEXCO中日本", "default": True},
    "新東名": {"slug": "shin-tomei", "osm_name": "新東名高速道路", "group": "NEXCO中日本", "default": True},
    "新名神": {"slug": "shin-meishin", "osm_name": "新名神高速道路", "group": "NEXCO西日本", "default": False},
    # 北関東・信越 (NEXCO東日本)
    "東北道": {"slug": "tohoku", "osm_name": "東北自動車道", "group": "NEXCO東日本・北関東/信越", "default": False},
    "常磐道": {"slug": "joban", "osm_name": "常磐自動車道", "group": "NEXCO東日本・北関東/信越", "default": False},
    "関越道": {"slug": "kanetsu", "osm_name": "関越自動車道", "group": "NEXCO東日本・北関東/信越", "default": False},
    "北関東道": {"slug": "kita-kanto", "osm_name": "北関東自動車道", "group": "NEXCO東日本・北関東/信越", "default": False},
    "上信越道": {"slug": "joshinetsu", "osm_name": "上信越自動車道", "group": "NEXCO東日本・北関東/信越", "default": False},
    # 南関東 (NEXCO東日本)
    "東関東道": {"slug": "higashi-kanto", "osm_name": "東関東自動車道", "group": "NEXCO東日本・関東", "default": False},
    # 環状路線: TOKYO_WARD_REFへの距離だけでは上り/下りが安定しない可能性がある
    # （build_pipeline.pyのTOKYO_WARD_REF周りのコメント参照）
    "圏央道": {"slug": "ken-o", "osm_name": "首都圏中央連絡自動車道", "group": "NEXCO東日本・関東", "default": False},
    "東京外環道": {"slug": "tokyo-gaikan", "osm_name": "東京外環自動車道", "group": "NEXCO東日本・関東", "default": False},
    "京葉道路": {"slug": "keiyo", "osm_name": "京葉道路", "group": "NEXCO東日本・関東", "default": False},
    "館山道": {"slug": "tateyama", "osm_name": "館山自動車道", "group": "NEXCO東日本・関東", "default": False},
    "千葉東金道路": {"slug": "chiba-togane", "osm_name": "千葉東金道路", "group": "NEXCO東日本・関東", "default": False},
    "東京湾アクアライン": {
        "slug": "aqua-line",
        "osm_name": "東京湾アクアライン",
        "osm_names": [
            "東京湾アクアライン;東京湾横断・木更津東金道路",
            "東京湾アクアライン連絡道",
            "東京湾アクアライン連絡道;東京湾横断・木更津東金道路",
        ],
        "group": "NEXCO東日本・関東",
        "default": False,
    },
    "横浜横須賀道路": {"slug": "yokohama-yokosuka", "osm_name": "横浜横須賀道路", "group": "NEXCO東日本・関東", "default": False},
    "富津館山道路": {"slug": "futtsu-tateyama", "osm_name": "富津館山道路", "group": "NEXCO東日本・関東", "default": False},
    # 東海 (NEXCO中日本)
    "中央道": {"slug": "chuo", "osm_name": "中央自動車道", "group": "NEXCO中日本", "default": False},
    "伊勢湾岸道": {"slug": "isewangan", "osm_name": "伊勢湾岸自動車道", "group": "NEXCO中日本", "default": False},
    "東海北陸道": {"slug": "tokai-hokuriku", "osm_name": "東海北陸自動車道", "group": "NEXCO中日本", "default": False},
    # 関西 (NEXCO西日本)
    "名神": {"slug": "meishin", "osm_name": "名神高速道路", "group": "NEXCO西日本", "default": False},
    "近畿道": {"slug": "kinki", "osm_name": "近畿自動車道", "group": "NEXCO西日本", "default": False},
}

for _key, _def in ROUTE_DEFS.items():
    _def.setdefault("color", auto_color(_def["slug"]))


def _query(q: str, rounds: int = 3) -> dict:
    # The public Overpass instance is shared infrastructure: nationwide
    # name-only queries (no bbox) run much longer server-side than the
    # bbox-restricted queries this pipeline used to make, so 504s/429s under
    # load are expected, not exceptional. Cycle mirrors before backing off —
    # a 504 usually means "this host is loaded right now", not "try again
    # later on the same host".
    last_exc = None
    for round_i in range(rounds):
        for url in OVERPASS_URLS:
            try:
                r = requests.post(url, data={"data": q}, headers=HEADERS, timeout=180)
                if r.status_code in (429, 504, 503):
                    raise requests.exceptions.HTTPError(f"{r.status_code} from {url}", response=r)
                r.raise_for_status()
                return r.json()
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                print(f"  {url} failed ({exc})", flush=True)
        if round_i < rounds - 1:
            wait = 20 * (round_i + 1)
            print(f"  all mirrors failed this round; retrying in {wait}s "
                  f"(round {round_i + 2}/{rounds})", flush=True)
            time.sleep(wait)
    raise last_exc


def _cached(name: str, query: str) -> dict:
    path = os.path.join(RAW_DIR, name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        # A previous exact-name lookup may legitimately return zero elements
        # when OSM uses a composite route name. Do not make that failed lookup
        # permanent after the route definition is corrected.
        if cached.get("elements"):
            return cached
    data = _query(query)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


def fetch_ways(route_key: str) -> dict:
    route_def = ROUTE_DEFS[route_key]
    osm_names = route_def.get("osm_names", [route_def["osm_name"]])
    slug = ROUTE_DEFS[route_key]["slug"]
    selectors = "\n".join(
        f'      way["highway"="motorway"]["name"="{name}"];' for name in osm_names
    )
    q = f"""
    [out:json][timeout:300];
    (
{selectors}
    );
    out geom;
    """
    return _cached(f"ways_{slug}.json", q)


def derive_bbox(ways_data: dict) -> tuple[float, float, float, float]:
    lons, lats = [], []
    for el in ways_data["elements"]:
        for pt in el.get("geometry", []) or []:
            if pt:
                lons.append(pt["lon"])
                lats.append(pt["lat"])
    if not lons:
        raise ValueError("no geometry to derive a bbox from")
    return (
        min(lats) - BBOX_PAD_DEG,
        min(lons) - BBOX_PAD_DEG,
        max(lats) + BBOX_PAD_DEG,
        max(lons) + BBOX_PAD_DEG,
    )


def route_bbox(route_key: str) -> tuple[float, float, float, float]:
    """Bbox for route_key, from a manual override if set, else derived from
    its already-fetched ways and cached to disk (so build_pipeline.py doesn't
    need to re-derive it or re-import Overpass-fetching code)."""
    manual = ROUTE_DEFS[route_key].get("bbox")
    if manual:
        return tuple(manual)
    slug = ROUTE_DEFS[route_key]["slug"]
    cache_path = os.path.join(RAW_DIR, f"bbox_{slug}.json")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return tuple(json.load(f))
    bbox = derive_bbox(fetch_ways(route_key))
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(bbox, f)
    return bbox


TILE_MAX_DEG = 1.0  # ~110km; a route bbox larger than this in either axis gets
# split into tiles before querying facilities. An untiled query over, say,
# 東名's full Tokyo-Nagoya bbox pulls every motorway_junction/services/
# rest_area in that whole span (from every crossing highway, not just this
# one) in a single request, which routinely 504s; per-tile queries stay small
# and fast, and the extra-route noise gets filtered out later anyway by
# build_pipeline.assign_facility_chainage (only nodes that actually snap
# within 250m of this route's own centerline survive into its output).


def _tile_bbox(bbox, max_deg=TILE_MAX_DEG):
    s, w, n, e = bbox
    lat_tiles = max(1, math.ceil((n - s) / max_deg))
    lon_tiles = max(1, math.ceil((e - w) / max_deg))
    lat_step, lon_step = (n - s) / lat_tiles, (e - w) / lon_tiles
    return [
        (s + i * lat_step, w + j * lon_step, s + (i + 1) * lat_step, w + (j + 1) * lon_step)
        for i in range(lat_tiles)
        for j in range(lon_tiles)
    ]


def fetch_facilities(bbox: tuple[float, float, float, float]) -> dict:
    s, w, n, e = bbox
    key = f"facilities_{s:.3f}_{w:.3f}_{n:.3f}_{e:.3f}.json"
    cache_path = os.path.join(RAW_DIR, key)
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    tiles = _tile_bbox(bbox)
    seen_ids = set()
    elements = []
    failed_tiles = []
    for i, tile in enumerate(tiles):
        ts, tw, tn, te = tile
        q = f"""
        [out:json][timeout:120];
        (
          node["highway"="motorway_junction"]({ts},{tw},{tn},{te});
          nwr["highway"="services"]({ts},{tw},{tn},{te});
          nwr["highway"="rest_area"]({ts},{tw},{tn},{te});
        );
        out center;
        """
        print(f"  facilities tile {i + 1}/{len(tiles)} {tile}", flush=True)
        try:
            data = _query(q)
        except requests.exceptions.RequestException as exc:
            # A single stubborn tile (all mirrors, all rounds) shouldn't sink
            # a multi-hour, many-route batch run: that tile's IC/JCT/SA/PA
            # facilities are missing for this route, but the road geometry
            # and gradient calculation (the actual point of this pipeline)
            # are untouched. Surfaced in the return value so it's visible
            # rather than silently swallowed.
            print(f"  tile {i + 1}/{len(tiles)} permanently failed, skipping ({exc})", flush=True)
            failed_tiles.append(tile)
            continue
        for el in data["elements"]:
            eid = (el["type"], el["id"])
            if eid not in seen_ids:
                seen_ids.add(eid)
                elements.append(el)

    result = {"elements": elements, "failed_tiles": failed_tiles}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def main():
    seen_bboxes = set()
    for key in ROUTE_DEFS:
        data = fetch_ways(key)
        print(f"{key}: {len(data['elements'])} ways")
        seen_bboxes.add(route_bbox(key))
    for bbox in seen_bboxes:
        fac = fetch_facilities(bbox)
        print(f"facilities {bbox}: {len(fac['elements'])} nodes/ways")


if __name__ == "__main__":
    main()

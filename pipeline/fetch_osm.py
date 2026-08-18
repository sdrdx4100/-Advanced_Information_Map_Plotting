"""Fetch expressway geometry and facility points from OpenStreetMap via the
Overpass API. Used as the road-centerline source (auxiliary/primary geometry
source per the requirements doc) since GSI's height-attributed centerline does
not have confirmed national/complete coverage for these corridors.

Caches raw Overpass responses to data/raw/ so repeated pipeline runs don't hammer
the public API.

Routes are matched by OSM `name` (not `ref`): several expressways share a route
ref with their "sister" road (東名/名神 are both ref=E1, 新東名/新名神 are both
ref=E1A), so ref alone can't tell them apart.
"""
from __future__ import annotations

import json
import os

import requests

HEADERS = {
    "User-Agent": "road-gradient-map-prototype/0.1 (research; contact: sdrdx3xd3@gmail.com)"
}
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

RAW_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

# key: internal route name (also used in output filenames)
# osm_name: exact OSM `name` tag to match
# bbox: (south, west, north, east), padded around the route's mapped extent
# "slug" is the ASCII-safe identifier used in output filenames/URLs (Japanese
# filenames round-trip fine locally, but are a needless risk once this is
# deployed to Cloudflare Workers static assets). "key" (the dict key, e.g.
# "東名") is the display name carried in GeoJSON properties and route.json.
ROUTE_DEFS = {
    "東名": {"slug": "tomei", "osm_name": "東名高速道路", "bbox": (34.85, 137.55, 35.32, 138.98), "color": "#297a58", "default": True},
    "新東名": {"slug": "shin-tomei", "osm_name": "新東名高速道路", "bbox": (34.85, 137.55, 35.32, 138.98), "color": "#5558a9", "default": True},
    "新名神": {"slug": "shin-meishin", "osm_name": "新名神高速道路", "bbox": (34.70, 135.10, 35.10, 136.70), "color": "#a15a2e", "default": False},
}


def _query(q: str) -> dict:
    r = requests.post(OVERPASS_URL, data={"data": q}, headers=HEADERS, timeout=180)
    r.raise_for_status()
    return r.json()


def _cached(name: str, query: str) -> dict:
    path = os.path.join(RAW_DIR, name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    data = _query(query)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


def fetch_ways(route_key: str) -> dict:
    osm_name = ROUTE_DEFS[route_key]["osm_name"]
    s, w, n, e = ROUTE_DEFS[route_key]["bbox"]
    q = f"""
    [out:json][timeout:180];
    way["highway"="motorway"]["name"="{osm_name}"]({s},{w},{n},{e});
    out geom;
    """
    return _cached(f"ways_{ROUTE_DEFS[route_key]['slug']}.json", q)


def fetch_facilities(bbox: tuple[float, float, float, float]) -> dict:
    s, w, n, e = bbox
    key = f"facilities_{s}_{w}_{n}_{e}.json"
    q = f"""
    [out:json][timeout:180];
    (
      node["highway"="motorway_junction"]({s},{w},{n},{e});
      nwr["highway"="services"]({s},{w},{n},{e});
      nwr["highway"="rest_area"]({s},{w},{n},{e});
    );
    out center;
    """
    return _cached(key, q)


def main():
    seen_bboxes = set()
    for key in ROUTE_DEFS:
        data = fetch_ways(key)
        print(f"{key}: {len(data['elements'])} ways")
        seen_bboxes.add(ROUTE_DEFS[key]["bbox"])
    for bbox in seen_bboxes:
        fac = fetch_facilities(bbox)
        print(f"facilities {bbox}: {len(fac['elements'])} nodes/ways")


if __name__ == "__main__":
    main()

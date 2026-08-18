"""Fetch Tomei / Shin-Tomei road geometry and facility points from OpenStreetMap
via the Overpass API. Used as the road-centerline source (auxiliary/primary geometry
source per the requirements doc) since GSI's height-attributed centerline does not
have confirmed national/complete coverage for this corridor.

Caches raw Overpass responses to data/raw/ so repeated pipeline runs don't hammer
the public API.
"""
from __future__ import annotations

import json
import os

import requests

HEADERS = {
    "User-Agent": "road-gradient-map-prototype/0.1 (research; contact: sdrdx3xd3@gmail.com)"
}
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# 御殿場JCT 〜 浜松いなさJCT corridor, padded.
BBOX = (34.85, 137.55, 35.32, 138.98)  # south, west, north, east

RAW_DIR = os.path.join(os.path.dirname(__file__), "data", "raw")
os.makedirs(RAW_DIR, exist_ok=True)

ROUTES = {
    "東名": "E1",
    "新東名": "E1A",
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


def fetch_ways(route_ref: str, name: str) -> dict:
    s, w, n, e = BBOX
    q = f"""
    [out:json][timeout:180];
    way["highway"="motorway"]["ref"~"^{route_ref}$"]({s},{w},{n},{e});
    out geom;
    """
    return _cached(f"ways_{name}.json", q)


def fetch_facilities() -> dict:
    s, w, n, e = BBOX
    q = f"""
    [out:json][timeout:180];
    (
      node["highway"="motorway_junction"]({s},{w},{n},{e});
      nwr["highway"="services"]({s},{w},{n},{e});
      nwr["highway"="rest_area"]({s},{w},{n},{e});
    );
    out center;
    """
    return _cached("facilities.json", q)


def main():
    for name, ref in ROUTES.items():
        data = fetch_ways(ref, name)
        print(f"{name} ({ref}): {len(data['elements'])} ways")
    fac = fetch_facilities()
    print(f"facilities: {len(fac['elements'])} nodes/ways")


if __name__ == "__main__":
    main()

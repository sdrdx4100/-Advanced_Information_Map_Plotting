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

import json
import os

HERE = os.path.dirname(__file__)
PUBLIC_DATA_DIR = os.path.join(HERE, "..", "public", "data")
OUT_PATH = os.path.join(HERE, "local_only", "geometry_bundle.json")


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
    return by_direction


def main():
    with open(os.path.join(PUBLIC_DATA_DIR, "routes.json"), "r", encoding="utf-8") as f:
        routes = json.load(f)

    bundle = {"routes": [], "facilities": {}, "chains": {}}
    for r in routes:
        key, slug = r["key"], r["slug"]
        bundle["routes"].append(key)

        fpath = os.path.join(PUBLIC_DATA_DIR, f"facilities-{slug}.geojson")
        facilities = {}
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                ffc = json.load(f)
            for feat in ffc["features"]:
                lon, lat = feat["geometry"]["coordinates"]
                facilities[feat["properties"]["name"]] = [round(lon, 6), round(lat, 6)]
        bundle["facilities"][key] = facilities

        bundle["chains"][key] = build_chains_for_route(slug)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = os.path.getsize(OUT_PATH) / 1_000_000
    print(f"wrote {OUT_PATH} ({size_mb:.2f} MB), {len(bundle['routes'])} routes")


if __name__ == "__main__":
    main()

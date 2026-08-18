"""GSI (国土地理院) elevation tile sampler.

Uses the plain-text DEM tile format (`.txt`, 256x256 grid of meters, "e" = no-data),
not the RGB-encoded PNG variant. Tries the highest-precision dataset first (dem5a:
5m LiDAR mesh) and falls back to coarser/broader-coverage datasets when a tile is
missing, since dem5a/dem5b coverage is patchy outside dense urban and priority areas.

Verified empirically against https://cyberjapandata.gsi.go.jp (2026-08-18):
- dem5a / dem5b: 5m mesh, best coverage at z14/z15, patchy elsewhere.
- dem: legacy combined 10m/50m mesh, near-national coverage, works at z10/z12/z14.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from functools import lru_cache
from typing import Optional, Tuple

import numpy as np
import requests

HEADERS = {
    "User-Agent": "road-gradient-map-prototype/0.1 (research; contact: sdrdx3xd3@gmail.com)"
}
BASE = "https://cyberjapandata.gsi.go.jp/xyz"
TILE_SIZE = 256

# (dataset, zoom) tried in order, highest precision / most restrictive coverage first.
DATASET_CHAIN = [
    ("dem5a", 15),
    ("dem5a", 14),
    ("dem5b", 15),
    ("dem5b", 14),
    ("dem", 14),
    ("dem", 12),
    ("dem", 10),
]

CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "dem_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _lonlat_to_tilef(lon: float, lat: float, z: int) -> Tuple[float, float]:
    n = 2**z
    fx = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    fy = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return fx, fy


def _cache_path(dataset: str, z: int, x: int, y: int) -> str:
    return os.path.join(CACHE_DIR, f"{dataset}_{z}_{x}_{y}.npy")


@lru_cache(maxsize=8192)
def _fetch_tile(dataset: str, z: int, x: int, y: int) -> Optional[np.ndarray]:
    cache_path = _cache_path(dataset, z, x, y)
    if os.path.exists(cache_path):
        arr = np.load(cache_path)
        return None if arr.size == 1 and np.isnan(arr[0, 0]) and arr.shape == (1, 1) else arr

    url = f"{BASE}/{dataset}/{z}/{x}/{y}.txt"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        np.save(cache_path, np.array([[np.nan]]))
        return None

    rows = r.text.strip("\n").split("\n")
    grid = np.full((len(rows), TILE_SIZE), np.nan, dtype=np.float64)
    for i, row in enumerate(rows):
        for j, v in enumerate(row.split(",")):
            v = v.strip()
            if v and v != "e":
                try:
                    grid[i, j] = float(v)
                except ValueError:
                    pass
    np.save(cache_path, grid)
    return grid


def _bilinear(grid: np.ndarray, px: float, py: float) -> Optional[float]:
    h, w = grid.shape
    x0 = min(max(int(math.floor(px - 0.5)), 0), w - 1)
    y0 = min(max(int(math.floor(py - 0.5)), 0), h - 1)
    x1 = min(x0 + 1, w - 1)
    y1 = min(y0 + 1, h - 1)
    fx = min(max(px - 0.5 - x0, 0.0), 1.0)
    fy = min(max(py - 0.5 - y0, 0.0), 1.0)
    v00, v01, v10, v11 = grid[y0, x0], grid[y0, x1], grid[y1, x0], grid[y1, x1]
    vals = [v for v in (v00, v01, v10, v11) if not math.isnan(v)]
    if not vals:
        return None
    if len(vals) < 4:
        return float(np.mean(vals))
    top = v00 * (1 - fx) + v01 * fx
    bot = v10 * (1 - fx) + v11 * fx
    return float(top * (1 - fy) + bot * fy)


def sample_elevation(lon: float, lat: float) -> Tuple[Optional[float], Optional[str]]:
    """Returns (elevation_m, dataset_used) or (None, None) if no dataset has coverage."""
    for dataset, z in DATASET_CHAIN:
        fx, fy = _lonlat_to_tilef(lon, lat, z)
        x, y = int(fx), int(fy)
        grid = _fetch_tile(dataset, z, x, y)
        if grid is None:
            continue
        px = (fx - x) * TILE_SIZE
        py = (fy - y) * TILE_SIZE
        val = _bilinear(grid, px, py)
        if val is not None and not math.isnan(val):
            return val, dataset
    return None, None

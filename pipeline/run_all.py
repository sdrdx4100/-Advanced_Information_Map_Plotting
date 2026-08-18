"""Batch driver: fetch OSM data for every route in fetch_osm.ROUTE_DEFS, build
all three gradient-window variants, and sync the output into public/data/.

Each step runs as its own subprocess (not an in-process import) because
build_pipeline.py reads GRADE_WINDOW_M from the environment at import time —
running it 3x in one process wouldn't pick up the second/third window size.

Usage:
    .venv/Scripts/python.exe run_all.py            # fetch + build all windows + sync
    .venv/Scripts/python.exe run_all.py --no-fetch # reuse cached OSM/DEM data
    .venv/Scripts/python.exe run_all.py --windows 100          # just one window
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(__file__)
PY = sys.executable
OUT_DIR = os.path.join(HERE, "data", "output")
PUBLIC_DATA_DIR = os.path.join(HERE, "..", "public", "data")


def run(args, env=None):
    print(f"$ {' '.join(args)}", flush=True)
    merged_env = {**os.environ, "PYTHONUTF8": "1", **(env or {})}
    t0 = time.time()
    subprocess.run(args, cwd=HERE, env=merged_env, check=True)
    print(f"  ({time.time() - t0:.0f}s)", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true", help="skip fetch_osm.py (reuse data/raw/ cache)")
    ap.add_argument("--windows", nargs="+", type=int, default=[50, 100, 250])
    ap.add_argument("--no-sync", action="store_true", help="skip copying data/output/ into public/data/")
    args = ap.parse_args()

    if not args.no_fetch:
        run([PY, "fetch_osm.py"])

    for window in args.windows:
        run([PY, "build_pipeline.py"], env={"GRADE_WINDOW_M": str(window)})

    if not args.no_sync:
        os.makedirs(PUBLIC_DATA_DIR, exist_ok=True)
        # clear stale files first (e.g. a route removed from ROUTE_DEFS should
        # disappear from public/data/, not linger as an orphaned fetchable file)
        for name in os.listdir(PUBLIC_DATA_DIR):
            if name.endswith((".geojson", ".json")):
                os.remove(os.path.join(PUBLIC_DATA_DIR, name))
        n = 0
        for name in os.listdir(OUT_DIR):
            if name.endswith((".geojson", ".json")):
                shutil.copyfile(os.path.join(OUT_DIR, name), os.path.join(PUBLIC_DATA_DIR, name))
                n += 1
        print(f"synced {n} files to {os.path.abspath(PUBLIC_DATA_DIR)}")


if __name__ == "__main__":
    main()

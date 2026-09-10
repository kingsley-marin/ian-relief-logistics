#!/usr/bin/env python3
"""Fetch Hurricane Ian's best track (AL092022) from NOAA/NHC HURDAT2 and write a tidy CSV.

Source
------
HURDAT2 Atlantic database: https://www.nhc.noaa.gov/data/hurdat/
The file name changes with every re-analysis release (e.g. hurdat2-1851-2025-02272026.txt). This
script reads the directory listing and picks the newest file, so it will not break next year.
Format spec: https://www.nhc.noaa.gov/data/hurdat/hurdat2-format-atl-1851-2021.pdf
Public domain (NOAA).

Alternative (not used): the NHC GIS best-track shapefile zip
https://www.nhc.noaa.gov/gis/best_track/al092022_best_track.zip — same fixes, but needs a
shapefile reader (pyshp/fiona) which this scaffold deliberately avoids.

Output: data/clean/storm_track_al092022.csv with
  time_utc, record_id, status, lat, lon, wind_kt, pressure_mb, r34_ne..r34_nw, r50_*, r64_*, rmw_nmi
Landfall row: 2022-09-28 19:05 UTC, record_id 'L', 130 kt, 940 mb (Cayo Costa, FL).
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import config, http  # noqa: E402

INDEX_URL = "https://www.nhc.noaa.gov/data/hurdat/"
CLEAN_PATH = config.CLEAN_DIR / f"storm_track_{config.STORM_ATCF_ID.lower()}.csv"
COLUMNS = [
    "time_utc", "record_id", "status", "lat", "lon", "wind_kt", "pressure_mb",
    "r34_ne_nmi", "r34_se_nmi", "r34_sw_nmi", "r34_nw_nmi",
    "r50_ne_nmi", "r50_se_nmi", "r50_sw_nmi", "r50_nw_nmi",
    "r64_ne_nmi", "r64_se_nmi", "r64_sw_nmi", "r64_nw_nmi",
    "rmw_nmi",
]


def newest_hurdat2_filename(index_html: str) -> str:
    names = set(re.findall(r"hurdat2-1851-\d{4}-\d{6,8}\.txt", index_html))
    if not names:
        raise ValueError("no hurdat2 file names found in index page")

    def key(n: str):
        m = re.match(r"hurdat2-1851-(\d{4})-(\d+)\.txt", n)
        year = int(m.group(1))
        stamp = m.group(2)
        # stamps are MMDDYY or MMDDYYYY; normalise to a sortable (yyyy, mm, dd)
        mm, dd, yy = stamp[:2], stamp[2:4], stamp[4:]
        yyyy = int(yy) if len(yy) == 4 else 2000 + int(yy)
        return (year, yyyy, int(mm), int(dd))

    return sorted(names, key=key)[-1]


def _coord(tok: str) -> float:
    tok = tok.strip()
    val = float(tok[:-1])
    return -val if tok[-1] in ("S", "W") else val


def _int(tok: str) -> str:
    tok = tok.strip()
    if tok in ("", "-999", "-99"):
        return ""
    return str(int(tok))


def parse_storm(text: str, atcf_id: str) -> list[dict]:
    lines = text.splitlines()
    rows: list[dict] = []
    i = 0
    while i < len(lines):
        header = lines[i]
        if not header.startswith("AL") and not header.startswith("EP"):
            i += 1
            continue
        parts = [p.strip() for p in header.split(",")]
        sid, n = parts[0], int(parts[2])
        block = lines[i + 1 : i + 1 + n]
        i += 1 + n
        if sid != atcf_id:
            continue
        for ln in block:
            f = [x.strip() for x in ln.split(",")]
            time_utc = f"{f[0][:4]}-{f[0][4:6]}-{f[0][6:8]}T{f[1][:2]}:{f[1][2:]}:00Z"
            row = {
                "time_utc": time_utc,
                "record_id": f[2],
                "status": f[3],
                "lat": _coord(f[4]),
                "lon": _coord(f[5]),
                "wind_kt": _int(f[6]),
                "pressure_mb": _int(f[7]),
            }
            radii = [_int(x) for x in f[8:20]]
            for name, val in zip(COLUMNS[7:19], radii):
                row[name] = val
            row["rmw_nmi"] = _int(f[20]) if len(f) > 20 else ""
            rows.append(row)
        break
    return rows


def main(force: bool = False) -> int:
    config.ensure_dirs()
    print(f"NHC HURDAT2 best track for {config.STORM_ATCF_ID} -> {CLEAN_PATH.relative_to(config.REPO_ROOT)}")
    idx = http.fetch("hurdat2_index.html", INDEX_URL, force=force, timeout=60)
    if not idx.ok:
        print(f"  FAILED to read index: {idx.error}")
        return 1
    fname = newest_hurdat2_filename(idx.path.read_text(encoding="utf-8", errors="replace"))
    res = http.fetch(fname, INDEX_URL + fname, force=force, timeout=180)
    if not res.ok:
        print(f"  FAILED to download {fname}: {res.error}")
        return 1
    rows = parse_storm(res.path.read_text(encoding="utf-8", errors="replace"), config.STORM_ATCF_ID)
    if not rows:
        http.write_failure("storm_track", INDEX_URL + fname, f"{config.STORM_ATCF_ID} not found in {fname}")
        print(f"  FAILED: storm not found in {fname}")
        return 1
    with open(CLEAN_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    landfalls = [r for r in rows if r["record_id"] == "L"]
    print(f"  {len(rows)} fixes from {fname} ({'cache' if res.from_cache else 'live'}); landfall records: "
          + ", ".join(f"{r['time_utc']} {r['wind_kt']}kt {r['pressure_mb']}mb" for r in landfalls))
    return 0


if __name__ == "__main__":
    sys.exit(main(force="--force" in sys.argv))

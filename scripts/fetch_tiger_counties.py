#!/usr/bin/env python3
"""Fetch county boundaries for the study area from the US Census Bureau (TIGER).

Source
------
TIGERweb ArcGIS REST service (Census Bureau, public domain, no key):
  https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1
Layer 1 = current-vintage Counties. Returns GeoJSON directly in EPSG:4326.

Why not the shapefile zip (https://www2.census.gov/geo/tiger/TIGER2023/COUNTY/tl_2023_us_county.zip)?
It is ~80 MB for all US counties and needs a shapefile reader. The REST query returns exactly the
five polygons we need (~220 KB). The shapefile is the better choice for reproducible archival
vintages (TIGERweb tracks the newest release); note that in DECISIONS.md if it matters to you.

Output: data/clean/counties.geojson (5 features: GEOID, NAME, ...).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import config, http  # noqa: E402

LAYER_URL = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1"
RAW_NAME = "tiger_counties.geojson"
CLEAN_PATH = config.CLEAN_DIR / "counties.geojson"


def main(force: bool = False) -> int:
    config.ensure_dirs()
    print(f"Census TIGERweb counties -> {CLEAN_PATH.relative_to(config.REPO_ROOT)}")
    county_codes = ",".join(f"'{f[2:]}'" for f in config.COUNTY_FIPS)
    params = {
        "where": f"STATE='{config.STATE_FIPS}' AND COUNTY IN ({county_codes})",
        "outFields": "GEOID,STATE,COUNTY,NAME,BASENAME,AREALAND,AREAWATER,CENTLAT,CENTLON",
        "outSR": "4326",
        "f": "geojson",
    }
    res = http.fetch(RAW_NAME, f"{LAYER_URL}/query", params=params, force=force, timeout=120)
    if not res.ok:
        print(f"  FAILED: {res.error}")
        return 1
    doc = json.loads(res.path.read_text(encoding="utf-8"))
    if "error" in doc:
        http.write_failure("tiger_counties", LAYER_URL, json.dumps(doc["error"]))
        print(f"  server error: {doc['error']}")
        return 1
    feats = doc.get("features", [])
    for f in feats:
        p = f["properties"]
        p["county_fips"] = p.get("GEOID")
        p["county_norm"] = config.normalize_county(p.get("BASENAME"))
    out = {"type": "FeatureCollection", "name": "study_area_counties", "source": LAYER_URL, "features": feats}
    CLEAN_PATH.write_text(json.dumps(out), encoding="utf-8")
    print(f"  {len(feats)} counties ({'cache' if res.from_cache else 'live'}): " + ", ".join(f"{f['properties']['GEOID']} {f['properties']['NAME']}" for f in feats))
    missing = set(config.COUNTY_FIPS) - {f["properties"]["GEOID"] for f in feats}
    if missing:
        print(f"  WARNING: missing counties {sorted(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(force="--force" in sys.argv))

#!/usr/bin/env python3
"""Fetch shelter facilities for the five study counties from HIFLD Open / FEMA NSS.

Source
------
HIFLD Open's "National Shelter System Facilities" is served by FEMA's ArcGIS server:
  https://gis.fema.gov/arcgis/rest/services/NSS/FEMA_NSS/FeatureServer/5   (layer "Shelter Locations")
The older services1.arcgis.com/Hp6G80Pky0om7QvQ/... URL that many tutorials cite returns 400 now
(checked 2026-09-09). See docs/DECISIONS.md.

No API key. Public domain (US federal government work).

What this script does
---------------------
1. Queries the layer with a WHERE clause on state + county_parish (the county field is spelled
   inconsistently: 'LEE', 'Lee', 'DE SOTO', 'DeSoto', 'Desoto' — we ask for all spellings).
2. Pages with resultOffset in case the count ever exceeds maxRecordCount (4000).
3. Caches the raw GeoJSON pages under data/raw/ and writes a single merged
   data/clean/hifld_shelters.geojson with normalised county names.
4. If the endpoint is unreachable, writes data/raw/hifld_shelters.FAILED.json and copies the
   committed sample (data/sample/hifld_shelters_sample.geojson, 10 features) into data/clean/
   with "source_label": "SAMPLE" so downstream steps still run — but the label travels with it.

Honesty note: this is a CURRENT snapshot of the shelter registry, not the September 2022 state.
Shelters that opened for Ian may have since been removed; new ones added. shelter_status_code is
'CLOSED' for every row today (no active incident). Treat it as "candidate shelter locations".
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import config, http  # noqa: E402

LAYER_URL = "https://gis.fema.gov/arcgis/rest/services/NSS/FEMA_NSS/FeatureServer/5"
OUT_FIELDS = [
    "shelter_id", "shelter_name", "address_1", "city", "county_parish", "fips_code", "state", "zip",
    "facility_usage_code", "evacuation_capacity", "post_impact_capacity", "ada_compliant",
    "wheelchair_accessible", "pet_accommodations_code", "generator_onsite", "in_100_yr_floodplain",
    "in_500_yr_floodplain", "in_surge_slosh_area", "pre_landfall_shelter", "shelter_status_code",
    "facility_type", "org_organization_name", "latitude", "longitude",
]
COUNTY_SPELLINGS = ["LEE", "CHARLOTTE", "COLLIER", "SARASOTA", "DESOTO", "DE SOTO"]
PAGE_SIZE = 2000
CLEAN_PATH = config.CLEAN_DIR / "hifld_shelters.geojson"
SAMPLE_PATH = config.SAMPLE_DIR / "hifld_shelters_sample.geojson"


def build_where() -> str:
    quoted = ",".join(f"'{c}'" for c in COUNTY_SPELLINGS)
    return f"state='FL' AND UPPER(county_parish) IN ({quoted})"


def fetch_pages(force: bool = False) -> list[dict] | None:
    features: list[dict] = []
    offset = 0
    page = 0
    while True:
        name = f"hifld_shelters_page{page}.geojson"
        params = {
            "where": build_where(),
            "outFields": ",".join(OUT_FIELDS),
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
        }
        res = http.fetch(name, f"{LAYER_URL}/query", params=params, force=force, timeout=90)
        if not res.ok:
            print(f"  FAILED: {res.error}")
            return None
        doc = json.loads(res.path.read_text(encoding="utf-8"))
        if "error" in doc:
            http.write_failure("hifld_shelters", LAYER_URL, json.dumps(doc["error"]))
            print(f"  server error: {doc['error']}")
            return None
        got = doc.get("features", [])
        features.extend(got)
        print(f"  page {page}: {len(got)} features ({'cache' if res.from_cache else 'live'})")
        exceeded = doc.get("properties", {}).get("exceededTransferLimit") or doc.get("exceededTransferLimit")
        if not exceeded or not got:
            break
        offset += len(got)
        page += 1
        http.polite_pause(1.0)
    return features


def normalise(features: list[dict], source_label: str) -> dict:
    seen: set[str] = set()
    out = []
    for f in features:
        p = f.get("properties") or {}
        sid = p.get("shelter_id")
        if sid is None:
            continue
        key = str(sid)
        if key in seen:           # the service can return the same facility twice across pages
            continue
        seen.add(key)
        p["county_norm"] = config.normalize_county(p.get("county_parish"))
        p["source_system"] = "HIFLD_NSS"
        p["source_label"] = source_label
        out.append({"type": "Feature", "geometry": f.get("geometry"), "properties": p})
    return {
        "type": "FeatureCollection",
        "name": "hifld_shelters_study_area",
        "source": LAYER_URL,
        "source_label": source_label,
        "features": out,
    }


def main(force: bool = False) -> int:
    config.ensure_dirs()
    print(f"HIFLD/FEMA NSS shelters -> {CLEAN_PATH.relative_to(config.REPO_ROOT)}")
    feats = fetch_pages(force=force)
    if feats is None:
        print(f"  falling back to committed sample: {SAMPLE_PATH.relative_to(config.REPO_ROOT)}")
        sample = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        doc = normalise(sample["features"], "SAMPLE")
        CLEAN_PATH.write_text(json.dumps(doc), encoding="utf-8")
        print(f"  wrote {len(doc['features'])} SAMPLE features (labelled as such)")
        return 2
    doc = normalise(feats, "LIVE")
    CLEAN_PATH.write_text(json.dumps(doc), encoding="utf-8")
    by_county: dict[str, int] = {}
    for f in doc["features"]:
        c = f["properties"]["county_norm"]
        by_county[c] = by_county.get(c, 0) + 1
    print(f"  wrote {len(doc['features'])} features; by county: {by_county}")
    return 0


if __name__ == "__main__":
    sys.exit(main(force="--force" in sys.argv))

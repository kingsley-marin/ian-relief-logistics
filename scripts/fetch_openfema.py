#!/usr/bin/env python3
"""Fetch the FEMA disaster declaration summary for Hurricane Ian in Florida (DR-4673-FL).

Source
------
OpenFEMA API v2, DisasterDeclarationsSummaries. No key. Public domain.
  https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries?$filter=disasterNumber eq 4673
One row per designated area (county / tribal area) x declaration type, so ~70 rows for one
disaster. We keep all of them and also write a compact per-county CSV for the study counties.

Output:
  data/raw/openfema_dr4673.json           (full API response)
  data/clean/fema_dr4673_declarations.csv (one row per designated area)
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import config, http  # noqa: E402

API_URL = "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
DISASTER_NUMBER = 4673
RAW_NAME = f"openfema_dr{DISASTER_NUMBER}.json"
CLEAN_PATH = config.CLEAN_DIR / f"fema_dr{DISASTER_NUMBER}_declarations.csv"
FIELDS = [
    "femaDeclarationString", "disasterNumber", "state", "declarationType", "declarationDate", "incidentType",
    "declarationTitle", "incidentBeginDate", "incidentEndDate", "fipsStateCode", "fipsCountyCode",
    "designatedArea", "ihProgramDeclared", "iaProgramDeclared", "paProgramDeclared", "hmProgramDeclared",
]


def main(force: bool = False) -> int:
    config.ensure_dirs()
    print(f"OpenFEMA DR-{DISASTER_NUMBER}-FL -> {CLEAN_PATH.relative_to(config.REPO_ROOT)}")
    params = {"$filter": f"disasterNumber eq {DISASTER_NUMBER}", "$top": 1000}
    res = http.fetch(RAW_NAME, API_URL, params=params, force=force, timeout=60)
    if not res.ok:
        print(f"  FAILED: {res.error}")
        return 1
    doc = json.loads(res.path.read_text(encoding="utf-8"))
    rows = doc.get("DisasterDeclarationsSummaries", [])
    with open(CLEAN_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS + ["county_fips", "in_study_area"])
        w.writeheader()
        for r in rows:
            fips = f"{r.get('fipsStateCode','')}{r.get('fipsCountyCode','')}"
            out = {k: r.get(k) for k in FIELDS}
            out["county_fips"] = fips
            out["in_study_area"] = fips in config.COUNTY_FIPS
            w.writerow(out)
    study = sorted({r["designatedArea"] for r in rows if f"{r.get('fipsStateCode','')}{r.get('fipsCountyCode','')}" in config.COUNTY_FIPS})
    first = rows[0] if rows else {}
    print(f"  {len(rows)} designated-area rows ({'cache' if res.from_cache else 'live'}); declared {str(first.get('declarationDate',''))[:10]}, "
          f"incident {str(first.get('incidentBeginDate',''))[:10]}..{str(first.get('incidentEndDate',''))[:10]}")
    print(f"  study-area designations: {study}")
    return 0


if __name__ == "__main__":
    sys.exit(main(force="--force" in sys.argv))

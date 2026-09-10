#!/usr/bin/env python3
"""Run every fetcher in sequence with a pause between them. Same as `make fetch`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

import fetch_hifld_shelters, fetch_nhc_track, fetch_openfema, fetch_osm_roads, fetch_tiger_counties  # noqa: E402
from ianrelief import http  # noqa: E402

if __name__ == "__main__":
    force = "--force" in sys.argv
    rc = 0
    for mod in (fetch_hifld_shelters, fetch_nhc_track, fetch_tiger_counties, fetch_openfema, fetch_osm_roads):
        rc = max(rc, mod.main(force=force))
        http.polite_pause(1.0)
    sys.exit(rc)

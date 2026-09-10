#!/usr/bin/env python3
"""First working slice: shelters, unmet demand, nearest warehouse (straight-line) at a given time.

    make demo                       # PostGIS path (needs `make up && make load`)
    make demo-offline               # pure-python path from CSV/GeoJSON, no database
    python scripts/demo.py --at 2022-10-01T15:00:00Z [--offline]

Try different --at values: 2022-09-29T12:00Z (day after landfall), 2022-10-01T15:00Z (I-75 Myakka
closure active), 2022-10-20T12:00Z (Sanibel Causeway reopened). Watch the closures list change.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import cleaning, config, demo, loaders  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="2022-09-29T12:00:00Z", help="ISO-8601 UTC instant, e.g. 2022-09-29T12:00:00Z")
    ap.add_argument("--offline", action="store_true", help="compute from CSV/GeoJSON with haversine; no database")
    args = ap.parse_args()
    t = cleaning.parse_utc(args.at)
    if t is None:
        print("--at must be a timezone-aware ISO timestamp, e.g. 2022-09-29T12:00:00Z")
        return 2
    if args.offline:
        inputs = loaders.read_all_inputs()
        rows, active = demo.run_offline(inputs, t)
        print(demo.format_table(rows, active, t, "offline / haversine"))
        print(f"(inputs: shelters from {inputs['labels']['shelters']}; {len(inputs['rejects'])} rows rejected during cleaning)")
        return 0
    from ianrelief import db  # imported late so the offline path never needs psycopg2 to connect

    try:
        with db.connection() as conn:
            rows, active = demo.run_sql(conn, t)
    except Exception as exc:  # noqa: BLE001
        print(f"database not reachable at {config.DATABASE_URL}: {exc}\n\nRun `make up && make load`, or use `make demo-offline`.")
        return 1
    print(demo.format_table(rows, active, t, "PostGIS / ST_Distance(geography)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

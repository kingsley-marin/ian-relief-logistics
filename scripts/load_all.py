#!/usr/bin/env python3
"""Load everything into PostGIS (idempotent). Same as `make load`.

    python scripts/load_all.py            # sites, vehicles, inventory, demand, closures, storm track, counties, roads
    python scripts/load_all.py --no-roads # skip the ~10k-way road table (faster for schema/demo iteration)
    python scripts/load_all.py --dry-run  # clean only: print counts and write data/clean/rejects.csv, no database

Rejected rows -> data/clean/rejects.csv (source, row_number, key, reason, raw).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import cleaning, config, db, loaders  # noqa: E402


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    inputs = loaders.read_all_inputs()
    print("inputs:")
    for k, v in inputs["labels"].items():
        print(f"  {k:12s} {v}")
    print("clean rows:", {k: len(inputs[k]) for k in ("shelters", "warehouses", "vehicles", "inventory", "demand", "closures", "storm_track", "roads", "counties")})
    n_rej = len(inputs["rejects"])
    if dry:
        cleaning.write_rejects(inputs["rejects"])
        print(f"dry run: {n_rej} rejected rows written to {config.REJECTS_PATH.relative_to(config.REPO_ROOT)}")
        for r in inputs["rejects"]:
            print(f"  REJECT {r.source}:{r.row_number} {r.key} — {r.reason}")
        return 0
    with db.connection() as conn:
        stats = loaders.load_everything(conn, inputs, include_roads="--no-roads" not in argv)
    print(f"\n{'loader':12s} {'rows_in':>8s} {'upserted':>9s} {'rejected':>9s}  source")
    for s in stats:
        print(f"{s.loader:12s} {s.rows_in:8d} {s.rows_upserted:9d} {s.rows_rejected:9d}  {s.source_path}")
    print(f"\n{n_rej} rejected rows -> {config.REJECTS_PATH.relative_to(config.REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

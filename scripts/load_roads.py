#!/usr/bin/env python3
"""Load ONLY roads from data/clean/roads.geojson (the slow table; ~10k ways)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import cleaning, config, db, loaders  # noqa: E402

if __name__ == "__main__":
    path = config.CLEAN_DIR / "roads.geojson"
    if not path.exists():
        print("data/clean/roads.geojson missing — run `make fetch-roads` first")
        sys.exit(1)
    rows, rejects = cleaning.clean_roads(loaders.read_geojson_features(path))
    with db.connection() as conn:
        n = loaders.upsert_roads(conn, rows)
        loaders.log_load(conn, loaders.LoadStats("roads", str(path.relative_to(config.REPO_ROOT)), len(rows) + len(rejects), n, len(rejects)))
    print(f"roads upserted: {n}; rejected: {len(rejects)}")

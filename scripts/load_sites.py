#!/usr/bin/env python3
"""Load ONLY sites (shelters + warehouses) and the crosswalk. Handy when iterating on shelter cleaning."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import cleaning, config, db, loaders  # noqa: E402

if __name__ == "__main__":
    inputs = loaders.read_all_inputs()
    with db.connection() as conn:
        n = loaders.upsert_sites(conn, inputs["sites"])
        loaders.log_load(conn, loaders.LoadStats("sites", inputs["labels"]["shelters"], len(inputs["sites"]), n, 0))
    site_rejects = [r for r in inputs["rejects"] if r.source.startswith(("hifld", "warehouses"))]
    cleaning.write_rejects(site_rejects)
    print(f"sites upserted: {n} ({len(inputs['shelters'])} shelters, {len(inputs['warehouses'])} warehouses); "
          f"{len(site_rejects)} rejects -> {config.REJECTS_PATH.relative_to(config.REPO_ROOT)}")

#!/usr/bin/env python3
"""Load ONLY the synthetic scenario tables: vehicles, inventory, demand, closures.

Requires sites to be loaded first (foreign keys). Rejects are appended to the rejects file for these
sources only. Use this when Kingsley edits the synthetic CSVs and wants a fast reload.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import cleaning, config, db, loaders  # noqa: E402

if __name__ == "__main__":
    inputs = loaders.read_all_inputs()
    with db.connection() as conn:
        counts = {
            "vehicles": loaders.upsert_vehicles(conn, inputs["vehicles"]),
            "inventory": loaders.upsert_inventory(conn, inputs["inventory"]),
            "demand": loaders.upsert_demand(conn, inputs["demand"]),
            "closures": loaders.upsert_closures(conn, inputs["closures"]),
        }
        for k, n in counts.items():
            loaders.log_load(conn, loaders.LoadStats(k, f"data/synthetic/{k}.csv", n, n, 0))
    scenario_rejects = [r for r in inputs["rejects"] if r.source.startswith(("trucks", "inventory", "demand", "closures"))]
    cleaning.write_rejects(scenario_rejects)
    print("upserted:", counts, f"; {len(scenario_rejects)} rejects -> {config.REJECTS_PATH.relative_to(config.REPO_ROOT)}")

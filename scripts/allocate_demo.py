#!/usr/bin/env python3
"""Run the placeholder allocator against the cleaned inputs and print the plan with explanations.

    python scripts/allocate_demo.py --at 2022-09-29T12:00:00Z

This is here so the interface can be exercised end to end. The plan it prints is NOT a recommendation.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import allocate, cleaning, loaders  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="2022-09-29T12:00:00Z")
    ap.add_argument("--horizon", type=float, default=12.0)
    a = ap.parse_args()
    t = cleaning.parse_utc(a.at)
    inputs = loaders.read_all_inputs()
    plan = allocate.allocate(allocate.AllocationInputs(inputs["sites"], inputs["vehicles"], inputs["inventory"], inputs["demand"], inputs["closures"], t, a.horizon))
    print(allocate.format_plan(plan))
    print("\nExplanations:")
    for r in plan.rows:
        print("  -", r.explanation)

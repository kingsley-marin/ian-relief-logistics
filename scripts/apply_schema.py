#!/usr/bin/env python3
"""(Re)apply sql/schema.sql to the configured database. Safe to run repeatedly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ianrelief import db  # noqa: E402

if __name__ == "__main__":
    with db.connection() as conn:
        db.apply_schema(conn)
        tables = sorted(db.table_names(conn))
    print("schema applied; tables:", ", ".join(tables))

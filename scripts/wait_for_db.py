#!/usr/bin/env python3
"""Block until the PostGIS container accepts connections (used by `make up`)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ianrelief import config, db  # noqa: E402

if __name__ == "__main__":
    ok = db.wait_for_db(config.DATABASE_URL, timeout_s=90)
    print("database ready" if ok else "database NOT ready")
    sys.exit(0 if ok else 1)

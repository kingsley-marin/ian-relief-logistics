"""Shared fixtures.

Two kinds of tests live here:
  * pure-python (default): run anywhere with `make test`
  * @pytest.mark.db: need PostGIS. They connect to IAN_DATABASE_URL; if that fails they SKIP with the reason,
    they never silently pass. Run with `make up && make test-db`.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ianrelief import config, loaders  # noqa: E402


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


@pytest.fixture(scope="session")
def inputs() -> dict:
    """All inputs read + cleaned once per test session (reads data/clean if present, else data/sample)."""
    return loaders.read_all_inputs()


@pytest.fixture(scope="session")
def db_conn():
    """A live PostGIS connection with the schema applied, or skip."""
    try:
        import psycopg2  # noqa: F401

        from ianrelief import db
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"psycopg2 not installed: {exc}")
    url = os.environ.get("IAN_DATABASE_URL", config.DATABASE_URL)
    try:
        conn = db.connect(url)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostGIS not reachable at {url}: {type(exc).__name__}: {str(exc).strip().splitlines()[0]}")
    try:
        db.apply_schema(conn)
        conn.commit()
        yield conn
    finally:
        conn.close()


def pytest_collection_modifyitems(items):  # noqa: D401
    """Auto-mark tests that request the db_conn fixture with @pytest.mark.db."""
    for item in items:
        if "db_conn" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.db)

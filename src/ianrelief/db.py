"""Thin psycopg2 helpers. Nothing clever: one connection, explicit transactions."""
from __future__ import annotations

import contextlib
import time
from typing import Iterator

import psycopg2
import psycopg2.extras

from . import config


def connect(url: str | None = None):
    """Open a connection. Caller closes it (or use `connection()` as a context manager)."""
    return psycopg2.connect(url or config.DATABASE_URL)


@contextlib.contextmanager
def connection(url: str | None = None) -> Iterator["psycopg2.extensions.connection"]:
    conn = connect(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def wait_for_db(url: str | None = None, timeout_s: float = 60.0, interval_s: float = 2.0) -> bool:
    """Poll until Postgres accepts connections. Returns True on success, False on timeout."""
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            conn = connect(url)
            conn.close()
            return True
        except Exception as exc:  # noqa: BLE001 - we really do want to retry on anything here
            last_err = exc
            time.sleep(interval_s)
    print(f"database not reachable after {timeout_s:.0f}s: {last_err}")
    return False


def apply_schema(conn, schema_path=None) -> None:
    sql = (schema_path or (config.SQL_DIR / "schema.sql")).read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)


def table_names(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return {r[0] for r in cur.fetchall()}


def geometry_srids(conn) -> dict[str, int]:
    """{table.column: srid} for every registered geometry column."""
    with conn.cursor() as cur:
        cur.execute("SELECT f_table_name, f_geometry_column, srid FROM geometry_columns WHERE f_table_schema='public'")
        return {f"{t}.{c}": srid for t, c, srid in cur.fetchall()}


def execute_values(cur, sql: str, rows, page_size: int = 500) -> None:
    psycopg2.extras.execute_values(cur, sql, rows, page_size=page_size)

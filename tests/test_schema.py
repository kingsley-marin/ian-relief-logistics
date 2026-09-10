"""Schema presence. The text checks run anywhere; the db_conn ones need PostGIS (auto-marked `db`)."""
from __future__ import annotations

import re

from ianrelief import config, db

SCHEMA = (config.SQL_DIR / "schema.sql").read_text(encoding="utf-8")
EXPECTED_TABLES = {"sites", "site_crosswalk", "roads", "closures", "vehicles", "inventory", "demand", "storm_track", "counties", "load_log"}
EXPECTED_GEOM = {"sites.geom": "Point", "roads.geom": "LineString", "closures.geom": "Point", "storm_track.geom": "Point", "counties.geom": "MultiPolygon"}


def test_schema_text_declares_every_table():
    found = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA))
    assert EXPECTED_TABLES <= found, EXPECTED_TABLES - found


def test_schema_text_every_geometry_is_4326():
    decls = re.findall(r"geometry\((\w+),\s*(\d+)\)", SCHEMA)
    assert decls, "no geometry columns found"
    assert {srid for _, srid in decls} == {"4326"}
    assert {g for g, _ in decls} == set(EXPECTED_GEOM.values())


def test_schema_text_has_closure_function_and_view():
    assert "FUNCTION closures_active_at" in SCHEMA
    assert "VIEW v_shelter_unmet_demand" in SCHEMA


def test_schema_text_is_idempotent_style():
    """Every CREATE must be IF NOT EXISTS / OR REPLACE so `make schema` can be re-run."""
    for m in re.finditer(r"^CREATE (TABLE|INDEX|EXTENSION|VIEW|FUNCTION|OR REPLACE VIEW|OR REPLACE FUNCTION)\b.*$", SCHEMA, re.M):
        line = m.group(0)
        assert "IF NOT EXISTS" in line or "OR REPLACE" in line, line


# ------------------------------------------------------------------------------------------ need PostGIS

def test_db_tables_exist(db_conn):
    assert EXPECTED_TABLES <= db.table_names(db_conn)


def test_db_geometry_columns_are_4326(db_conn):
    srids = db.geometry_srids(db_conn)
    for col in EXPECTED_GEOM:
        assert srids.get(col) == 4326, (col, srids.get(col))


def test_db_postgis_available(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT PostGIS_Version()")
        assert cur.fetchone()[0]

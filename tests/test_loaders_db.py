"""Loader behaviour against a real PostGIS (auto-marked `db`; skipped when the database is unreachable).

Roads are excluded here to keep the test fast; scripts/load_roads.py covers them at `make load` time.
"""
from __future__ import annotations

from ianrelief import config, demo, loaders

from conftest import utc


def _counts(conn) -> dict[str, int]:
    out = {}
    with conn.cursor() as cur:
        for t in ("sites", "site_crosswalk", "vehicles", "inventory", "demand", "closures", "storm_track"):
            cur.execute(f"SELECT count(*) FROM {t}")
            out[t] = cur.fetchone()[0]
    return out


def test_load_is_idempotent_and_writes_rejects(db_conn, inputs, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REJECTS_PATH", tmp_path / "rejects.csv")
    stats1 = loaders.load_everything(db_conn, inputs, include_roads=False)
    db_conn.commit()
    first = _counts(db_conn)
    stats2 = loaders.load_everything(db_conn, inputs, include_roads=False)
    db_conn.commit()
    second = _counts(db_conn)
    assert first == second, (first, second)
    assert first["sites"] == len(inputs["sites"])
    assert first["demand"] == len(inputs["demand"])
    assert first["closures"] == len(inputs["closures"]) == 4
    assert (tmp_path / "rejects.csv").exists()
    assert sum(s.rows_rejected for s in stats1) == sum(s.rows_rejected for s in stats2) >= 7


def test_db_geometries_are_4326_points_in_study_area(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ST_SRID(geom) FROM sites")
        assert cur.fetchall() == [(4326,)]
        cur.execute("SELECT count(*) FROM sites WHERE NOT ST_Within(geom, ST_MakeEnvelope(%s,%s,%s,%s,4326))",
                    (config.STUDY_AREA_BBOX["west"], config.STUDY_AREA_BBOX["south"], config.STUDY_AREA_BBOX["east"], config.STUDY_AREA_BBOX["north"]))
        assert cur.fetchone()[0] == 0


def test_sql_closures_active_at_agrees_with_python(db_conn, inputs):
    from ianrelief import closures

    for t in (utc("2022-09-29T12:00:00Z"), utc("2022-10-01T15:00:00Z"), utc("2022-10-25T00:00:00Z")):
        with db_conn.cursor() as cur:
            cur.execute("SELECT closure_id FROM closures_active_at(%s)", (t,))
            sql_ids = {r[0] for r in cur.fetchall()}
        py_ids = {c["closure_id"] for c in closures.active_at(inputs["closures"], t)}
        assert sql_ids == py_ids, (t, sql_ids, py_ids)


def test_demo_sql_matches_offline_within_tolerance(db_conn, inputs):
    t = utc("2022-09-29T12:00:00Z")
    sql_rows, _ = demo.run_sql(db_conn, t)
    off_rows, _ = demo.run_offline(inputs, t)
    assert len(sql_rows) == len(off_rows) == 20
    by_key = {(r.site_id, r.commodity): r for r in off_rows}
    for r in sql_rows:
        o = by_key[(r.site_id, r.commodity)]
        assert r.qty_unmet == o.qty_unmet
        assert r.warehouse_id == o.warehouse_id
        # geography (spheroid) vs haversine (sphere): agree well within 0.5% at this scale
        assert abs(r.distance_km - o.distance_km) <= max(0.1, 0.005 * o.distance_km)

"""Idempotent PostGIS loaders.

Every loader is UPSERT (INSERT ... ON CONFLICT DO UPDATE) keyed on the table's primary key, so running
`make load` twice leaves the row counts unchanged. Dirty rows never reach the database: cleaning.py
drops them and they are written to data/clean/rejects.csv.

Reading order matters: sites first (shelters + warehouses), because vehicles/inventory/demand carry
foreign keys to site_id and the cleaners need the set of known site ids.

`read_*` helpers are shared with the offline demo so the same rows feed both paths.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from psycopg2.extras import Json

from . import cleaning, config, db


@dataclass
class LoadStats:
    loader: str
    source_path: str
    rows_in: int
    rows_upserted: int
    rows_rejected: int


# --------------------------------------------------------------------------------------------- readers

def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_geojson_features(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("features", [])


def shelters_path() -> tuple[Path, str]:
    """Prefer the fetched file; fall back to the committed sample and say so."""
    live = config.CLEAN_DIR / "hifld_shelters.geojson"
    if live.exists():
        return live, "LIVE_OR_CACHED"
    return config.SAMPLE_DIR / "hifld_shelters_sample.geojson", "SAMPLE"


def storm_track_path() -> tuple[Path, str]:
    live = config.CLEAN_DIR / f"storm_track_{config.STORM_ATCF_ID.lower()}.csv"
    if live.exists():
        return live, "LIVE_OR_CACHED"
    return config.SAMPLE_DIR / f"storm_track_{config.STORM_ATCF_ID.lower()}_sample.csv", "SAMPLE"


def read_all_inputs() -> dict:
    """Read + clean every input. Returns {'sites': [...], 'vehicles': [...], ..., 'rejects': [...], 'labels': {...}}."""
    rejects: list[cleaning.Reject] = []
    labels: dict[str, str] = {}

    sh_path, sh_label = shelters_path()
    labels["shelters"] = f"{sh_path.relative_to(config.REPO_ROOT)} ({sh_label})"
    shelters, rj = cleaning.clean_shelters(read_geojson_features(sh_path), source=sh_path.name)
    rejects += rj
    warehouses, rj = cleaning.clean_warehouses(read_csv(config.SYNTHETIC_DIR / "warehouses.csv"))
    rejects += rj
    sites = shelters + warehouses
    known = {s["site_id"] for s in sites}

    vehicles, rj = cleaning.clean_vehicles(read_csv(config.SYNTHETIC_DIR / "trucks.csv"), known)
    rejects += rj
    inventory, rj = cleaning.clean_inventory(read_csv(config.SYNTHETIC_DIR / "inventory.csv"), known)
    rejects += rj
    demand, rj = cleaning.clean_demand(read_csv(config.SYNTHETIC_DIR / "demand.csv"), known)
    rejects += rj
    closures, rj = cleaning.clean_closures(read_csv(config.SYNTHETIC_DIR / "closures.csv"))
    rejects += rj

    st_path, st_label = storm_track_path()
    labels["storm_track"] = f"{st_path.relative_to(config.REPO_ROOT)} ({st_label})"
    track, rj = cleaning.clean_storm_track(read_csv(st_path), source=st_path.name)
    rejects += rj

    roads: list[dict] = []
    roads_path = config.CLEAN_DIR / "roads.geojson"
    if roads_path.exists():
        roads, rj = cleaning.clean_roads(read_geojson_features(roads_path))
        rejects += rj
        labels["roads"] = f"{roads_path.relative_to(config.REPO_ROOT)}"
    else:
        labels["roads"] = "MISSING (run `make fetch-roads`)"

    counties: list[dict] = []
    counties_path = config.CLEAN_DIR / "counties.geojson"
    if counties_path.exists():
        counties, rj = cleaning.clean_counties(read_geojson_features(counties_path))
        rejects += rj
        labels["counties"] = f"{counties_path.relative_to(config.REPO_ROOT)}"
    else:
        labels["counties"] = "MISSING (run `make fetch-counties`)"

    return {"sites": sites, "shelters": shelters, "warehouses": warehouses, "vehicles": vehicles, "inventory": inventory,
            "demand": demand, "closures": closures, "storm_track": track, "roads": roads, "counties": counties,
            "rejects": rejects, "labels": labels}


# --------------------------------------------------------------------------------------------- upserts

def upsert_sites(conn, rows: list[dict]) -> int:
    sql = """
        INSERT INTO sites (site_id, site_type, name, address, city, county, county_fips, state, source_system, source_id,
                           evacuation_capacity, post_impact_capacity, pet_friendly, generator_onsite, in_surge_zone, attributes, geom)
        VALUES %s
        ON CONFLICT (site_id) DO UPDATE SET
            site_type = EXCLUDED.site_type, name = EXCLUDED.name, address = EXCLUDED.address, city = EXCLUDED.city,
            county = EXCLUDED.county, county_fips = EXCLUDED.county_fips, state = EXCLUDED.state,
            source_system = EXCLUDED.source_system, source_id = EXCLUDED.source_id,
            evacuation_capacity = EXCLUDED.evacuation_capacity, post_impact_capacity = EXCLUDED.post_impact_capacity,
            pet_friendly = EXCLUDED.pet_friendly, generator_onsite = EXCLUDED.generator_onsite, in_surge_zone = EXCLUDED.in_surge_zone,
            attributes = EXCLUDED.attributes, geom = EXCLUDED.geom, loaded_at = now()
    """
    template = "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326))"
    values = [(r["site_id"], r["site_type"], r["name"], r["address"], r["city"], r["county"], r["county_fips"], r["state"],
               r["source_system"], r["source_id"], r["evacuation_capacity"], r["post_impact_capacity"], r["pet_friendly"],
               r["generator_onsite"], r["in_surge_zone"], Json(r["attributes"]), r["lon"], r["lat"]) for r in rows]
    with conn.cursor() as cur:
        from psycopg2.extras import execute_values
        execute_values(cur, sql, values, template=template, page_size=500)
        xw = [(r["site_id"], r["source_system"], r["source_id"]) for r in rows if r.get("source_id")]
        execute_values(cur, """
            INSERT INTO site_crosswalk (site_id, source_system, source_id) VALUES %s
            ON CONFLICT (source_system, source_id) DO UPDATE SET site_id = EXCLUDED.site_id
        """, xw, page_size=500)
    return len(rows)


def upsert_vehicles(conn, rows: list[dict]) -> int:
    sql = """
        INSERT INTO vehicles (vehicle_id, vehicle_type, capacity_kg, capacity_pallets, home_site_id, available_from_utc, notes)
        VALUES %s
        ON CONFLICT (vehicle_id) DO UPDATE SET vehicle_type = EXCLUDED.vehicle_type, capacity_kg = EXCLUDED.capacity_kg,
            capacity_pallets = EXCLUDED.capacity_pallets, home_site_id = EXCLUDED.home_site_id,
            available_from_utc = EXCLUDED.available_from_utc, notes = EXCLUDED.notes, loaded_at = now()
    """
    values = [(r["vehicle_id"], r["vehicle_type"], r["capacity_kg"], r["capacity_pallets"], r["home_site_id"], r["available_from_utc"], r["notes"]) for r in rows]
    with conn.cursor() as cur:
        db.execute_values(cur, sql, values)
    return len(rows)


def upsert_inventory(conn, rows: list[dict]) -> int:
    sql = """
        INSERT INTO inventory (site_id, commodity, as_of_utc, qty_on_hand, unit_kg) VALUES %s
        ON CONFLICT (site_id, commodity, as_of_utc) DO UPDATE SET qty_on_hand = EXCLUDED.qty_on_hand, unit_kg = EXCLUDED.unit_kg, loaded_at = now()
    """
    values = [(r["site_id"], r["commodity"], r["as_of_utc"], r["qty_on_hand"], r["unit_kg"]) for r in rows]
    with conn.cursor() as cur:
        db.execute_values(cur, sql, values)
    return len(rows)


def upsert_demand(conn, rows: list[dict]) -> int:
    sql = """
        INSERT INTO demand (site_id, commodity, valid_from_utc, qty_needed, qty_delivered, priority, notes) VALUES %s
        ON CONFLICT (site_id, commodity, valid_from_utc) DO UPDATE SET qty_needed = EXCLUDED.qty_needed,
            qty_delivered = EXCLUDED.qty_delivered, priority = EXCLUDED.priority, notes = EXCLUDED.notes, loaded_at = now()
    """
    values = [(r["site_id"], r["commodity"], r["valid_from_utc"], r["qty_needed"], r["qty_delivered"], r["priority"], r["notes"]) for r in rows]
    with conn.cursor() as cur:
        db.execute_values(cur, sql, values)
    return len(rows)


def upsert_closures(conn, rows: list[dict]) -> int:
    sql = """
        INSERT INTO closures (closure_id, name, closure_type, start_utc, end_utc, time_confidence, osm_ref, source_note, geom) VALUES %s
        ON CONFLICT (closure_id) DO UPDATE SET name = EXCLUDED.name, closure_type = EXCLUDED.closure_type,
            start_utc = EXCLUDED.start_utc, end_utc = EXCLUDED.end_utc, time_confidence = EXCLUDED.time_confidence,
            osm_ref = EXCLUDED.osm_ref, source_note = EXCLUDED.source_note, geom = EXCLUDED.geom, loaded_at = now()
    """
    template = "(%s,%s,%s,%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326))"
    values = [(r["closure_id"], r["name"], r["closure_type"], r["start_utc"], r["end_utc"], r["time_confidence"], r["osm_ref"], r["source_note"], r["lon"], r["lat"]) for r in rows]
    with conn.cursor() as cur:
        from psycopg2.extras import execute_values
        execute_values(cur, sql, values, template=template, page_size=500)
    return len(rows)


def upsert_storm_track(conn, rows: list[dict]) -> int:
    sql = """
        INSERT INTO storm_track (atcf_id, time_utc, record_id, status, wind_kt, pressure_mb,
                                 r34_ne_nmi, r34_se_nmi, r34_sw_nmi, r34_nw_nmi, r64_ne_nmi, r64_se_nmi, r64_sw_nmi, r64_nw_nmi, geom)
        VALUES %s
        ON CONFLICT (atcf_id, time_utc, record_id) DO UPDATE SET status = EXCLUDED.status, wind_kt = EXCLUDED.wind_kt,
            pressure_mb = EXCLUDED.pressure_mb, r34_ne_nmi = EXCLUDED.r34_ne_nmi, r34_se_nmi = EXCLUDED.r34_se_nmi,
            r34_sw_nmi = EXCLUDED.r34_sw_nmi, r34_nw_nmi = EXCLUDED.r34_nw_nmi, r64_ne_nmi = EXCLUDED.r64_ne_nmi,
            r64_se_nmi = EXCLUDED.r64_se_nmi, r64_sw_nmi = EXCLUDED.r64_sw_nmi, r64_nw_nmi = EXCLUDED.r64_nw_nmi,
            geom = EXCLUDED.geom, loaded_at = now()
    """
    template = "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),4326))"
    values = [(r["atcf_id"], r["time_utc"], r["record_id"], r["status"], r["wind_kt"], r["pressure_mb"],
               r["r34_ne_nmi"], r["r34_se_nmi"], r["r34_sw_nmi"], r["r34_nw_nmi"], r["r64_ne_nmi"], r["r64_se_nmi"], r["r64_sw_nmi"], r["r64_nw_nmi"],
               r["lon"], r["lat"]) for r in rows]
    with conn.cursor() as cur:
        from psycopg2.extras import execute_values
        execute_values(cur, sql, values, template=template, page_size=500)
    return len(rows)


def upsert_roads(conn, rows: list[dict]) -> int:
    sql = """
        INSERT INTO roads (osm_id, highway, name, ref, oneway, lanes, maxspeed, bridge, tags, geom, length_m) VALUES %s
        ON CONFLICT (osm_id) DO UPDATE SET highway = EXCLUDED.highway, name = EXCLUDED.name, ref = EXCLUDED.ref,
            oneway = EXCLUDED.oneway, lanes = EXCLUDED.lanes, maxspeed = EXCLUDED.maxspeed, bridge = EXCLUDED.bridge,
            tags = EXCLUDED.tags, geom = EXCLUDED.geom, length_m = EXCLUDED.length_m, loaded_at = now()
    """
    # geometry arrives as GeoJSON text; PostGIS parses it, forces SRID 4326, and computes the geodesic length once.
    template = ("(%s,%s,%s,%s,%s,%s,%s,%s,%s, ST_SetSRID(ST_GeomFromGeoJSON(%s),4326), "
                "ST_Length(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)::geography))")
    values = []
    for r in rows:
        gj = json.dumps(r["geometry"])
        values.append((r["osm_id"], r["highway"], r["name"], r["ref"], r["oneway"], r["lanes"], r["maxspeed"], r["bridge"], Json(r["tags"]), gj, gj))
    with conn.cursor() as cur:
        from psycopg2.extras import execute_values
        execute_values(cur, sql, values, template=template, page_size=200)
    return len(rows)


def upsert_counties(conn, rows: list[dict]) -> int:
    sql = """
        INSERT INTO counties (county_fips, name, state_fips, geom) VALUES %s
        ON CONFLICT (county_fips) DO UPDATE SET name = EXCLUDED.name, state_fips = EXCLUDED.state_fips, geom = EXCLUDED.geom, loaded_at = now()
    """
    template = "(%s,%s,%s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s),4326)))"
    values = [(r["county_fips"], r["name"], r["state_fips"], json.dumps(r["geometry"])) for r in rows]
    with conn.cursor() as cur:
        from psycopg2.extras import execute_values
        execute_values(cur, sql, values, template=template, page_size=50)
    return len(rows)


def log_load(conn, stats: LoadStats) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO load_log (loader, source_path, rows_in, rows_upserted, rows_rejected) VALUES (%s,%s,%s,%s,%s)",
                    (stats.loader, stats.source_path, stats.rows_in, stats.rows_upserted, stats.rows_rejected))


# --------------------------------------------------------------------------------------------- orchestration

def load_everything(conn, inputs: dict | None = None, include_roads: bool = True) -> list[LoadStats]:
    """Load all inputs in dependency order. Returns per-loader stats. Writes data/clean/rejects.csv."""
    inputs = inputs or read_all_inputs()
    rejects = inputs["rejects"]

    def rej(source_prefix: str) -> int:
        return sum(1 for r in rejects if r.source.startswith(source_prefix))

    stats: list[LoadStats] = []
    n = upsert_sites(conn, inputs["sites"])
    stats.append(LoadStats("sites", inputs["labels"]["shelters"] + " + warehouses.csv", len(inputs["sites"]) + rej("hifld") + rej("warehouses"), n, rej("hifld") + rej("warehouses")))
    n = upsert_vehicles(conn, inputs["vehicles"])
    stats.append(LoadStats("vehicles", "data/synthetic/trucks.csv", len(inputs["vehicles"]) + rej("trucks"), n, rej("trucks")))
    n = upsert_inventory(conn, inputs["inventory"])
    stats.append(LoadStats("inventory", "data/synthetic/inventory.csv", len(inputs["inventory"]) + rej("inventory"), n, rej("inventory")))
    n = upsert_demand(conn, inputs["demand"])
    stats.append(LoadStats("demand", "data/synthetic/demand.csv", len(inputs["demand"]) + rej("demand"), n, rej("demand")))
    n = upsert_closures(conn, inputs["closures"])
    stats.append(LoadStats("closures", "data/synthetic/closures.csv", len(inputs["closures"]) + rej("closures"), n, rej("closures")))
    n = upsert_storm_track(conn, inputs["storm_track"])
    stats.append(LoadStats("storm_track", inputs["labels"]["storm_track"], len(inputs["storm_track"]) + rej("storm_track"), n, rej("storm_track")))
    if inputs["counties"]:
        n = upsert_counties(conn, inputs["counties"])
        stats.append(LoadStats("counties", inputs["labels"]["counties"], len(inputs["counties"]) + rej("counties"), n, rej("counties")))
    if include_roads and inputs["roads"]:
        n = upsert_roads(conn, inputs["roads"])
        stats.append(LoadStats("roads", inputs["labels"]["roads"], len(inputs["roads"]) + rej("roads"), n, rej("roads")))
    for s in stats:
        log_load(conn, s)
    cleaning.write_rejects(rejects)
    return stats

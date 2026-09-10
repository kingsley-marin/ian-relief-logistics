"""Loader cleaning: dirty rows are rejected and logged; clean rows are consistent."""
from __future__ import annotations

import csv

from ianrelief import cleaning, config
from ianrelief.loaders import read_csv

from conftest import utc

SYN = config.SYNTHETIC_DIR


def test_seeded_dirty_rows_are_rejected(inputs):
    reasons = {(r.source, r.key): r.reason for r in inputs["rejects"]}
    # warehouses.csv: missing coordinate + duplicate
    assert any(k[0] == "warehouses.csv" and "missing coordinate" in v for k, v in reasons.items())
    assert any(k[0] == "warehouses.csv" and "duplicate site_id" in v for k, v in reasons.items())
    # demand.csv: bad shelter id, duplicate, negative qty
    assert any(k[0] == "demand.csv" and "SH-999999" in k[1] and "not a known site" in v for k, v in reasons.items())
    assert any(k[0] == "demand.csv" and "duplicate" in v for k, v in reasons.items())
    assert any(k[0] == "demand.csv" and "non-negative" in v for k, v in reasons.items())
    # closures.csv: duplicate id, end before start
    assert any(k[0] == "closures.csv" and k[1] == "CL-SANIBEL-CSWY" and "duplicate" in v for k, v in reasons.items())
    assert any(k[0] == "closures.csv" and k[1] == "CL-BAD-WINDOW" and "end_utc must be after" in v for k, v in reasons.items())


def test_exactly_seven_synthetic_rejects_and_zero_from_public_data(inputs):
    synthetic = [r for r in inputs["rejects"] if r.source in ("warehouses.csv", "trucks.csv", "inventory.csv", "demand.csv", "closures.csv")]
    public = [r for r in inputs["rejects"] if r not in synthetic]
    assert len(synthetic) == 7, [f"{r.source}:{r.key}:{r.reason}" for r in synthetic]
    assert public == [], [f"{r.source}:{r.key}:{r.reason}" for r in public]


def test_rejects_file_is_written_with_expected_columns(inputs, tmp_path):
    path = tmp_path / "rejects.csv"
    n = cleaning.write_rejects(inputs["rejects"], path)
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    assert n == len(rows) == len(inputs["rejects"])
    assert list(rows[0].keys()) == cleaning.REJECT_COLUMNS


def test_no_duplicate_site_ids(inputs):
    ids = [s["site_id"] for s in inputs["sites"]]
    assert len(ids) == len(set(ids))


def test_site_ids_follow_scheme_and_crosswalk_to_source(inputs):
    for s in inputs["shelters"]:
        assert s["site_id"] == f"SH-{s['source_id']}"
        assert s["source_system"] == "HIFLD_NSS"
    for w in inputs["warehouses"]:
        assert w["site_id"].startswith("WH-")
        assert w["source_system"] == "SYNTHETIC"


def test_all_site_coordinates_are_lon_lat_degrees_in_study_area(inputs):
    """EPSG:4326 sanity: values are degrees (not metres), lon is negative (western hemisphere), inside SW Florida."""
    for s in inputs["sites"]:
        assert -90 <= s["lat"] <= 90 and -180 <= s["lon"] <= 180
        assert s["lon"] < 0 < s["lat"]
        assert cleaning.in_study_area(s["lat"], s["lon"]), s


def test_shelter_county_names_are_normalised(inputs):
    counties = {s["county"] for s in inputs["shelters"]}
    assert counties <= set(config.COUNTY_NAMES), counties
    assert "DeSoto" in counties or len(inputs["shelters"]) < 50  # sample may not include DeSoto


def test_demand_references_only_known_shelters(inputs):
    shelter_ids = {s["site_id"] for s in inputs["shelters"]}
    for d in inputs["demand"]:
        assert d["site_id"] in shelter_ids


def test_all_timestamps_are_utc_aware(inputs):
    for d in inputs["demand"]:
        assert d["valid_from_utc"].utcoffset().total_seconds() == 0
    for c in inputs["closures"]:
        assert c["start_utc"].utcoffset().total_seconds() == 0
        assert c["end_utc"] is None or c["end_utc"].utcoffset().total_seconds() == 0
    for t in inputs["storm_track"]:
        assert t["time_utc"].utcoffset().total_seconds() == 0


def test_parse_utc_rejects_naive_timestamps():
    assert cleaning.parse_utc("2022-09-29T12:00:00Z") == utc("2022-09-29T12:00:00Z")
    assert cleaning.parse_utc("2022-09-29T08:00:00-04:00") == utc("2022-09-29T12:00:00Z")
    assert cleaning.parse_utc("2022-09-29T12:00:00") is None
    assert cleaning.parse_utc("") is None
    assert cleaning.parse_utc("not a date") is None


def test_storm_track_contains_florida_landfall(inputs):
    landfalls = [t for t in inputs["storm_track"] if t["record_id"] == "L"]
    fl = [t for t in landfalls if t["time_utc"] == utc("2022-09-28T19:05:00Z")]
    assert len(fl) == 1
    assert fl[0]["wind_kt"] == 130 and 26.0 < fl[0]["lat"] < 27.0 and -82.5 < fl[0]["lon"] < -81.5


def test_clean_warehouses_unit_examples():
    rows, rejects = cleaning.clean_warehouses([
        {"site_id": "WH-A", "name": "A", "lat": "26.6", "lon": "-81.9"},
        {"site_id": "WH-B", "name": "B", "lat": "", "lon": "-81.9"},
        {"site_id": "WH-A", "name": "A again", "lat": "26.6", "lon": "-81.9"},
        {"site_id": "WH-C", "name": "Denver", "lat": "39.7", "lon": "-104.9"},
    ])
    assert [r["site_id"] for r in rows] == ["WH-A"]
    assert [r.reason for r in rejects] == ["missing coordinate", "duplicate site_id", "coordinate outside study area (39.7,-104.9)"]


def test_synthetic_csv_headers_match_data_contract():
    expect = {
        "warehouses.csv": ["site_id", "name", "city", "county", "lat", "lon", "notes"],
        "trucks.csv": ["vehicle_id", "vehicle_type", "capacity_kg", "capacity_pallets", "home_site_id", "available_from_utc", "notes"],
        "inventory.csv": ["site_id", "commodity", "as_of_utc", "qty_on_hand", "unit_kg", "notes"],
        "demand.csv": ["site_id", "hifld_shelter_id", "shelter_name_hint", "commodity", "valid_from_utc", "qty_needed", "qty_delivered", "priority", "notes"],
        "closures.csv": ["closure_id", "name", "closure_type", "start_utc", "end_utc", "time_confidence", "osm_ref", "lat", "lon", "source_note"],
    }
    for fname, cols in expect.items():
        rows = read_csv(SYN / fname)
        assert list(rows[0].keys()) == cols, fname

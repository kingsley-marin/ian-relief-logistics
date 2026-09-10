"""The first slice (offline path) and the allocation interface contract."""
from __future__ import annotations

from ianrelief import allocate, demo

from conftest import utc


def test_offline_demo_day_after_landfall(inputs):
    rows, active = demo.run_offline(inputs, utc("2022-09-29T12:00:00Z"))
    assert len(rows) == 20  # 21 clean demand rows, one (Turner Ag Center meals) not yet valid at this time
    unmet = {(r.site_id, r.commodity): r.qty_unmet for r in rows}
    assert unmet[("SH-134379", "meals_shelf_stable")] == 4500
    assert unmet[("SH-134369", "water_cases")] == 0  # fully served row shows zero, not dropped
    assert rows[0].priority == 1 and rows[0].qty_unmet == max(r.qty_unmet for r in rows if r.priority == 1)
    assert {c["closure_id"] for c in active} == {"CL-SANIBEL-CSWY", "CL-MATLACHA-PINE-ISLAND-RD", "CL-CAPE-CORAL-BRIDGES-INSPECT"}
    for r in rows:
        assert r.warehouse_id.startswith("WH-") and 0 < r.distance_km < 120


def test_offline_demo_respects_valid_from(inputs):
    rows, _ = demo.run_offline(inputs, utc("2022-09-30T12:00:00Z"))
    assert ("SH-352424", "meals_shelf_stable") in {(r.site_id, r.commodity) for r in rows}
    assert len(rows) == 21


def test_haversine_known_distance():
    # Fort Myers (26.64,-81.87) to Naples (26.14,-81.79): ~56 km
    assert 54 < demo.haversine_km(26.64, -81.87, 26.14, -81.79) < 58


def test_allocation_plan_contract(inputs):
    at = utc("2022-09-29T12:00:00Z")
    plan = allocate.allocate(allocate.AllocationInputs(inputs["sites"], inputs["vehicles"], inputs["inventory"], inputs["demand"], inputs["closures"], at, 12))
    assert plan.at == at
    assert plan.rows, "placeholder should assign something"
    vehicle_ids = {v["vehicle_id"] for v in inputs["vehicles"]}
    site_ids = {s["site_id"] for s in inputs["sites"]}
    for r in plan.rows:
        assert r.vehicle_id in vehicle_ids and r.warehouse_id in site_ids and r.shelter_id in site_ids
        assert r.qty > 0 and r.load_kg > 0
        assert r.explanation and r.shelter_id != r.warehouse_id
        assert "because" in r.explanation  # every row explains itself
    # truck capacity is never exceeded
    load_by_truck: dict[str, float] = {}
    for r in plan.rows:
        load_by_truck[r.vehicle_id] = load_by_truck.get(r.vehicle_id, 0) + r.load_kg
    cap = {v["vehicle_id"]: v["capacity_kg"] for v in inputs["vehicles"]}
    for vid, load in load_by_truck.items():
        assert load <= cap[vid] + 1e-6, (vid, load, cap[vid])
    # stock is never over-allocated
    assigned: dict[tuple[str, str], int] = {}
    for r in plan.rows:
        assigned[(r.warehouse_id, r.commodity)] = assigned.get((r.warehouse_id, r.commodity), 0) + r.qty
    stock = allocate.latest_inventory(inputs["inventory"], at)
    for k, q in assigned.items():
        assert q <= stock[k]["qty_on_hand"], (k, q)
    assert any("PLACEHOLDER" in n for n in plan.notes)


def test_allocation_unavailable_truck_is_not_used(inputs):
    # T-05 (reefer) becomes available at 12:00Z; at 06:00Z it must not appear and ice cannot be delivered
    at = utc("2022-09-29T06:00:00Z")
    plan = allocate.allocate(allocate.AllocationInputs(inputs["sites"], inputs["vehicles"], inputs["inventory"], inputs["demand"], inputs["closures"], at, 12))
    assert all(r.vehicle_id != "T-05" for r in plan.rows)
    assert any("T-05" in n for n in plan.notes)

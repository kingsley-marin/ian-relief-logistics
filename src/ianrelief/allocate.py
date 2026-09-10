"""Allocation engine — INTERFACE plus a naive placeholder.

# KINGSLEY: replace with OR-Tools VRP — see docs/DECISIONS.md ("Allocation engine") and docs/SCENARIO_TEMPLATE.md.

What is fixed (the contract the rest of the project relies on)
--------------------------------------------------------------
    allocate(inputs: AllocationInputs) -> AllocationPlan

    AllocationInputs (everything "as of" one instant, inputs.at):
        sites       list[dict]  shelters + warehouses (site_id, site_type, name, lat, lon, county, ...)
        vehicles    list[dict]  vehicle_id, capacity_kg, home_site_id, available_from_utc
        inventory   list[dict]  site_id, commodity, as_of_utc, qty_on_hand, unit_kg   (snapshot rows)
        demand      list[dict]  site_id, commodity, valid_from_utc, qty_needed, qty_delivered, priority
        closures    list[dict]  closure_id, start_utc, end_utc, lat, lon, ...
        at          datetime    the decision instant (UTC)
        horizon_h   float       planning horizon in hours

    AllocationPlan:
        rows        list[PlanRow]  one row per (vehicle, warehouse, shelter, commodity, qty)
        unserved    list[dict]     demand that the plan could not cover, with a reason
        notes       list[str]      plan-level explanations (what was assumed, what was ignored)

    PlanRow.explanation is a plain-English sentence a coordinator can read aloud. Every row must have one.
    That is the point of the tool: decisions people can check, not a black box.

What is deliberately NOT done here
----------------------------------
The placeholder below is greedy: highest-priority, largest unmet demand first; nearest warehouse by
straight-line distance that still has stock; first truck at that warehouse with capacity. It ignores
road distance, closures (except to mention them), multi-stop routes, time windows and re-supply. It exists
so `allocate()` returns something shaped correctly and the tests/UI have an object to hold.

Kingsley: the real engine is a capacitated vehicle routing problem with time windows over the road graph
minus active closures. docs/DECISIONS.md sketches how OR-Tools slots into this exact interface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import closures as closures_mod
from .demo import haversine_km


@dataclass
class AllocationInputs:
    sites: list[dict]
    vehicles: list[dict]
    inventory: list[dict]
    demand: list[dict]
    closures: list[dict]
    at: datetime
    horizon_h: float = 12.0


@dataclass
class PlanRow:
    vehicle_id: str
    warehouse_id: str
    shelter_id: str
    commodity: str
    qty: int
    load_kg: float
    distance_km: float
    priority: int
    explanation: str


@dataclass
class AllocationPlan:
    at: datetime
    rows: list[PlanRow] = field(default_factory=list)
    unserved: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def total_qty(self) -> int:
        return sum(r.qty for r in self.rows)


def latest_demand(demand: list[dict], at: datetime) -> list[dict]:
    """Most recent demand row per (site, commodity) valid at `at`, with qty_unmet attached."""
    latest: dict[tuple[str, str], dict] = {}
    for d in demand:
        if d["valid_from_utc"] <= at:
            k = (d["site_id"], d["commodity"])
            if k not in latest or d["valid_from_utc"] > latest[k]["valid_from_utc"]:
                latest[k] = d
    out = []
    for d in latest.values():
        row = dict(d)
        row["qty_unmet"] = max(0, d["qty_needed"] - d["qty_delivered"])
        out.append(row)
    return out


def latest_inventory(inventory: list[dict], at: datetime) -> dict[tuple[str, str], dict]:
    """Most recent inventory snapshot per (warehouse, commodity) at `at`. Mutable copy: the greedy pass decrements it."""
    latest: dict[tuple[str, str], dict] = {}
    for r in inventory:
        if r["as_of_utc"] <= at:
            k = (r["site_id"], r["commodity"])
            if k not in latest or r["as_of_utc"] > latest[k]["as_of_utc"]:
                latest[k] = dict(r)
    return latest


def allocate(inputs: AllocationInputs) -> AllocationPlan:
    """Naive greedy placeholder. # KINGSLEY: replace with OR-Tools VRP — see docs/DECISIONS.md"""
    plan = AllocationPlan(at=inputs.at)
    sites = {s["site_id"]: s for s in inputs.sites}
    warehouses = [s for s in inputs.sites if s["site_type"] == "warehouse"]
    stock = latest_inventory(inputs.inventory, inputs.at)
    trucks = [dict(v, remaining_kg=float(v["capacity_kg"])) for v in inputs.vehicles
              if v.get("available_from_utc") is None or v["available_from_utc"] <= inputs.at]
    unavailable = [v["vehicle_id"] for v in inputs.vehicles if v.get("available_from_utc") and v["available_from_utc"] > inputs.at]
    active = closures_mod.active_at(inputs.closures, inputs.at)

    plan.notes.append(f"GREEDY PLACEHOLDER — not an optimiser. Decision time {inputs.at.isoformat()}, horizon {inputs.horizon_h:g} h.")
    plan.notes.append("Distance is straight-line (haversine). Road network and closures are NOT used for reachability.")
    if active:
        plan.notes.append("Closures active but IGNORED by this placeholder: " + ", ".join(c["closure_id"] for c in active))
    if unavailable:
        plan.notes.append("Vehicles not yet available at decision time: " + ", ".join(unavailable))

    needs = [d for d in latest_demand(inputs.demand, inputs.at) if d["qty_unmet"] > 0 and sites.get(d["site_id"], {}).get("site_type") == "shelter"]
    needs.sort(key=lambda d: (d["priority"], -d["qty_unmet"], d["site_id"], d["commodity"]))

    for d in needs:
        shelter = sites[d["site_id"]]
        remaining = d["qty_unmet"]
        # warehouses that have any stock of this commodity, nearest first
        candidates = sorted(
            (w for w in warehouses if stock.get((w["site_id"], d["commodity"]), {}).get("qty_on_hand", 0) > 0),
            key=lambda w: haversine_km(shelter["lat"], shelter["lon"], w["lat"], w["lon"]),
        )
        if not candidates:
            plan.unserved.append({**d, "reason": f"no warehouse holds {d['commodity']} at {inputs.at.isoformat()}"})
            continue
        for w in candidates:
            if remaining <= 0:
                break
            inv = stock[(w["site_id"], d["commodity"])]
            unit_kg = float(inv.get("unit_kg") or 1.0)
            dist = haversine_km(shelter["lat"], shelter["lon"], w["lat"], w["lon"])
            for truck in (t for t in trucks if t["home_site_id"] == w["site_id"] and t["remaining_kg"] > unit_kg):
                if remaining <= 0 or inv["qty_on_hand"] <= 0:
                    break
                qty = int(min(remaining, inv["qty_on_hand"], truck["remaining_kg"] // unit_kg))
                if qty <= 0:
                    continue
                load = qty * unit_kg
                truck["remaining_kg"] -= load
                inv["qty_on_hand"] -= qty
                remaining -= qty
                plan.rows.append(PlanRow(
                    vehicle_id=truck["vehicle_id"], warehouse_id=w["site_id"], shelter_id=shelter["site_id"],
                    commodity=d["commodity"], qty=qty, load_kg=round(load, 1), distance_km=round(dist, 1), priority=d["priority"],
                    explanation=(f"Send {qty} {d['commodity']} from {w['name']} to {shelter['name']} on {truck['vehicle_id']} "
                                 f"({load:.0f} kg, {dist:.1f} km straight-line) because it is priority {d['priority']} with "
                                 f"{d['qty_unmet']} unmet and {w['site_id']} is the nearest warehouse with stock."),
                ))
        if remaining > 0:
            plan.unserved.append({**d, "qty_unserved": remaining,
                                  "reason": "stock or truck capacity exhausted at every warehouse holding this commodity"})

    plan.notes.append(f"{len(plan.rows)} plan rows, {plan.total_qty()} units assigned, {len(plan.unserved)} demand lines unserved.")
    return plan


def format_plan(plan: AllocationPlan) -> str:
    out = [f"Allocation plan as of {plan.at.isoformat()}", ""]
    for n in plan.notes:
        out.append(f"  * {n}")
    out.append("")
    out.append(f"{'vehicle':8s} {'from':6s} {'to':10s} {'commodity':19s} {'qty':>6s} {'kg':>8s} {'km':>6s} pri")
    for r in plan.rows:
        out.append(f"{r.vehicle_id:8s} {r.warehouse_id:6s} {r.shelter_id:10s} {r.commodity:19s} {r.qty:6d} {r.load_kg:8.0f} {r.distance_km:6.1f} {r.priority}")
    if plan.unserved:
        out.append("")
        out.append("Unserved:")
        for u in plan.unserved:
            out.append(f"  - {u['site_id']} {u['commodity']} unmet={u.get('qty_unserved', u['qty_unmet'])}: {u['reason']}")
    return "\n".join(out)


def horizon_end(inputs: AllocationInputs) -> datetime:
    return inputs.at + timedelta(hours=inputs.horizon_h)

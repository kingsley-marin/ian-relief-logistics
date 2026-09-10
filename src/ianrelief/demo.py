"""The first end-to-end slice.

Question answered: "At time T, which shelters still need what, and which warehouse is closest to each?"

Two implementations of the same question, so the answer can be cross-checked:
  * `run_sql(conn, t)`     — PostGIS: latest demand row per (shelter, commodity) as of T, unmet quantity,
                             nearest warehouse by ST_Distance on geography (metres, great-circle).
  * `run_offline(inputs, t)` — pure python from the same cleaned rows, haversine distance. Runs with no database.

This is straight-line distance. It is NOT routing, and it does NOT look at closures except to list the ones
active at T next to the table as a warning. Turning "nearest by distance" into "reachable by road given
closures" is the routing/allocation work Kingsley owns (docs/DECISIONS.md, src/ianrelief/allocate.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from . import closures as closures_mod

SQL_UNMET_NEAREST = """
WITH latest AS (
    -- the most recent demand row per (shelter, commodity) that was already valid at time T
    SELECT DISTINCT ON (d.site_id, d.commodity) d.*
    FROM demand d
    WHERE d.valid_from_utc <= %(t)s
    ORDER BY d.site_id, d.commodity, d.valid_from_utc DESC
),
unmet AS (
    SELECT l.site_id, s.name, s.county, l.commodity, l.priority,
           (l.qty_needed - l.qty_delivered) AS qty_unmet, s.geom
    FROM latest l
    JOIN sites s ON s.site_id = l.site_id
    WHERE s.site_type = 'shelter'
)
SELECT u.site_id, u.name, u.county, u.commodity, u.priority, u.qty_unmet,
       w.site_id AS warehouse_id, w.name AS warehouse_name,
       ST_Distance(u.geom::geography, w.geom::geography) / 1000.0 AS distance_km
FROM unmet u
CROSS JOIN LATERAL (
    SELECT s2.site_id, s2.name, s2.geom
    FROM sites s2
    WHERE s2.site_type = 'warehouse'
    ORDER BY ST_Distance(s2.geom::geography, u.geom::geography)
    LIMIT 1
) w
ORDER BY u.priority ASC, u.qty_unmet DESC, u.site_id, u.commodity;
"""

SQL_ACTIVE_CLOSURES = "SELECT closure_id, name, closure_type, start_utc, end_utc, time_confidence FROM closures_active_at(%(t)s) ORDER BY start_utc;"


@dataclass
class DemoRow:
    site_id: str
    name: str
    county: str | None
    commodity: str
    priority: int
    qty_unmet: int
    warehouse_id: str
    warehouse_name: str
    distance_km: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def run_sql(conn, t: datetime) -> tuple[list[DemoRow], list[dict]]:
    with conn.cursor() as cur:
        cur.execute(SQL_UNMET_NEAREST, {"t": t})
        rows = [DemoRow(*r[:8], float(r[8])) for r in cur.fetchall()]
        cur.execute(SQL_ACTIVE_CLOSURES, {"t": t})
        cols = [d[0] for d in cur.description]
        active = [dict(zip(cols, r)) for r in cur.fetchall()]
    return rows, active


def run_offline(inputs: dict, t: datetime) -> tuple[list[DemoRow], list[dict]]:
    sites = {s["site_id"]: s for s in inputs["sites"]}
    warehouses = [s for s in inputs["sites"] if s["site_type"] == "warehouse"]
    latest: dict[tuple[str, str], dict] = {}
    for d in inputs["demand"]:
        if d["valid_from_utc"] <= t:
            k = (d["site_id"], d["commodity"])
            if k not in latest or d["valid_from_utc"] > latest[k]["valid_from_utc"]:
                latest[k] = d
    rows: list[DemoRow] = []
    for (site_id, commodity), d in latest.items():
        s = sites.get(site_id)
        if not s or s["site_type"] != "shelter":
            continue
        best = min(warehouses, key=lambda w: haversine_km(s["lat"], s["lon"], w["lat"], w["lon"]))
        rows.append(DemoRow(site_id, s["name"], s["county"], commodity, d["priority"], d["qty_needed"] - d["qty_delivered"],
                            best["site_id"], best["name"], haversine_km(s["lat"], s["lon"], best["lat"], best["lon"])))
    rows.sort(key=lambda r: (r.priority, -r.qty_unmet, r.site_id, r.commodity))
    active = closures_mod.active_at(inputs["closures"], t)
    return rows, active


def format_table(rows: list[DemoRow], active: list[dict], t: datetime, mode: str) -> str:
    out = [f"Unmet shelter demand and nearest warehouse (straight line) as of {t.isoformat()}  [{mode}]", ""]
    hdr = f"{'shelter':10s} {'name':42s} {'county':9s} {'commodity':19s} {'pri':>3s} {'unmet':>6s}  {'nearest wh':10s} {'km':>6s}"
    out.append(hdr)
    out.append("-" * len(hdr))
    for r in rows:
        out.append(f"{r.site_id:10s} {r.name[:42]:42s} {(r.county or '')[:9]:9s} {r.commodity:19s} {r.priority:3d} {r.qty_unmet:6d}  {r.warehouse_id:10s} {r.distance_km:6.1f}")
    out.append("")
    out.append(f"{len(rows)} (shelter, commodity) rows; {sum(1 for r in rows if r.qty_unmet > 0)} with unmet demand.")
    out.append("")
    if active:
        out.append(f"Closures ACTIVE at this time ({len(active)}) — straight-line distance ignores them:")
        for c in active:
            end = c.get("end_utc")
            out.append(f"  - {c['closure_id']}: {c['name']} [{c['closure_type']}, {c['time_confidence']}] "
                       f"{c['start_utc'].isoformat()} -> {end.isoformat() if end else 'open-ended'}")
    else:
        out.append("No closures active at this time.")
    out.append("")
    out.append("Note: 'nearest' here is great-circle distance, not drive time or reachability. Routing + closures = next step.")
    return "\n".join(out)

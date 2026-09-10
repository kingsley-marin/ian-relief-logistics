"""Row validation shared by every loader. Pure python, no database, unit-tested.

Each `clean_*` function takes raw rows (dicts from csv.DictReader, or GeoJSON features) and returns
    (clean_rows, rejects)
where clean_rows are ready for the loader and rejects are `Reject` records explaining, per row, why
it was dropped. Loaders write the rejects to data/clean/rejects.csv so nothing disappears silently.

Rules applied
    * coordinates must parse, be finite, and fall inside the SW Florida sanity box (sites only)
    * keys must be unique (site_id, vehicle_id, closure_id, (site_id, commodity, as_of/valid_from))
    * foreign keys must resolve (demand/inventory/vehicles -> a known site_id)
    * quantities must be non-negative integers
    * timestamps must be ISO-8601 with an explicit UTC 'Z' or offset (naive timestamps are rejected —
      "was that local or UTC?" is exactly the bug we do not want in a disaster timeline)
    * closure windows must have end > start (or no end)
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import config

REJECT_COLUMNS = ["source", "row_number", "key", "reason", "raw"]


@dataclass
class Reject:
    source: str
    row_number: int
    key: str
    reason: str
    raw: str

    @classmethod
    def of(cls, source: str, row_number: int, key: Any, reason: str, raw: Any) -> "Reject":
        return cls(source, row_number, str(key), reason, json.dumps(raw, default=str)[:2000])


# --------------------------------------------------------------------------------------------- helpers

def parse_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp that carries a timezone. Returns None if missing/naive/unparseable."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def parse_int(value: Any) -> int | None:
    f = parse_float(value)
    if f is None or f != int(f):
        return None
    return int(f)


def in_study_area(lat: float, lon: float) -> bool:
    b = config.STUDY_AREA_BBOX
    return b["south"] <= lat <= b["north"] and b["west"] <= lon <= b["east"]


def _yn(value: Any) -> bool | None:
    if value is None:
        return None
    s = str(value).strip().upper()
    if s in ("Y", "YES", "TRUE", "T", "1"):
        return True
    if s in ("N", "NO", "FALSE", "F", "0", "NONE"):
        return False
    return None


# --------------------------------------------------------------------------------------------- sites

def shelter_site_id(shelter_id: Any) -> str:
    return f"SH-{int(shelter_id)}"


def clean_shelters(features: Iterable[dict], source: str = "hifld_shelters.geojson") -> tuple[list[dict], list[Reject]]:
    """HIFLD/FEMA NSS GeoJSON features -> site rows (site_type='shelter')."""
    rows: list[dict] = []
    rejects: list[Reject] = []
    seen: set[str] = set()
    for i, f in enumerate(features, start=1):
        p = f.get("properties") or {}
        geom = f.get("geometry") or {}
        sid_raw = p.get("shelter_id")
        if parse_int(sid_raw) is None:
            rejects.append(Reject.of(source, i, sid_raw, "missing or non-integer shelter_id", p))
            continue
        site_id = shelter_site_id(sid_raw)
        if site_id in seen:
            rejects.append(Reject.of(source, i, site_id, "duplicate shelter_id", p))
            continue
        coords = geom.get("coordinates") if geom.get("type") == "Point" else None
        lon = parse_float(coords[0]) if coords else parse_float(p.get("longitude"))
        lat = parse_float(coords[1]) if coords else parse_float(p.get("latitude"))
        if lat is None or lon is None:
            rejects.append(Reject.of(source, i, site_id, "missing coordinate", p))
            continue
        if not in_study_area(lat, lon):
            rejects.append(Reject.of(source, i, site_id, f"coordinate outside study area ({lat},{lon})", p))
            continue
        name = (p.get("shelter_name") or "").strip()
        if not name:
            rejects.append(Reject.of(source, i, site_id, "missing shelter_name", p))
            continue
        seen.add(site_id)
        pets = (p.get("pet_accommodations_code") or "").strip().upper()
        rows.append({
            "site_id": site_id,
            "site_type": "shelter",
            "name": name,
            "address": p.get("address_1"),
            "city": (p.get("city") or "").strip().title() or None,
            "county": p.get("county_norm") or config.normalize_county(p.get("county_parish")),
            "county_fips": p.get("fips_code"),
            "state": p.get("state") or "FL",
            "source_system": p.get("source_system") or "HIFLD_NSS",
            "source_id": str(int(sid_raw)),
            "evacuation_capacity": parse_int(p.get("evacuation_capacity")),
            "post_impact_capacity": parse_int(p.get("post_impact_capacity")),
            "pet_friendly": (True if pets and pets not in ("NONE", "N", "NO") else (False if pets else ("PET" in name.upper() or None))),
            "generator_onsite": _yn(p.get("generator_onsite")),
            "in_surge_zone": _yn(p.get("in_surge_slosh_area")),
            "attributes": {k: v for k, v in p.items() if v is not None},
            "lat": lat,
            "lon": lon,
        })
    return rows, rejects


def clean_warehouses(raw_rows: Iterable[dict], source: str = "warehouses.csv") -> tuple[list[dict], list[Reject]]:
    rows: list[dict] = []
    rejects: list[Reject] = []
    seen: set[str] = set()
    for i, r in enumerate(raw_rows, start=2):  # header is line 1
        site_id = (r.get("site_id") or "").strip()
        if not site_id:
            rejects.append(Reject.of(source, i, "", "missing site_id", r))
            continue
        if site_id in seen:
            rejects.append(Reject.of(source, i, site_id, "duplicate site_id", r))
            continue
        lat, lon = parse_float(r.get("lat")), parse_float(r.get("lon"))
        if lat is None or lon is None:
            rejects.append(Reject.of(source, i, site_id, "missing coordinate", r))
            continue
        if not in_study_area(lat, lon):
            rejects.append(Reject.of(source, i, site_id, f"coordinate outside study area ({lat},{lon})", r))
            continue
        seen.add(site_id)
        rows.append({
            "site_id": site_id,
            "site_type": "warehouse",
            "name": (r.get("name") or site_id).strip(),
            "address": None,
            "city": r.get("city"),
            "county": config.normalize_county(r.get("county")),
            "county_fips": None,
            "state": "FL",
            "source_system": "SYNTHETIC",
            "source_id": site_id,
            "evacuation_capacity": None,
            "post_impact_capacity": None,
            "pet_friendly": None,
            "generator_onsite": None,
            "in_surge_zone": None,
            "attributes": {"notes": r.get("notes")},
            "lat": lat,
            "lon": lon,
        })
    return rows, rejects


# --------------------------------------------------------------------------------------------- vehicles / inventory / demand

def clean_vehicles(raw_rows: Iterable[dict], known_sites: set[str], source: str = "trucks.csv") -> tuple[list[dict], list[Reject]]:
    rows: list[dict] = []
    rejects: list[Reject] = []
    seen: set[str] = set()
    for i, r in enumerate(raw_rows, start=2):
        vid = (r.get("vehicle_id") or "").strip()
        if not vid:
            rejects.append(Reject.of(source, i, "", "missing vehicle_id", r))
            continue
        if vid in seen:
            rejects.append(Reject.of(source, i, vid, "duplicate vehicle_id", r))
            continue
        cap = parse_int(r.get("capacity_kg"))
        if cap is None or cap <= 0:
            rejects.append(Reject.of(source, i, vid, "capacity_kg must be a positive integer", r))
            continue
        home = (r.get("home_site_id") or "").strip() or None
        if home and home not in known_sites:
            rejects.append(Reject.of(source, i, vid, f"home_site_id {home} is not a known site", r))
            continue
        avail = parse_utc(r.get("available_from_utc"))
        if r.get("available_from_utc") and avail is None:
            rejects.append(Reject.of(source, i, vid, "available_from_utc is not a timezone-aware ISO timestamp", r))
            continue
        seen.add(vid)
        rows.append({
            "vehicle_id": vid,
            "vehicle_type": (r.get("vehicle_type") or "unknown").strip(),
            "capacity_kg": cap,
            "capacity_pallets": parse_int(r.get("capacity_pallets")),
            "home_site_id": home,
            "available_from_utc": avail,
            "notes": r.get("notes"),
        })
    return rows, rejects


def clean_inventory(raw_rows: Iterable[dict], known_sites: set[str], source: str = "inventory.csv") -> tuple[list[dict], list[Reject]]:
    rows: list[dict] = []
    rejects: list[Reject] = []
    seen: set[tuple] = set()
    for i, r in enumerate(raw_rows, start=2):
        site_id = (r.get("site_id") or "").strip()
        commodity = (r.get("commodity") or "").strip()
        as_of = parse_utc(r.get("as_of_utc"))
        key = (site_id, commodity, r.get("as_of_utc"))
        if not site_id or not commodity:
            rejects.append(Reject.of(source, i, key, "missing site_id or commodity", r))
            continue
        if site_id not in known_sites:
            rejects.append(Reject.of(source, i, key, f"site_id {site_id} is not a known site", r))
            continue
        if as_of is None:
            rejects.append(Reject.of(source, i, key, "as_of_utc is not a timezone-aware ISO timestamp", r))
            continue
        qty = parse_int(r.get("qty_on_hand"))
        if qty is None or qty < 0:
            rejects.append(Reject.of(source, i, key, "qty_on_hand must be a non-negative integer", r))
            continue
        k = (site_id, commodity, as_of)
        if k in seen:
            rejects.append(Reject.of(source, i, key, "duplicate (site_id, commodity, as_of_utc)", r))
            continue
        seen.add(k)
        rows.append({"site_id": site_id, "commodity": commodity, "as_of_utc": as_of, "qty_on_hand": qty,
                     "unit_kg": parse_float(r.get("unit_kg")), "notes": r.get("notes")})
    return rows, rejects


def clean_demand(raw_rows: Iterable[dict], known_sites: set[str], source: str = "demand.csv") -> tuple[list[dict], list[Reject]]:
    rows: list[dict] = []
    rejects: list[Reject] = []
    seen: set[tuple] = set()
    for i, r in enumerate(raw_rows, start=2):
        site_id = (r.get("site_id") or "").strip()
        commodity = (r.get("commodity") or "").strip()
        key = (site_id, commodity, r.get("valid_from_utc"))
        if not site_id or not commodity:
            rejects.append(Reject.of(source, i, key, "missing site_id or commodity", r))
            continue
        if site_id not in known_sites:
            rejects.append(Reject.of(source, i, key, f"site_id {site_id} is not a known site (bad shelter id?)", r))
            continue
        valid_from = parse_utc(r.get("valid_from_utc"))
        if valid_from is None:
            rejects.append(Reject.of(source, i, key, "valid_from_utc is not a timezone-aware ISO timestamp", r))
            continue
        needed, delivered = parse_int(r.get("qty_needed")), parse_int(r.get("qty_delivered") or 0)
        if needed is None or needed < 0 or delivered is None or delivered < 0:
            rejects.append(Reject.of(source, i, key, "qty_needed/qty_delivered must be non-negative integers", r))
            continue
        priority = parse_int(r.get("priority") or 3)
        if priority is None or not 1 <= priority <= 5:
            rejects.append(Reject.of(source, i, key, "priority must be 1..5", r))
            continue
        k = (site_id, commodity, valid_from)
        if k in seen:
            rejects.append(Reject.of(source, i, key, "duplicate (site_id, commodity, valid_from_utc)", r))
            continue
        seen.add(k)
        rows.append({"site_id": site_id, "commodity": commodity, "valid_from_utc": valid_from, "qty_needed": needed,
                     "qty_delivered": delivered, "priority": priority, "notes": r.get("notes")})
    return rows, rejects


# --------------------------------------------------------------------------------------------- closures

VALID_CLOSURE_TYPES = {"bridge_collapse", "washout", "flooding", "debris", "inspection", "other"}
VALID_CONFIDENCE = {"reported", "approximate", "assumed"}


def clean_closures(raw_rows: Iterable[dict], source: str = "closures.csv") -> tuple[list[dict], list[Reject]]:
    rows: list[dict] = []
    rejects: list[Reject] = []
    seen: set[str] = set()
    for i, r in enumerate(raw_rows, start=2):
        cid = (r.get("closure_id") or "").strip()
        if not cid:
            rejects.append(Reject.of(source, i, "", "missing closure_id", r))
            continue
        if cid in seen:
            rejects.append(Reject.of(source, i, cid, "duplicate closure_id", r))
            continue
        start = parse_utc(r.get("start_utc"))
        end = parse_utc(r.get("end_utc")) if (r.get("end_utc") or "").strip() else None
        if start is None:
            rejects.append(Reject.of(source, i, cid, "start_utc is not a timezone-aware ISO timestamp", r))
            continue
        if (r.get("end_utc") or "").strip() and end is None:
            rejects.append(Reject.of(source, i, cid, "end_utc is not a timezone-aware ISO timestamp", r))
            continue
        if end is not None and end <= start:
            rejects.append(Reject.of(source, i, cid, "end_utc must be after start_utc", r))
            continue
        ctype = (r.get("closure_type") or "other").strip()
        if ctype not in VALID_CLOSURE_TYPES:
            rejects.append(Reject.of(source, i, cid, f"closure_type {ctype!r} not in {sorted(VALID_CLOSURE_TYPES)}", r))
            continue
        conf = (r.get("time_confidence") or "").strip()
        if conf not in VALID_CONFIDENCE:
            rejects.append(Reject.of(source, i, cid, f"time_confidence {conf!r} not in {sorted(VALID_CONFIDENCE)}", r))
            continue
        lat, lon = parse_float(r.get("lat")), parse_float(r.get("lon"))
        if lat is None or lon is None or not in_study_area(lat, lon):
            rejects.append(Reject.of(source, i, cid, "missing coordinate or outside study area", r))
            continue
        if not (r.get("source_note") or "").strip():
            rejects.append(Reject.of(source, i, cid, "source_note is required for every closure", r))
            continue
        seen.add(cid)
        rows.append({"closure_id": cid, "name": (r.get("name") or cid).strip(), "closure_type": ctype, "start_utc": start,
                     "end_utc": end, "time_confidence": conf, "osm_ref": (r.get("osm_ref") or "").strip() or None,
                     "source_note": r.get("source_note").strip(), "lat": lat, "lon": lon})
    return rows, rejects


# --------------------------------------------------------------------------------------------- roads / track / counties

def clean_roads(features: Iterable[dict], source: str = "roads.geojson") -> tuple[list[dict], list[Reject]]:
    rows: list[dict] = []
    rejects: list[Reject] = []
    seen: set[int] = set()
    for i, f in enumerate(features, start=1):
        p = f.get("properties") or {}
        g = f.get("geometry") or {}
        osm_id = parse_int(p.get("osm_id"))
        if osm_id is None:
            rejects.append(Reject.of(source, i, p.get("osm_id"), "missing osm_id", p))
            continue
        if osm_id in seen:
            rejects.append(Reject.of(source, i, osm_id, "duplicate osm_id", p))
            continue
        if g.get("type") != "LineString" or len(g.get("coordinates") or []) < 2:
            rejects.append(Reject.of(source, i, osm_id, "geometry is not a LineString with >= 2 points", p))
            continue
        seen.add(osm_id)
        lanes = parse_int(p.get("lanes"))
        rows.append({
            "osm_id": osm_id,
            "highway": p.get("highway") or "unknown",
            "name": p.get("name"),
            "ref": p.get("ref"),
            "oneway": _yn(p.get("oneway")),
            "lanes": lanes if lanes is not None and 0 < lanes < 32 else None,
            "maxspeed": p.get("maxspeed"),
            "bridge": _yn(p.get("bridge")) if p.get("bridge") not in ("viaduct", "movable") else True,
            "tags": p.get("tags") or {},
            "geometry": g,
        })
    return rows, rejects


def clean_storm_track(raw_rows: Iterable[dict], atcf_id: str = config.STORM_ATCF_ID, source: str = "storm_track.csv") -> tuple[list[dict], list[Reject]]:
    rows: list[dict] = []
    rejects: list[Reject] = []
    seen: set[tuple] = set()
    for i, r in enumerate(raw_rows, start=2):
        t = parse_utc(r.get("time_utc"))
        rec = (r.get("record_id") or "").strip()
        if t is None:
            rejects.append(Reject.of(source, i, r.get("time_utc"), "time_utc is not a timezone-aware ISO timestamp", r))
            continue
        lat, lon = parse_float(r.get("lat")), parse_float(r.get("lon"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            rejects.append(Reject.of(source, i, r.get("time_utc"), "bad lat/lon", r))
            continue
        k = (t, rec)
        if k in seen:
            rejects.append(Reject.of(source, i, r.get("time_utc"), "duplicate (time_utc, record_id)", r))
            continue
        seen.add(k)
        row = {"atcf_id": atcf_id, "time_utc": t, "record_id": rec, "status": r.get("status"),
               "wind_kt": parse_int(r.get("wind_kt")), "pressure_mb": parse_int(r.get("pressure_mb")), "lat": lat, "lon": lon}
        for col in ("r34_ne_nmi", "r34_se_nmi", "r34_sw_nmi", "r34_nw_nmi", "r64_ne_nmi", "r64_se_nmi", "r64_sw_nmi", "r64_nw_nmi"):
            row[col] = parse_int(r.get(col))
        rows.append(row)
    return rows, rejects


def clean_counties(features: Iterable[dict], source: str = "counties.geojson") -> tuple[list[dict], list[Reject]]:
    rows: list[dict] = []
    rejects: list[Reject] = []
    for i, f in enumerate(features, start=1):
        p = f.get("properties") or {}
        g = f.get("geometry") or {}
        fips = str(p.get("GEOID") or p.get("county_fips") or "").strip()
        if len(fips) != 5:
            rejects.append(Reject.of(source, i, fips, "GEOID must be a 5-digit county FIPS", p))
            continue
        if g.get("type") not in ("Polygon", "MultiPolygon"):
            rejects.append(Reject.of(source, i, fips, "geometry must be Polygon/MultiPolygon", p))
            continue
        rows.append({"county_fips": fips, "name": p.get("BASENAME") or p.get("NAME") or fips, "state_fips": fips[:2], "geometry": g})
    return rows, rejects


# --------------------------------------------------------------------------------------------- rejects file

def write_rejects(rejects: Iterable[Reject], path: Path = config.REJECTS_PATH) -> int:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    rejects = list(rejects)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REJECT_COLUMNS)
        w.writeheader()
        for rj in rejects:
            w.writerow(asdict(rj))
    return len(rejects)

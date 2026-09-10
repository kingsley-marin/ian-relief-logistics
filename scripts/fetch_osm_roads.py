#!/usr/bin/env python3
"""Fetch drivable major roads around Fort Myers / Cape Coral / Punta Gorda from OpenStreetMap.

Why Overpass and not Geofabrik
------------------------------
Geofabrik's florida-latest.osm.pbf is ~300 MB and needs osmium/pyosmium/ogr2ogr to read. For a
scaffold, a bounded Overpass query (highway = motorway..tertiary inside config/counties.yaml
roads_bbox) returns ~8-9k ways / ~8 MB JSON in a few seconds, with no extra tooling.

Caveats (read these before hammering the button)
-----------------------------------------------
* Overpass is a free shared service. One request per run; cached under data/raw; polite UA. If you
  get HTTP 429/504, wait a few minutes; or set IAN_OVERPASS_URL to another public instance.
* Residential/service roads are excluded on purpose (they multiply size ~5x). Add them to
  roads_highway_classes if the routing work needs them.
* The data is TODAY's OSM, not September 2022. The Sanibel Causeway and the Matlacha bridge exist in
  OSM now (they were rebuilt). That is exactly why closures.csv exists: passability at time T is a
  scenario input, not something the map knows.
* License: ODbL 1.0 (© OpenStreetMap contributors). Attribution is required in any UI.

If the region grows past what Overpass will serve, switch to Geofabrik:
    curl -O https://download.geofabrik.de/north-america/us/florida-latest.osm.pbf
    osmium extract -b W,S,E,N florida-latest.osm.pbf -o swfl.osm.pbf
    ogr2ogr -f GeoJSON roads.geojson swfl.osm.pbf lines -where "highway IS NOT NULL"

Output: data/clean/roads.geojson (LineString features with osm_id + selected tags).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import config, http  # noqa: E402

RAW_NAME = "osm_roads_overpass.json"
CLEAN_PATH = config.CLEAN_DIR / "roads.geojson"
KEEP_TAGS = ["highway", "name", "ref", "oneway", "lanes", "maxspeed", "bridge", "tunnel", "surface", "access"]


def build_query() -> str:
    b = config.ROADS_BBOX
    classes = "|".join(config.ROADS_HIGHWAY_CLASSES)
    return (
        f"[out:json][timeout:120];"
        f'way["highway"~"^({classes})$"]({b["south"]},{b["west"]},{b["north"]},{b["east"]});'
        f"out geom tags;"
    )


def to_geojson(overpass_doc: dict) -> dict:
    feats = []
    for el in overpass_doc.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = [[pt["lon"], pt["lat"]] for pt in el["geometry"]]
        if len(coords) < 2:
            continue
        tags = el.get("tags", {})
        props = {"osm_id": el["id"]}
        for k in KEEP_TAGS:
            props[k] = tags.get(k)
        props["tags"] = tags
        feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": props})
    return {
        "type": "FeatureCollection",
        "name": "osm_roads_study_bbox",
        "source": "OpenStreetMap via Overpass API",
        "license": "ODbL 1.0 — © OpenStreetMap contributors",
        "bbox": [config.ROADS_BBOX["west"], config.ROADS_BBOX["south"], config.ROADS_BBOX["east"], config.ROADS_BBOX["north"]],
        "osm3s": overpass_doc.get("osm3s", {}),
        "features": feats,
    }


def main(force: bool = False) -> int:
    config.ensure_dirs()
    print(f"OSM roads via Overpass -> {CLEAN_PATH.relative_to(config.REPO_ROOT)}")
    res = http.fetch(RAW_NAME, config.OVERPASS_URL, method="POST", data={"data": build_query()}, force=force, timeout=180, retries=2, backoff_s=30)
    if not res.ok:
        print(f"  FAILED: {res.error}\n  (Overpass is rate-limited; wait a few minutes and retry, or see the Geofabrik note in this script)")
        return 1
    doc = json.loads(res.path.read_text(encoding="utf-8"))
    gj = to_geojson(doc)
    CLEAN_PATH.write_text(json.dumps(gj), encoding="utf-8")
    counts: dict[str, int] = {}
    for f in gj["features"]:
        h = f["properties"]["highway"]
        counts[h] = counts.get(h, 0) + 1
    print(f"  {len(gj['features'])} ways ({res.bytes/1e6:.1f} MB raw, {'cache' if res.from_cache else 'live'}); OSM data timestamp: {gj['osm3s'].get('timestamp_osm_base')}")
    print(f"  by class: {dict(sorted(counts.items()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(force="--force" in sys.argv))

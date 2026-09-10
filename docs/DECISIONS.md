# Decisions log

Data judgments and design decisions, dated, with the alternative that was not taken. Append; do not rewrite
history. Raphael's entries cover the plumbing (2026-09-09). Kingsley's entries start at the "Allocation
engine" section and everything after.

---

## 2026-09-09 — Plumbing decisions (Raphael)

### D1. Study area = five counties, but roads only for a bbox around Fort Myers / Cape Coral / Punta Gorda
Shelters, counties and FEMA declarations cover Lee, Charlotte, Collier, Sarasota, DeSoto (the counties
under the eyewall or the surge). Roads are pulled for a smaller bbox (26.40–27.10 N, 82.35–81.60 W) because
Overpass is a shared free service and residential roads multiply size ~5x. Consequence: Naples/Golden Gate
(Collier) and Arcadia (DeSoto) shelters are *outside* the road bbox. Widen `roads_bbox` in
`config/counties.yaml` when routing needs them, or switch to Geofabrik (`scripts/fetch_osm_roads.py` docstring).

### D2. HIFLD shelter endpoint moved; we use FEMA's NSS service directly
The commonly cited `services1.arcgis.com/Hp6G80Pky0om7QvQ/.../National_Shelter_System_Facilities` returns
HTTP 400 "Invalid URL" (checked 2026-09-09). HIFLD Open's shelter layer is served from
`gis.fema.gov/arcgis/rest/services/NSS/FEMA_NSS/FeatureServer/5` ("Shelter Locations"). Same dataset, same
schema (`shelter_id`, `shelter_name`, `county_parish`, `evacuation_capacity`, …). 304 features in the five
counties on the fetch date (Lee 99, Collier 72, Sarasota 61, Charlotte 52, DeSoto 20).

### D3. HIFLD is a CURRENT registry, not the September 2022 state
Every row's `shelter_status_code` is `CLOSED` (no active incident). Shelters that opened for Ian may have
been dropped; new ones added. We treat the table as "candidate shelter locations" and hand-pick nine that
were widely reported as open Ian shelters (Hertz Arena, South Fort Myers HS, Island Coast HS, North Fort
Myers Academy for the Arts, Estero Rec Center, Port Charlotte HS, Kingsway Elementary, Golden Gate HS,
North Port HS) plus Turner Ag Center in Arcadia for `demand.csv`. Kingsley: if you find the Lee County /
Charlotte County EOC shelter lists for 2022-09-27..30, replace this selection and cite them.

### D4. County names are spelled five ways; we normalise
`county_parish` arrives as `LEE`, `Lee`, `DE SOTO`, `DeSoto`, `Desoto`, … We query all spellings and
normalise to `Lee, Charlotte, Collier, Sarasota, DeSoto`. The raw value is preserved in `attributes`.

### D5. HIFLD has near-duplicate facilities — kept, not merged
The same building appears under several `shelter_id`s with name variants (e.g. Kingsway Elementary:
134236 / 366405 / 366505; Harns Marsh Middle: 355784 / 366533). We do NOT merge them: `site_id` is one
per source id so the crosswalk stays honest. A future "facility" concept could group them. Alternative
rejected: fuzzy-merge on name+distance — risky to do silently in a scaffold.

### D6. Roads: Overpass, not Geofabrik; today's map, not 2022's
Geofabrik's Florida extract is ~300 MB and needs osmium/GDAL. Overpass returned 9,757 ways (9 MB JSON)
in ~3 s for our bbox and classes. OSM today shows the rebuilt Sanibel Causeway and Matlacha bridge; the
2022 outages therefore live in `closures.csv`, not in the road geometry. Attribution: © OpenStreetMap
contributors, ODbL.

### D7. Storm track from HURDAT2 text, not the NHC GIS shapefile zip
HURDAT2 is a plain text file, is the authoritative post-season best track, and needs no shapefile reader.
The fetcher picks the newest `hurdat2-1851-YYYY-*.txt` on the index page (currently the 2025-season
release dated 2026-02-27) so it survives annual re-releases. 40 fixes for AL092022 incl. five landfall
records (two in Cuba, Cayo Costa FL 19:05Z 130 kt / 941 mb, Punta Gorda area 20:35Z, South Carolina 09-30).

### D8. County boundaries from TIGERweb REST, not the TIGER shapefile
No pyshp/fiona/GDAL in the toolchain; TIGERweb returns the five polygons as GeoJSON (220 KB). It tracks
the newest vintage, which is fine for a context layer. For archival reproducibility use the shapefile.

### D9. OpenFEMA: keep every designated-area row
DR-4673-FL has 69 designated-area rows (counties + tribal areas x declaration programs). We store all
and flag `in_study_area`. Not loaded into a table yet — it is context, not a decision input.

### D10. GIBS: layer ids verified against capabilities; Black Marble rejected
`VIIRS_Black_Marble` exists but only for 2012 and 2016 (annual composites) — it cannot show Ian. Daily
`VIIRS_SNPP_DayNightBand_At_Sensor_Radiance` (+ `_ENCC`) is the honest substitute for "lights out".
`MODIS_Combined_Flood_2-Day` is the best flood layer covering 2022 (the VIIRS flood layers start 2025).
All nine configured layers returned HTTP 200 image tiles for 2022-09-28 over Fort Myers (`make gibs-check`).
Tile maths uses ScaleDenominator (GIBS EPSG:4326 matrices are 2/3/5/10/20… tiles wide, not powers of two).

### D11. Closures are reconstructed and say so
Four closures: Sanibel Causeway (collapse, ~09-28 20Z → 10-19 14Z), Pine Island Rd/Matlacha (washout,
~09-28 20Z → 10-05 22Z), I-75 at the Myakka River (flooding, ~10-01 12Z → 10-02 20Z), Cape Coral bridges
(inspection, `assumed`). Start hours are set to ~1 h after landfall where the exact failure time is not
public. Each row carries `time_confidence` and a `source_note`; the `assumed` row is an explicit placeholder
for Kingsley to verify or delete. Alternative rejected: leaving closures out until sourced — the schema and
the demo need something to show.

### D12. Site ids are ours; source ids are kept
`SH-<shelter_id>`, `WH-nn`. A crosswalk table maps `(source_system, source_id) -> site_id`. This lets a
second source (Red Cross, county EOC) attach to the same site later without renumbering.

### D13. Timestamps: UTC only, tz-aware or rejected
Landfall 15:05 EDT = 19:05 UTC. Every CSV timestamp must carry `Z`/offset; the cleaner rejects naive ones.
Half-open windows `[start, end)` for closures.

### D14. Dirty rows are seeded on purpose
`warehouses.csv` (missing lon, duplicate), `demand.csv` (unknown shelter, duplicate, negative qty),
`closures.csv` (duplicate id, end before start). The loader must catch all seven; `tests/test_cleaning.py`
asserts exactly seven synthetic rejects and zero from public data.

### D15. Demo = straight-line nearest warehouse, deliberately
`make demo` proves the pipeline end to end (fetch → clean → load → spatial query → table). It uses
`ST_Distance(geography)` and lists active closures beside the table as a warning. Island Coast HS (Cape
Coral) comes out "nearest" to the Punta Gorda warehouse at 22 km straight-line — by road that trip crosses
the Caloosahatchee or loops via US-41, which is exactly the gap the routing work must close.

### D16. Docker was not available on the build machine
The PostGIS schema, loaders and SQL demo were written and reviewed but NOT executed against a live
database on 2026-09-09. The 7 `db`-marked tests skip with the connection error. First thing to do on a
machine with Docker: `make up && make load && make demo && make test-db`, and fix whatever breaks.

---

## Allocation engine (Kingsley owns this section)

### Where the engine slots in
`src/ianrelief/allocate.py` fixes the contract:

```
allocate(AllocationInputs(sites, vehicles, inventory, demand, closures, at, horizon_h)) -> AllocationPlan
AllocationPlan.rows: list[PlanRow(vehicle_id, warehouse_id, shelter_id, commodity, qty, load_kg, distance_km, priority, explanation)]
AllocationPlan.unserved, AllocationPlan.notes
```
Keep the signature; replace the body. Everything else (tests, future UI) depends only on the shapes.

### How OR-Tools would fit (sketch, not a spec)
1. **Build the travel-time matrix at time T.** Nodes = warehouses + shelters with unmet demand. Edges = road
   network (`roads` table) with active closures removed. Options, cheapest first:
   - pgRouting on the `roads` table (`pgr_createTopology`, then `pgr_dijkstraCostMatrix`), deleting/penalising
     edges within N metres of an active closure point (`closures_active_at(T)`).
   - OSRM/Valhalla with a closure-aware profile (heavier to set up, better routes).
   - Straight-line × a detour factor (what the placeholder does) — only as a fallback.
2. **Model it as a CVRP with time windows and heterogeneous fleet** using
   `ortools.constraint_solver.pywrapcp.RoutingModel`:
   - one vehicle per truck, start/end at `home_site_id`, capacity dimension in kg (and/or pallets),
   - demand per shelter node = unmet qty × `unit_kg` (per commodity; multi-commodity = either one node per
     (shelter, commodity) or a dimension per commodity),
   - time dimension from the matrix + `loading_minutes`/`unloading_minutes` from `scenario.yaml`,
   - vehicle start time = `available_from_utc`, horizon = `planning_horizon_hours`,
   - drop penalties per node scaled by `priority` (cheap to drop priority 5, expensive to drop priority 1),
   - warehouse stock as a global constraint (pickups-and-deliveries, or pre-assign shelters to warehouses).
3. **Objective** = the one written in `scenario.yaml` (e.g. minimise total drop penalty, then total time).
4. **Translate the solution back** into `PlanRow`s. The `explanation` string should cite the actual reasons:
   priority, unmet qty, drive time via which road, and *why not* the nearest warehouse if a closure forced a detour.
5. **Re-plan on `closures.next_change(T)`** — the plan is only valid until the next closure starts or ends.

### Tests the engine should add (Kingsley)
- no `PlanRow` route uses an edge that is closed at departure time
- truck capacity / stock / availability never violated (the placeholder already has these; keep them)
- a priority-1 shelter is never left unserved while a priority-4 one of the same commodity is served from the same warehouse
- the Oct 1 15:00Z scenario re-routes North Port deliveries away from the I-75 Myakka crossing

### Decisions to record here as you make them
- objective and tie-break (with the sentence AND the formula)
- how a closure "point" maps to road edges (radius? named segment? explicit `osm_id` list?)
- weight vs pallets
- what a human practitioner said when they saw the plan (date, role, three quotes)

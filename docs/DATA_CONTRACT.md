# Data contract

What every table/file means, where it comes from, and the rules the loaders enforce. If you change a
column here, change `sql/schema.sql`, `src/ianrelief/cleaning.py` and the tests together.

## Global rules

| Rule | Why |
|---|---|
| Every geometry is **EPSG:4326** (WGS84, longitude/latitude in degrees). | One CRS, no reprojection bugs. Distances are computed on `geography` casts (metres), never on degrees. |
| Every timestamp is **UTC** and must carry a `Z` or offset in the CSVs. Naive timestamps are rejected. | Ian made landfall at 15:05 EDT = 19:05 UTC. "Which one did you mean?" is a real bug in a disaster timeline. |
| Every table has a stable primary key; loaders **UPSERT**. Re-running `make load` never duplicates rows. | Data gets re-fetched and re-edited constantly. |
| Rows that fail validation go to **`data/clean/rejects.csv`** with `source, row_number, key, reason, raw`. | Nothing disappears silently. Look at this file after every load. |
| `data/raw/` is never committed; `data/clean/` is derived; `data/synthetic/` and `data/sample/` are committed. | Raw downloads are large and re-fetchable; the scenario inputs are the thing worth versioning. |
| Public source ids are kept (`source_system`, `source_id`, `site_crosswalk`). | So a number in this database can be traced back to the FEMA/OSM/NHC record it came from. |

## Identifiers

| Prefix | Meaning | Example |
|---|---|---|
| `SH-<hifld shelter_id>` | Shelter from HIFLD/FEMA NSS | `SH-134379` = Hertz Arena |
| `WH-nn` | Synthetic warehouse / staging area | `WH-01` |
| `T-nn` | Synthetic truck | `T-03` |
| `CL-<slug>` | Closure | `CL-SANIBEL-CSWY` |
| `osm_id` | OpenStreetMap way id (BIGINT) | `12345678` |
| `county_fips` | 5-digit Census FIPS | `12071` = Lee |

## Tables

### `sites` — shelters and warehouses
Source: shelters from **HIFLD Open / FEMA NSS "Shelter Locations"** (`data/clean/hifld_shelters.geojson`, or `data/sample/…` fallback labelled SAMPLE); warehouses from `data/synthetic/warehouses.csv`.

| column | type | rule |
|---|---|---|
| `site_id` | TEXT PK | see prefixes; unique |
| `site_type` | TEXT | `shelter` / `warehouse` / `pod` / `other` |
| `name`, `address`, `city`, `county`, `county_fips`, `state` | TEXT | `county` normalised to `Lee, Charlotte, Collier, Sarasota, DeSoto` |
| `source_system`, `source_id` | TEXT | `HIFLD_NSS` + shelter_id, or `SYNTHETIC` |
| `evacuation_capacity`, `post_impact_capacity` | INT | as published; often NULL |
| `pet_friendly`, `generator_onsite`, `in_surge_zone` | BOOL | derived from HIFLD codes; NULL when unknown |
| `attributes` | JSONB | every non-null source field, untouched |
| `geom` | Point 4326 | must be inside the SW Florida sanity box (25.6–27.7 N, 82.9–80.8 W) |

Known data issues: HIFLD lists the same physical facility under several `shelter_id`s (e.g. Kingsway Elementary appears three times with slightly different names). We keep them as separate sites and leave de-duplication as a documented decision (see `DECISIONS.md`). `shelter_status_code` is `CLOSED` for every row (no active incident today).

### `site_crosswalk`
`(source_system, source_id) -> site_id`. Add a row when you learn another system's id for a site (Red Cross facility code, county EOC code, …).

### `roads` — OSM ways
Source: Overpass API, `highway` in motorway…tertiary (+links), bbox in `config/counties.yaml`. One row per way; `geom` LineString 4326; `length_m` = geodesic metres; `tags` = all OSM tags. Today's map, not 2022's (see closures).

### `closures` — time windows when a link is impassable
Source: `data/synthetic/closures.csv`, reconstructed by hand from public reporting.

| column | rule |
|---|---|
| `closure_id` | unique |
| `closure_type` | `bridge_collapse` / `washout` / `flooding` / `debris` / `inspection` / `other` |
| `start_utc`, `end_utc` | tz-aware; `end_utc` NULL = open-ended; `end > start` |
| `time_confidence` | `reported` (a source states the time) / `approximate` (day known, hour estimated) / `assumed` (placeholder — verify or delete) |
| `osm_ref` | road ref the closure affects (`I 75`, `FL 78`) — free text for now, not yet joined to `roads` |
| `source_note` | **required**: what happened, where the times came from, what is uncertain |
| `geom` | representative Point 4326 |

Active-at semantics: half-open, `start <= t < end`. Python: `ianrelief.closures.is_active`. SQL: `closures_active_at(t)`.

### `vehicles`
`vehicle_id` PK, `vehicle_type`, `capacity_kg > 0`, `capacity_pallets`, `home_site_id -> sites`, `available_from_utc`.

### `inventory` — snapshot series
PK `(site_id, commodity, as_of_utc)`; `qty_on_hand >= 0`; `unit_kg` for capacity maths. "Current stock at time T" = latest row with `as_of_utc <= T`.

### `demand` — what shelters need
PK `(site_id, commodity, valid_from_utc)`; `qty_needed, qty_delivered >= 0`; `priority` 1 (most urgent) … 5. Unmet = needed − delivered. "Demand at time T" = latest row with `valid_from_utc <= T`. `site_id` must be a known shelter.

### `storm_track` — NHC best track
Source: HURDAT2 (newest release on the NHC index page), storm `AL092022`. PK `(atcf_id, time_utc, record_id)`; `record_id = 'L'` marks landfall fixes. Wind in knots, pressure in mb, wind radii in nautical miles. Florida landfall: `2022-09-28T19:05:00Z`, 130 kt, 941 mb.

### `counties`
Source: Census TIGERweb (current vintage). MultiPolygon 4326, `county_fips` PK.

### `load_log`
One row per loader run: `loader, source_path, rows_in, rows_upserted, rows_rejected, ran_at`.

## Files that are not tables

| file | purpose |
|---|---|
| `config/counties.yaml` | study area, FIPS codes, county spelling aliases, Overpass bbox and highway classes |
| `config/gibs_layers.yaml` | NASA GIBS WMTS layer ids for a map UI, verified against the capabilities XML |
| `data/synthetic/scenario.yaml` | the scenario definition (Kingsley's file) |
| `data/clean/fema_dr4673_declarations.csv` | OpenFEMA DR-4673-FL designated areas (context, not loaded to a table yet) |
| `data/clean/rejects.csv` | rows dropped by the last load |
| `data/raw/*.meta.json` / `*.FAILED.json` | when/what was fetched, or why a fetch failed |

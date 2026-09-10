# ian-relief-logistics

A small, honest simulator for one hard question from Hurricane Ian (Southwest Florida, landfall
28 September 2022): **on the morning after the storm, which truck should take what, from which
warehouse, to which shelter — given the roads that are actually out right now?**

This repository is the *plumbing* for that question: public data fetched and cleaned, a PostGIS
database that holds it, a first working query, and a clearly marked empty seat where the decision
logic goes. It is a student interview project, built to be read by a non-engineer as much as run by
an engineer.

**Attribution.** Scaffold (data acquisition, cleaning, loading, project structure, this README) by
Raphael, Ted Barnett's agent. Scenario definition, decision logic, tests of that logic and user
validation by **Kingsley Marin** — see [What Kingsley owns next](#what-kingsley-owns-next).

---

## The decision this supports

A county logistics coordinator has a list of open shelters, three staging warehouses, five trucks, and
not enough water. The Sanibel Causeway is gone. Pine Island is cut off. Two days later I-75 floods at
the Myakka River. The tool should produce a **plan a human can read aloud and argue with**: one line per
truck-load, each with a plain-English *because*.

It does not predict floods. It does not replace the coordinator. It makes the trade-offs visible.

## Architecture in one picture

```
  PUBLIC DATA (no keys, all free)                          THIS REPO
  ─────────────────────────────                            ─────────
  HIFLD / FEMA  shelter registry  ──┐
  OpenStreetMap roads (Overpass) ───┤   scripts/fetch_*.py      data/raw/   (cached, not committed)
  NOAA NHC  HURDAT2 best track  ────┼──►  polite, cached  ──►  data/clean/ (derived)
  US Census TIGER  counties  ───────┤                               │
  OpenFEMA  DR-4673-FL  ────────────┘                               │  src/ianrelief/cleaning.py
  NASA GIBS  imagery (WMTS)  ── config only, tiles fetched by a map UI ──┐   rejects → data/clean/rejects.csv
                                                                    │   │
  SYNTHETIC SCENARIO (committed)                                    ▼   │
  data/synthetic/  warehouses, trucks, inventory, demand,   ──►  loaders.py (UPSERT, idempotent)
                   closures (reconstructed), scenario.yaml          │
                                                                    ▼
                                                    ┌──────────────────────────────┐
                                                    │  PostGIS (docker-compose)    │
                                                    │  sites · roads · closures    │
                                                    │  vehicles · inventory ·      │
                                                    │  demand · storm_track ·      │
                                                    │  counties  (all EPSG:4326)   │
                                                    └──────────────┬───────────────┘
                                                                   │
                     make demo  ──►  demo.py: unmet demand + nearest warehouse (ST_Distance)   ◄── works today
                                 ──►  allocate.py: KINGSLEY's engine (OR-Tools VRP)             ◄── placeholder
                                 ──►  map UI with GIBS layers                                     ◄── not built
```

## How to run (five commands)

You need Python 3.10+ and Docker. Without Docker, everything except `make load`, `make demo` and the
`db` tests still works (`make demo-offline` gives the same table from CSV).

```bash
make deps            # 1. pip install requests psycopg2-binary PyYAML shapely pytest
make up              # 2. start PostGIS in Docker; sql/schema.sql is applied automatically
make fetch           # 3. download + clean the public data into data/clean (≈20 MB, ~30 s)
make load            # 4. load public + synthetic data; dirty rows -> data/clean/rejects.csv
make demo            # 5. first slice: shelters, unmet demand, nearest warehouse at 2022-09-29 12:00Z
```

Then: `make test` (pure python), `make test-db` (also the PostGIS tests), `make gibs-check` (one NASA
tile per configured layer), `python scripts/demo.py --at 2022-10-01T15:00:00Z` (watch the I-75 closure
appear), `python scripts/allocate_demo.py` (the placeholder plan with explanations).

## What `make demo` shows

```
Unmet shelter demand and nearest warehouse (straight line) as of 2022-09-29T12:00:00+00:00

shelter    name                                  county    commodity           pri  unmet  nearest wh    km
SH-134379  Hertz Arena (Pet Friendly)            Lee       meals_shelf_stable    1   4500  WH-01        7.4
SH-133931  Port Charlotte High School            Charlotte meals_shelf_stable    1   2500  WH-02       18.4
SH-135125  Island Coast High School (Cape Coral) Lee       water_cases           1    800  WH-02       22.2
...
Closures ACTIVE at this time (3) — straight-line distance ignores them:
  - CL-SANIBEL-CSWY: Sanibel Causeway … [bridge_collapse, approximate] 2022-09-28T20:00Z -> 2022-10-19T14:00Z
  - CL-MATLACHA-PINE-ISLAND-RD: Pine Island Rd (SR 78) at Matlacha Pass … 2022-09-28T20:00Z -> 2022-10-05T22:00Z
  - CL-CAPE-CORAL-BRIDGES-INSPECT: … [inspection, assumed] …
```

Notice Island Coast High School in Cape Coral is "nearest" to the Punta Gorda warehouse. By road that
trip crosses a river. That gap — distance versus reachability — is the whole project.

## Every dataset

| Dataset | What | Where from | License | What was wrong / what we did |
|---|---|---|---|---|
| **Shelters** | 304 shelter facilities in Lee, Charlotte, Collier, Sarasota, DeSoto (name, address, capacity, pet/generator/surge flags, point) | HIFLD Open → FEMA "ESF#6 Shelter System", ArcGIS REST layer 5 `gis.fema.gov/arcgis/rest/services/NSS/FEMA_NSS/FeatureServer/5` | US Government work, public domain | The URL most tutorials cite (`services1.arcgis.com/Hp6G80Pky0om7QvQ/…`) is dead (HTTP 400). County spelled five ways → normalised. Same building under several ids → kept separate, documented. **It is today's registry, not the 2022 state** — every status is CLOSED. If the endpoint is down the fetcher falls back to a committed 10-feature sample and labels it `SAMPLE`. |
| **Roads** | 9,757 OpenStreetMap ways, motorway→tertiary, bbox 26.40–27.10 N / 82.35–81.60 W (Fort Myers, Cape Coral, Sanibel, Pine Island, Punta Gorda, North Port) | Overpass API (`overpass-api.de`) | ODbL 1.0, © OpenStreetMap contributors | Geofabrik's Florida file is ~300 MB and needs extra tools; Overpass returns 9 MB in seconds but is rate-limited and shared — one request per run, cached. Residential roads excluded for size. OSM is **today's** map: the rebuilt Sanibel Causeway exists in it; 2022 outages live in `closures.csv`. |
| **Storm track** | 40 six-hourly fixes + landfall records for AL092022 (time, lat, lon, wind kt, pressure mb, wind radii) | NOAA/NHC HURDAT2, newest release on `nhc.noaa.gov/data/hurdat/` | Public domain | HURDAT2 is a fixed-width text format with N/S/E/W suffixes and `-999` sentinels → parsed to a tidy CSV. File name changes yearly → fetcher reads the index. Florida landfall 2022-09-28 19:05 UTC, 130 kt, 941 mb. |
| **Counties** | 5 county polygons | US Census TIGERweb REST (current vintage) | Public domain | The TIGER shapefile needs a shapefile reader; the REST service returns GeoJSON in EPSG:4326 directly. |
| **Disaster declaration** | 69 designated-area rows for DR-4673-FL (declared 2022-09-29, incident 2022-09-23..11-04) | OpenFEMA API v2 `DisasterDeclarationsSummaries` | Public domain | Flattened to CSV with an `in_study_area` flag. Context only; not a decision input. |
| **Satellite imagery** | WMTS layer ids for 2022-09-26..10-02: VIIRS & MODIS true colour, MODIS flood 2-/3-day, VIIRS Day/Night Band (radiance + ENCC), IMERG rain, coastline + label overlays | NASA GIBS `gibs.earthdata.nasa.gov/wmts/epsg4326/best` | Free with NASA attribution | Ids checked against the live capabilities XML. `VIIRS_Black_Marble` only has 2012/2016 → replaced by the daily Day/Night Band. GIBS tile matrices are not powers of two → correct tile maths in `verify_gibs_layers.py`. All 9 layers returned a 200 image tile on 2026-09-09. |
| **Warehouses / trucks / inventory / demand** | 3 staging sites, 5 trucks, stock snapshots, needs for 9 real shelters (+1 later) | **Synthetic**, written for this scenario | MIT (this repo) | Quantities are invented; shelter ids are real HIFLD ids. Seven dirty rows are seeded on purpose (missing coordinate, duplicates, unknown shelter, negative qty, end-before-start) and must land in `rejects.csv`. |
| **Closures** | Sanibel Causeway, Pine Island Rd/Matlacha, I-75 at the Myakka River, Cape Coral bridges inspection — UTC windows | **Reconstructed** from public reporting | MIT (this repo) | Not an official log. Each row has `time_confidence` (`reported`/`approximate`/`assumed`) and a `source_note`. Start hours set ~1 h after landfall where the failure time is not public. |

Details for every column: [`docs/DATA_CONTRACT.md`](docs/DATA_CONTRACT.md). Why each choice: [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Repository map

```
config/counties.yaml        study area, FIPS, bbox, highway classes
config/gibs_layers.yaml     NASA GIBS layer ids (verified) for a map UI
data/synthetic/             the scenario: warehouses, trucks, inventory, demand, closures, scenario.yaml
data/sample/                small committed fallbacks (10 shelters, Ian track) — labelled SAMPLE when used
data/raw/  data/clean/      downloads (ignored) and derived files (ignored); rejects.csv lives in clean/
scripts/fetch_*.py          one fetcher per public source; scripts/verify_gibs_layers.py
scripts/load_*.py           idempotent loaders; scripts/demo.py; scripts/allocate_demo.py
sql/schema.sql              PostGIS schema (EPSG:4326 everywhere, UTC everywhere)
src/ianrelief/              the package: http, cleaning, closures, loaders, demo, allocate
tests/                      32 pure-python tests + 7 that need PostGIS (they skip, loudly, without it)
docs/                       DATA_CONTRACT, SCENARIO_TEMPLATE, DECISIONS
```

## What Kingsley owns next

1. **Run it on a machine with Docker.** `make up && make load && make demo && make test-db`. The PostGIS
   path was written but not executed on the machine that built this (no Docker there — see Honesty notes).
   Fix whatever breaks and commit the fix under your name.
2. **Own `data/synthetic/scenario.yaml`.** Replace every `TODO(Kingsley)` — objective, horizon, constraints,
   success criteria. [`docs/SCENARIO_TEMPLATE.md`](docs/SCENARIO_TEMPLATE.md) walks through the questions.
3. **Verify or delete the closures.** `closures.csv` is reconstructed. Upgrade `assumed`/`approximate` rows
   with FDOT / county sources and cite them in `source_note`, or remove them.
4. **Replace the allocation placeholder.** `src/ianrelief/allocate.py` has the interface and a greedy stub
   marked `# KINGSLEY: replace with OR-Tools VRP`. The OR-Tools slot-in sketch is in `docs/DECISIONS.md`.
   Start with a travel-time matrix that respects `closures_active_at(T)` — that alone makes the demo honest.
5. **Write the tests for your logic.** No plan row may use a closed road at departure time; priority-1
   is never starved for priority-4; the Oct 1 15:00Z scenario re-routes around the Myakka crossing.
6. **Put the plan in front of a practitioner** (food-bank logistics coordinator, Red Cross volunteer,
   university emergency-management staff). Record what they said in `docs/DECISIONS.md`.
7. **Only then** a small map (MapLibre/Leaflet) with the GIBS layers from `config/gibs_layers.yaml`, sites,
   closures and plan routes. The layer ids are ready; the UI is deliberately not built here.

## Honesty notes

- **Imagery is not passability.** The GIBS layers show clouds, floodwater and lights at 250–500 m. They
  cannot tell you a bridge span is missing. They are context for a person and a source for hand-entered
  closure rows — never a machine-read road status in this project.
- **Closures are reconstructed**, with approximate hours, from public reporting. Treat them as a scenario,
  not a record.
- **The shelter registry is stale relative to 2022.** HIFLD/FEMA publishes the current registry; we
  hand-picked shelters widely reported open for Ian. Per-shelter populations were never published in a
  machine-readable form; demand quantities are synthetic.
- **The roads are today's.** OSM has the rebuilt causeway. Time-dependence lives in `closures`, not geometry.
- **Straight-line ≠ road.** `make demo` uses great-circle distance on purpose, to make the gap visible.
- **Docker was not available where this was built.** Schema, loaders and the SQL demo were reviewed, not
  executed. 32 pure-python tests pass; the 7 database tests skip with the connection error as their reason.
- **Nothing here is an operational tool.** It is a learning project about making logistics trade-offs
  legible.

## License

MIT — see [LICENSE](LICENSE). Data sources keep their own terms (public domain for US federal data;
ODbL for OpenStreetMap; NASA attribution for GIBS imagery).

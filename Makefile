# ian-relief-logistics — common tasks.
# Every target is a thin wrapper around a Python script so nothing here is magic.

PY ?= python3
export PYTHONPATH := src
export IAN_DATABASE_URL ?= postgresql://ian:ian@localhost:5432/ianrelief

.PHONY: help deps up down reset wait-db schema fetch fetch-shelters fetch-roads fetch-track fetch-counties fetch-fema gibs-check load demo demo-offline test test-db clean-data

help:
	@echo "make deps          install python dependencies (pip)"
	@echo "make up            start PostGIS in Docker (applies sql/schema.sql on first boot)"
	@echo "make down          stop PostGIS (keeps data)"
	@echo "make reset         stop PostGIS and delete its volume"
	@echo "make schema        (re)apply sql/schema.sql to a running database"
	@echo "make fetch         download all public datasets into data/raw and clean into data/clean"
	@echo "make gibs-check    fetch one NASA GIBS tile per configured layer and report HTTP status"
	@echo "make load          load clean + synthetic data into PostGIS (idempotent)"
	@echo "make demo          first working slice: shelters, unmet demand, nearest warehouse (PostGIS)"
	@echo "make demo-offline  same demo computed in pure Python from CSV/GeoJSON (no database)"
	@echo "make test          pure-python tests (no database needed)"
	@echo "make test-db       all tests including those that need PostGIS"

deps:
	$(PY) -m pip install -r requirements.txt

up:
	docker compose up -d
	$(MAKE) wait-db

down:
	docker compose down

reset:
	docker compose down -v

wait-db:
	$(PY) scripts/wait_for_db.py

schema:
	$(PY) scripts/apply_schema.py

fetch: fetch-shelters fetch-roads fetch-track fetch-counties fetch-fema

fetch-shelters:
	$(PY) scripts/fetch_hifld_shelters.py

fetch-roads:
	$(PY) scripts/fetch_osm_roads.py

fetch-track:
	$(PY) scripts/fetch_nhc_track.py

fetch-counties:
	$(PY) scripts/fetch_tiger_counties.py

fetch-fema:
	$(PY) scripts/fetch_openfema.py

gibs-check:
	$(PY) scripts/verify_gibs_layers.py

load:
	$(PY) scripts/load_all.py

demo:
	$(PY) scripts/demo.py --at 2022-09-29T12:00:00Z

demo-offline:
	$(PY) scripts/demo.py --at 2022-09-29T12:00:00Z --offline

test:
	$(PY) -m pytest -q -m "not db"

test-db:
	$(PY) -m pytest -q

clean-data:
	rm -rf data/clean/* && touch data/clean/.gitkeep

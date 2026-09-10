"""Paths and settings. Everything is relative to the repo root so scripts work from anywhere."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CLEAN_DIR = DATA_DIR / "clean"
SYNTHETIC_DIR = DATA_DIR / "synthetic"
SAMPLE_DIR = DATA_DIR / "sample"
CONFIG_DIR = REPO_ROOT / "config"
SQL_DIR = REPO_ROOT / "sql"

REJECTS_PATH = CLEAN_DIR / "rejects.csv"

# Hurricane Ian: NHC ATCF id and the landfall moment we anchor scenarios on.
STORM_ATCF_ID = "AL092022"
LANDFALL_UTC = "2022-09-28T19:05:00Z"

DATABASE_URL = os.environ.get("IAN_DATABASE_URL", "postgresql://ian:ian@localhost:5432/ianrelief")
HTTP_USER_AGENT = os.environ.get(
    "IAN_HTTP_USER_AGENT",
    "ian-relief-logistics/0.1 (student project; github.com/kingsley-marin/ian-relief-logistics)",
)
OVERPASS_URL = os.environ.get("IAN_OVERPASS_URL", "https://overpass-api.de/api/interpreter")


def _load_counties() -> dict:
    with open(CONFIG_DIR / "counties.yaml", "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


_COUNTIES = _load_counties()
STATE_FIPS: str = _COUNTIES["state_fips"]
COUNTIES: list[dict] = _COUNTIES["counties"]
COUNTY_FIPS: list[str] = [c["fips"] for c in COUNTIES]
COUNTY_NAMES: list[str] = [c["name"] for c in COUNTIES]
COUNTY_NAME_ALIASES: dict[str, str] = {k.upper(): v for k, v in _COUNTIES.get("county_name_aliases", {}).items()}
ROADS_BBOX: dict = _COUNTIES["roads_bbox"]
ROADS_HIGHWAY_CLASSES: list[str] = _COUNTIES["roads_highway_classes"]

# Loose sanity box for "is this coordinate even in SW Florida?" checks in cleaning.py.
STUDY_AREA_BBOX = {"south": 25.6, "west": -82.9, "north": 27.7, "east": -80.8}


def normalize_county(name: str | None) -> str | None:
    """'LEE' -> 'Lee', 'DE SOTO' -> 'DeSoto', None -> None."""
    if name is None:
        return None
    key = name.strip().upper()
    if key in COUNTY_NAME_ALIASES:
        return COUNTY_NAME_ALIASES[key]
    for c in COUNTY_NAMES:
        if c.upper() == key:
            return c
    return name.strip().title()


def ensure_dirs() -> None:
    for d in (RAW_DIR, CLEAN_DIR):
        d.mkdir(parents=True, exist_ok=True)

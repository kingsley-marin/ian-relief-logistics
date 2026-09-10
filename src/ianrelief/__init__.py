"""ianrelief — Hurricane Ian disaster-response logistics simulator (data pipeline scaffold).

Package layout
--------------
config.py      paths, county list, bbox, environment variables
http.py        polite cached HTTP fetch used by every scripts/fetch_*.py
cleaning.py    row validation shared by the loaders; produces data/clean/rejects.csv
closures.py    road-closure time-window logic (pure python, unit tested)
db.py          psycopg2 connection helper
loaders.py     idempotent PostGIS loaders
demo.py        the first end-to-end slice (unmet demand + nearest warehouse)
allocate.py    allocation engine INTERFACE + naive placeholder (Kingsley owns the real one)
"""

__version__ = "0.1.0"

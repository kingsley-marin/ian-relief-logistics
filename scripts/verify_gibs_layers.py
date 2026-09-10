#!/usr/bin/env python3
"""Fetch ONE tile per layer in config/gibs_layers.yaml and report the HTTP status.

Also cross-checks every layer id and TileMatrixSet against the live GIBS capabilities XML, so a typo
or a retired layer is caught here rather than in the map UI.

Tile maths (WMTS, EPSG:4326 / CRS84): from the TileMatrix's ScaleDenominator,
  pixel_span_deg = scale_denominator * 0.28e-3 / 111319.490793
  tile_span_deg  = TileWidth * pixel_span_deg
  col = floor((lon - top_left_lon) / tile_span_deg);  row = floor((top_left_lat - lat) / tile_span_deg)
GIBS matrices are not powers of two (2, 3, 5, 10, 20 ... tiles wide) so do not use slippy-map z/x/y maths.

Run: make gibs-check      (exit code 0 = every layer returned 200)
Output: data/raw/gibs_check.json and a printed table.
"""
from __future__ import annotations

import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402

from ianrelief import config, http  # noqa: E402

NS = {"ows": "http://www.opengis.net/ows/1.1", "w": "http://www.opengis.net/wmts/1.0"}
METERS_PER_DEGREE = 111319.490793
CFG_PATH = config.CONFIG_DIR / "gibs_layers.yaml"


def load_capabilities(force: bool = False) -> ET.Element:
    cfg = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    res = http.fetch("gibs_WMTSCapabilities.xml", cfg["capabilities"], force=force, timeout=120)
    if not res.ok:
        raise SystemExit(f"cannot fetch GIBS capabilities: {res.error}")
    return ET.parse(res.path).getroot()


def index_capabilities(root: ET.Element):
    layers: dict[str, dict] = {}
    for L in root.iter("{http://www.opengis.net/wmts/1.0}Layer"):
        ident = L.find("ows:Identifier", NS).text
        dim = L.find("w:Dimension", NS)
        layers[ident] = {
            "tilematrixsets": [x.find("w:TileMatrixSet", NS).text for x in L.findall("w:TileMatrixSetLink", NS)],
            "formats": [f.text for f in L.findall("w:Format", NS)],
            "time_values": [v.text for v in dim.findall("w:Value", NS)] if dim is not None else [],
        }
    matrices: dict[str, list[dict]] = {}
    for tms in root.iter("{http://www.opengis.net/wmts/1.0}TileMatrixSet"):
        ident = tms.find("ows:Identifier", NS)
        if ident is None:
            continue
        rows = []
        for tm in tms.findall("w:TileMatrix", NS):
            tl = tm.find("w:TopLeftCorner", NS).text.split()
            rows.append({
                "z": tm.find("ows:Identifier", NS).text,
                "scale": float(tm.find("w:ScaleDenominator", NS).text),
                "top_left": (float(tl[0]), float(tl[1])),  # GIBS writes "lon lat" (-180 90)
                "tile_w": int(tm.find("w:TileWidth", NS).text),
                "matrix_w": int(tm.find("w:MatrixWidth", NS).text),
                "matrix_h": int(tm.find("w:MatrixHeight", NS).text),
            })
        matrices[ident.text] = rows
    return layers, matrices


def date_covered(time_values: list[str], day: str) -> bool | None:
    """True/False if the layer advertises a daily range covering `day`; None if no time dimension."""
    if not time_values:
        return None
    d = datetime.fromisoformat(day).date()
    for v in time_values:
        parts = v.split("/")
        if len(parts) == 3 and "T" not in parts[0]:
            a, b = datetime.fromisoformat(parts[0]).date(), datetime.fromisoformat(parts[1]).date()
            if a <= d <= b:
                return True
    return False


def tile_for(matrix: dict, lat: float, lon: float) -> tuple[int, int]:
    pixel_span = matrix["scale"] * 0.28e-3 / METERS_PER_DEGREE
    span = matrix["tile_w"] * pixel_span
    col = math.floor((lon - matrix["top_left"][0]) / span)
    row = math.floor((matrix["top_left"][1] - lat) / span)
    return row, col


def main(force: bool = False) -> int:
    cfg = yaml.safe_load(CFG_PATH.read_text(encoding="utf-8"))
    root = load_capabilities(force=force)
    cap_layers, matrices = index_capabilities(root)
    day = cfg["dates"]["landfall"]
    lat, lon = cfg["probe_point"]["lat"], cfg["probe_point"]["lon"]
    sess = requests.Session()
    sess.headers["User-Agent"] = config.HTTP_USER_AGENT

    results = []
    all_ok = True
    print(f"GIBS check — probe {lat},{lon} on {day}; capabilities lists {len(cap_layers)} layers\n")
    print(f"{'layer':48s} {'in_caps':7s} {'tms_ok':6s} {'date':8s} {'z/row/col':12s} {'http':5s} bytes")
    for layer in cfg["layers"]:
        lid, tms_name = layer["id"], layer["tilematrixset"]
        cap = cap_layers.get(lid)
        in_caps = cap is not None
        tms_ok = bool(cap and tms_name in cap["tilematrixsets"])
        covered = date_covered(cap["time_values"], day) if cap else False
        status, nbytes, url = None, 0, None
        z = row = col = None
        if in_caps and tms_ok:
            # pick a mid zoom so the tile is small but real; clamp to what the matrix set offers
            levels = matrices[tms_name]
            z_idx = min(max(len(levels) - 3, 0), len(levels) - 1)
            m = levels[z_idx]
            row, col = tile_for(m, lat, lon)
            z = m["z"]
            time_part = day if covered is not None else ""
            url = cfg["tile_template"].format(endpoint=cfg["endpoint"], layer=lid, time=time_part, tilematrixset=tms_name, z=z, y=row, x=col, ext=layer["ext"])
            url = url.replace("/default//", "/default/")  # static layers have no time segment
            try:
                r = sess.get(url, timeout=60)
                status, nbytes = r.status_code, len(r.content)
                ctype = r.headers.get("Content-Type", "")
                if status == 200 and not ctype.startswith("image/"):
                    status = -200  # 200 but not an image (GIBS returns XML errors with 200 sometimes)
            except requests.RequestException as exc:
                status, nbytes = -1, 0
                print(f"  {lid}: {exc}")
            http.polite_pause(0.5)
        ok = in_caps and tms_ok and covered is not False and status == 200
        all_ok &= ok
        results.append({"layer": lid, "in_capabilities": in_caps, "tilematrixset_ok": tms_ok, "date_covered": covered,
                        "z": z, "row": row, "col": col, "url": url, "http_status": status, "bytes": nbytes, "ok": ok})
        cov = "static" if covered is None else ("yes" if covered else "NO")
        print(f"{lid:48s} {str(in_caps):7s} {str(tms_ok):6s} {cov:8s} {f'{z}/{row}/{col}':12s} {str(status):5s} {nbytes}")

    for rej in cfg.get("rejected", []):
        cap = cap_layers.get(rej["id"])
        cov = date_covered(cap["time_values"], day) if cap else None
        print(f"(rejected) {rej['id']:37s} in_caps={cap is not None} covers_{day}={cov} — {rej['reason']}")

    out = {"checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "probe_day": day, "results": results, "all_ok": all_ok}
    (config.RAW_DIR / "gibs_check.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n{'ALL LAYERS OK' if all_ok else 'SOME LAYERS FAILED'} — details in data/raw/gibs_check.json")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(force="--force" in sys.argv))

"""Polite, cached HTTP for the fetch scripts.

Rules every fetcher follows:
  * identify ourselves with a User-Agent (Overpass in particular asks for this)
  * cache every download under data/raw/<name> and skip the network if the file exists
    (pass force=True or delete the file to re-download)
  * write a sidecar <name>.meta.json with url, timestamp, status, bytes — so the README's
    "what was fetched when" claims can be checked
  * on failure write <name>.FAILED.json with the error instead of silently continuing
  * sleep between paged requests; back off on 429/5xx
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from . import config


@dataclass
class FetchResult:
    name: str
    path: Path
    url: str
    status: int | None
    bytes: int
    from_cache: bool
    ok: bool
    error: str | None = None
    fetched_at: str | None = None

    def meta(self) -> dict[str, Any]:
        d = asdict(self)
        d["path"] = str(self.path)
        return d


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.HTTP_USER_AGENT, "Accept": "*/*"})
    return s


def write_meta(name: str, meta: dict[str, Any]) -> None:
    config.ensure_dirs()
    (config.RAW_DIR / f"{name}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def write_failure(name: str, url: str, error: str, extra: dict[str, Any] | None = None) -> Path:
    config.ensure_dirs()
    p = config.RAW_DIR / f"{name}.FAILED.json"
    payload = {"name": name, "url": url, "error": error, "failed_at": _now(), **(extra or {})}
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def fetch(
    name: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    method: str = "GET",
    force: bool = False,
    timeout: float = 120.0,
    retries: int = 3,
    backoff_s: float = 5.0,
) -> FetchResult:
    """Download `url` to data/raw/<name> unless it is already there."""
    config.ensure_dirs()
    path = config.RAW_DIR / name
    if path.exists() and path.stat().st_size > 0 and not force:
        return FetchResult(name, path, url, None, path.stat().st_size, True, True, fetched_at=_cached_at(name))

    sess = _session()
    last_err = "unknown"
    status: int | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = sess.request(method, url, params=params, data=data, timeout=timeout)
            status = resp.status_code
            if status == 200:
                path.write_bytes(resp.content)
                res = FetchResult(name, path, resp.url, status, len(resp.content), False, True, fetched_at=_now())
                write_meta(name, res.meta())
                failed = config.RAW_DIR / f"{name}.FAILED.json"
                if failed.exists():
                    failed.unlink()
                return res
            last_err = f"HTTP {status}: {resp.text[:300]}"
            if status in (429, 500, 502, 503, 504) and attempt < retries:
                wait = backoff_s * attempt
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = max(wait, float(retry_after))
                print(f"  {name}: {status}, retrying in {wait:.0f}s ({attempt}/{retries})")
                time.sleep(wait)
                continue
            break
        except requests.RequestException as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(backoff_s * attempt)
                continue
    write_failure(name, url, last_err, {"status": status})
    return FetchResult(name, path, url, status, 0, False, False, error=last_err, fetched_at=_now())


def _cached_at(name: str) -> str | None:
    meta = config.RAW_DIR / f"{name}.meta.json"
    if meta.exists():
        try:
            return json.loads(meta.read_text(encoding="utf-8")).get("fetched_at")
        except json.JSONDecodeError:
            return None
    return None


def polite_pause(seconds: float = 1.0) -> None:
    time.sleep(seconds)

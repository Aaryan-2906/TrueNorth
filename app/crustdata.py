"""Crustdata HTTP client with a response cache and a paid-call ledger."""

import csv
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from app import config

BASE_URL = "https://api.crustdata.com"
API_VERSION = "2025-11-01"
CACHE_DIR = config.ROOT_DIR / "cache"
LEDGER_PATH = config.ROOT_DIR / "credits.csv"
LEDGER_HEADER = ["time", "path", "sha1", "credits_used", "status"]
PERSON_FIELDS = ["basic_profile", "experience", "education", "social_handles"]
MAX_RETRIES = 3
RETRY_WAIT_SECONDS = 5
TIMEOUT_SECONDS = 60.0
HTTP_ERROR_MESSAGES = {
    401: "Crustdata rejected the API key (HTTP 401). Check CRUSTDATA_API_KEY in .env.",
    402: "Crustdata credits have run out (HTTP 402). Check the balance with GET /account/credits or ask an organiser.",
    403: "This API key is not allowed to use this endpoint (HTTP 403). Ask an organiser which endpoints it allows (GET /account/endpoints).",
    429: "Crustdata rate limit reached (HTTP 429) after 3 retries. Wait a minute and run again; results already fetched are reused.",
}

# Failures where the request was never sent, so retrying cannot double-bill.
_NEVER_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

# Lookups made during this run, for the report's cache-vs-live statement.
_lookups: list[dict[str, str]] = []

# Optional observer told about each paid lookup, live or cached. Used by the web UI's activity strip.
call_listener: Callable[[dict[str, Any]], None] | None = None


class CrustdataError(RuntimeError):
    """A Crustdata call failed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _request_hash(path: str, body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1((path + canonical).encode("utf-8")).hexdigest()


def _cache_file(path: str, sha1: str) -> Path:
    slug = path.strip("/").replace("/", "_")
    return CACHE_DIR / slug / f"{sha1}.json"


def _write_cache(file: Path, raw: str) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    temp = file.with_suffix(".tmp")
    temp.write_text(raw, encoding="utf-8")
    os.replace(temp, file)


def _append_ledger(path: str, sha1: str, credits_used: str, status: str) -> None:
    is_new = not LEDGER_PATH.exists()
    with LEDGER_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(LEDGER_HEADER)
        writer.writerow([_now(), path, sha1, credits_used, status])


def _notify(path: str, sha1: str, source: str, started: float, fetched_at: str | None, status: str) -> None:
    if call_listener is not None:
        call_listener(
            {
                "path": path,
                "sha1": sha1,
                "source": source,
                "fetched_at": fetched_at,
                "ms": round((time.perf_counter() - started) * 1000),
                "status": status,
            }
        )


def reset_run() -> None:
    """Forget the previous run's lookup counts (for a long-running server)."""
    _lookups.clear()


def _not_retried(path: str, billed: bool, detail: str) -> str:
    if billed:
        return f"{path}: lookup failed: may have been billed, not retried ({detail})."
    return f"{path}: call failed, not retried ({detail})."


def _send(method: str, path: str, body: Any, sha1: str, billed: bool) -> httpx.Response:
    """Send one request, retrying only on 429 or a connection that never opened."""
    if not config.CRUSTDATA_API_KEY:
        raise CrustdataError("CRUSTDATA_API_KEY is not set in .env")
    headers = {
        "authorization": f"Bearer {config.CRUSTDATA_API_KEY}",
        "x-api-version": API_VERSION,
        "content-type": "application/json",
    }

    for attempt in range(MAX_RETRIES + 1):
        started = time.perf_counter()
        try:
            response = httpx.request(
                method, BASE_URL + path, headers=headers, json=body, timeout=TIMEOUT_SECONDS
            )
        except _NEVER_SENT as exc:
            if billed:
                _notify(path, sha1, "live", started, None, type(exc).__name__)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT_SECONDS)
                continue
            raise CrustdataError(f"{path}: could not connect ({type(exc).__name__}).") from exc
        except httpx.TransportError as exc:
            if billed:
                _append_ledger(path, sha1, "", type(exc).__name__)
                _notify(path, sha1, "live", started, None, type(exc).__name__)
            raise CrustdataError(_not_retried(path, billed, type(exc).__name__)) from exc

        if billed:
            _append_ledger(
                path, sha1, response.headers.get("x-credits-used", ""), str(response.status_code)
            )
            _notify(path, sha1, "live", started, _now(), str(response.status_code))
        if response.status_code == 429 and attempt < MAX_RETRIES:
            time.sleep(RETRY_WAIT_SECONDS)
            continue
        if response.status_code in HTTP_ERROR_MESSAGES:
            raise CrustdataError(f"{path}: {HTTP_ERROR_MESSAGES[response.status_code]}")
        if response.status_code >= 500:
            raise CrustdataError(_not_retried(path, billed, f"HTTP {response.status_code}"))
        if response.status_code >= 400:
            raise CrustdataError(f"{path}: HTTP {response.status_code}: {response.text[:300]}")
        return response

    raise CrustdataError(f"{path}: gave up after {MAX_RETRIES} retries.")


def _parse_and_cache(response: httpx.Response, path: str, file: Path, billed: bool) -> Any:
    raw = response.text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CrustdataError(_not_retried(path, billed, "reply was not valid JSON")) from exc
    _write_cache(file, raw)
    return data


def post(path: str, body: Any, billed: bool, fresh: bool | None = None) -> Any:
    """POST a lookup, serving it from the cache unless fresh is on."""
    use_fresh = config.FRESH if fresh is None else fresh
    sha1 = _request_hash(path, body)
    file = _cache_file(path, sha1)

    if not use_fresh and file.exists():
        started = time.perf_counter()
        try:
            cached = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Never pay again on our own: the person decides whether to delete it and re-fetch.
            raise CrustdataError(
                f"{path}: cache file {file.name} could not be read. Delete it to fetch again (a paid call)."
            ) from exc
        fetched_at = datetime.fromtimestamp(file.stat().st_mtime, timezone.utc)
        _lookups.append(
            {
                "path": path,
                "sha1": sha1,
                "source": "cache",
                "fetched_at": fetched_at.isoformat(timespec="seconds"),
            }
        )
        _notify(path, sha1, "cache", started, fetched_at.isoformat(timespec="seconds"), "cached")
        return cached

    response = _send("POST", path, body, sha1, billed)
    data = _parse_and_cache(response, path, file, billed)
    _lookups.append({"path": path, "sha1": sha1, "source": "live", "fetched_at": _now()})
    return data


def get(path: str) -> Any:
    """GET a free account endpoint. Always live, because balances go stale."""
    sha1 = _request_hash(path, None)
    response = _send("GET", path, None, sha1, billed=False)
    return _parse_and_cache(response, path, _cache_file(path, sha1), billed=False)


def run_stats() -> dict[str, Any]:
    """Return this run's lookup counts and each lookup's source and fetch time."""
    return {
        "from_cache": sum(item["source"] == "cache" for item in _lookups),
        "live": sum(item["source"] == "live" for item in _lookups),
        "lookups": [dict(item) for item in _lookups],
    }


def account_credits() -> Any:
    return get("/account/credits")


def enrich_people(urls: list[str], fresh: bool | None = None) -> Any:
    if not 1 <= len(urls) <= 25:
        raise ValueError("enrich_people takes 1 to 25 profile URLs")
    body = {"professional_network_profile_urls": urls, "fields": PERSON_FIELDS}
    return post("/person/enrich", body, billed=True, fresh=fresh)


def identify_company(
    domain: str | None = None, name: str | None = None, fresh: bool | None = None
) -> Any:
    if domain:
        body: dict[str, Any] = {"domains": [domain]}
    elif name:
        body = {"names": [name], "exact_match": True}
    else:
        raise ValueError("identify_company needs a domain or a name")
    return post("/company/identify", body, billed=True, fresh=fresh)

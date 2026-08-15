"""FPL API client — polite, cached, and able to run with the API switched off.

Three layers, in order of preference:

1. ``raw/gw{n}/`` — **immutable snapshots.** Once a gameweek is Final its inputs
   can never change, so they are written once, committed, and read from disk
   forever after. This is what makes the whole ledger reproducible offline and
   keeps steady-state traffic at roughly 20 requests per run.
2. ``.cache/`` — an ETag/``If-None-Match`` HTTP cache for the endpoints that do
   move (bootstrap, fixtures, standings). A 304 costs no body transfer.
3. the network — sequential, delayed, backed off, and capped.

If a fetch fails and no cached copy exists, the caller is expected to abandon
the run *without publishing*. A stale ``data.json`` that stays put is strictly
better than a fresh one built from half an API, and ageing ``generated_at``
fires the §9.5 stale banner, which is the loud signal the old sheet never had.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

from .config import (
    CACHE,
    FPL_BASE,
    MAX_REQUESTS_PER_RUN,
    RAW,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT,
    RETRY_BACKOFF,
    USER_AGENT,
)

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """A request failed and nothing usable was cached."""


class BudgetExceeded(FetchError):
    """The per-run request cap was hit. Almost certainly a loop bug."""


class Fetcher:
    def __init__(self, *, offline: bool = False, cache_dir: Path | None = None,
                 raw_dir: Path | None = None):
        self.offline = offline
        self.cache_dir = cache_dir or CACHE
        self.raw_dir = raw_dir or RAW
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.requests_made = 0
        self.cache_hits = 0
        self.snapshot_hits = 0
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        self._last_request = 0.0

    # -- plumbing -------------------------------------------------------------

    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(url.encode()).hexdigest()[:20]
        return self.cache_dir / f"{digest}.json", self.cache_dir / f"{digest}.meta"

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)
        self._last_request = time.monotonic()

    def get(self, path: str, *, allow_404: bool = False) -> Any:
        """GET an endpoint, preferring the HTTP cache and falling back to it."""
        url = f"{FPL_BASE}{path}"
        body_path, meta_path = self._cache_paths(url)

        cached = None
        etag = None
        if body_path.exists():
            try:
                cached = json.loads(body_path.read_text())
                if meta_path.exists():
                    etag = json.loads(meta_path.read_text()).get("etag")
            except (json.JSONDecodeError, OSError):
                cached = None

        if self.offline:
            if cached is not None:
                self.cache_hits += 1
                return cached
            raise FetchError(f"offline and nothing cached for {path}")

        if self.requests_made >= MAX_REQUESTS_PER_RUN:
            raise BudgetExceeded(
                f"request cap of {MAX_REQUESTS_PER_RUN} reached at {path}"
            )

        headers = {"If-None-Match": etag} if etag else {}
        last_error: Exception | None = None

        for attempt, backoff in enumerate([0, *RETRY_BACKOFF]):
            if backoff:
                time.sleep(backoff)
            try:
                self._throttle()
                self.requests_made += 1
                response = self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

                if response.status_code == 304 and cached is not None:
                    self.cache_hits += 1
                    return cached
                if response.status_code == 404 and allow_404:
                    return None
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = FetchError(f"{response.status_code} for {path}")
                    log.warning("%s on %s, attempt %d", response.status_code, path, attempt + 1)
                    continue

                response.raise_for_status()
                payload = response.json()
                body_path.write_text(json.dumps(payload))
                if response.headers.get("ETag"):
                    meta_path.write_text(json.dumps({"etag": response.headers["ETag"]}))
                return payload
            except (requests.RequestException, json.JSONDecodeError) as exc:
                last_error = exc
                log.warning("fetch %s failed (attempt %d): %s", path, attempt + 1, exc)

        if cached is not None:
            log.warning("serving stale cache for %s after repeated failures", path)
            self.cache_hits += 1
            return cached
        raise FetchError(f"could not fetch {path}: {last_error}")

    # -- endpoints ------------------------------------------------------------

    def bootstrap(self) -> dict:
        return self.get("/bootstrap-static/")

    def fixtures(self) -> list[dict]:
        return self.get("/fixtures/")

    def league_standings(self, league_id: int) -> dict:
        """Members and entry_ids. Pre-season they appear under ``new_entries``."""
        first = self.get(f"/leagues-classic/{league_id}/standings/")
        results = list(first.get("standings", {}).get("results", []))
        page = 1
        while first.get("standings", {}).get("has_next") and page < 20:
            page += 1
            nxt = self.get(f"/leagues-classic/{league_id}/standings/?page_standings={page}")
            page_results = nxt.get("standings", {}).get("results", [])
            if not page_results:
                break
            results.extend(page_results)
            if not nxt.get("standings", {}).get("has_next"):
                break
        first.setdefault("standings", {})["results"] = results
        return first

    def entry(self, entry_id: int) -> dict | None:
        return self.get(f"/entry/{entry_id}/", allow_404=True)

    def entry_history(self, entry_id: int) -> dict | None:
        return self.get(f"/entry/{entry_id}/history/", allow_404=True)

    def entry_picks(self, entry_id: int, gw: int) -> dict | None:
        """Squad and multipliers. Returns None before the deadline passes."""
        return self.get(f"/entry/{entry_id}/event/{gw}/picks/", allow_404=True)

    def event_live(self, gw: int) -> dict | None:
        """Per-player stats for a gameweek. Empty before kickoff."""
        return self.get(f"/event/{gw}/live/", allow_404=True)

    def summary(self) -> str:
        return (
            f"{self.requests_made} requests, {self.cache_hits} cache hits, "
            f"{self.snapshot_hits} snapshot hits"
        )

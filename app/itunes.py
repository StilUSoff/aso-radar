"""Public iTunes Search / Lookup API client with rate limiting.

Apple allows roughly 20 requests per minute per IP, so every call goes
through a shared throttle.
"""

from __future__ import annotations

import os
import threading
import time

import httpx

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
SEARCH_LIMIT = 200
REQUEST_DELAY = float(os.environ.get("ITUNES_REQUEST_DELAY", "3.2"))

_lock = threading.Lock()
_last_call = 0.0
_client = httpx.Client(timeout=30, headers={"User-Agent": "aso-radar/1.0"})


class ItunesError(Exception):
    pass


def _get(url: str, params: dict) -> dict:
    global _last_call
    for attempt in range(5):
        with _lock:
            wait = _last_call + REQUEST_DELAY - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_call = time.monotonic()
        try:
            resp = _client.get(url, params=params)
        except httpx.HTTPError as e:
            err = str(e)
        else:
            if resp.status_code == 200:
                return resp.json()
            err = f"HTTP {resp.status_code}"
            if resp.status_code not in (403, 429, 500, 502, 503, 504):
                break
        # Throttled or transient failure: back off before retrying.
        time.sleep(min(60, 10 * 2**attempt))
    raise ItunesError(f"{url} failed: {err}")


def search(term: str, country: str) -> list[dict]:
    data = _get(
        SEARCH_URL,
        {"term": term, "country": country, "entity": "software", "limit": SEARCH_LIMIT},
    )
    return [
        {"id": r["trackId"], "name": r.get("trackName", ""), "icon": r.get("artworkUrl60", "")}
        for r in data.get("results", [])
        if "trackId" in r
    ]


def lookup(app_id: int, country: str = "us") -> dict | None:
    data = _get(LOOKUP_URL, {"id": app_id, "country": country})
    results = data.get("results") or []
    return results[0] if results else None

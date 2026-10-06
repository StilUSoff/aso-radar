"""App Store clients: search suggestions (hints) and app search.

Every endpoint has its own throttle. Search is limited by Apple to ~20
requests/minute per IP; hints tolerate much more, but we stay polite.
"""

from __future__ import annotations

import html
import os
import re
import threading
import time

import httpx

HINTS_URL = "https://search.itunes.apple.com/WebObjects/MZSearchHints.woa/wa/hints"
SEARCH_URL = "https://itunes.apple.com/search"
HINTS_PER_PAGE = 10

_client = httpx.Client(timeout=30, headers={"User-Agent": "AppStore/3.0 iOS/17.0 model/iPhone15,2"})
_HINT_TERM = re.compile(r"<key>term</key>\s*<string>(.*?)</string>", re.S)


class ItunesError(Exception):
    pass


class _Throttle:
    def __init__(self, delay: float):
        self.delay = delay
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self) -> None:
        with self.lock:
            pause = self.last + self.delay - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            self.last = time.monotonic()


_hints_throttle = _Throttle(float(os.environ.get("HINTS_REQUEST_DELAY", "0.35")))
_search_throttle = _Throttle(float(os.environ.get("SEARCH_REQUEST_DELAY", "3.2")))


def _get(url: str, throttle: _Throttle, **kwargs) -> httpx.Response:
    err = "?"
    for attempt in range(5):
        throttle.wait()
        try:
            resp = _client.get(url, **kwargs)
        except httpx.HTTPError as e:
            err = str(e)
        else:
            if resp.status_code == 200:
                return resp
            err = f"HTTP {resp.status_code}"
            if resp.status_code not in (403, 429, 500, 502, 503, 504):
                break
        # Throttled or transient failure: back off before retrying.
        time.sleep(min(120, 10 * 2**attempt))
    raise ItunesError(f"{url} failed: {err}")


def hints(prefix: str, storefront: int) -> list[str]:
    """Search suggestions for a typed prefix, most popular first."""
    resp = _get(
        HINTS_URL, _hints_throttle,
        params={"clientApplication": "Software", "term": prefix},
        headers={"X-Apple-Store-Front": f"{storefront}-1,29"},
    )
    seen, out = set(), []
    for raw in _HINT_TERM.findall(resp.text):
        term = " ".join(html.unescape(raw).split()).lower()
        if term and term not in seen:
            seen.add(term)
            out.append(term)
    return out


def search(term: str, country: str, limit: int = 5) -> list[dict]:
    """Top apps App Store shows for a query."""
    data = _get(
        SEARCH_URL, _search_throttle,
        params={"term": term, "country": country, "entity": "software", "limit": limit},
    ).json()
    return [
        {"id": r["trackId"], "name": r.get("trackName", ""), "icon": r.get("artworkUrl60", ""),
         "seller": r.get("sellerName", ""), "url": r.get("trackViewUrl", "")}
        for r in data.get("results", [])
        if "trackId" in r
    ]

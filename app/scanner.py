"""Mines App Store search suggestions per market and ranks them by popularity.

For every letter of a market's alphabets we ask App Store for suggestions,
then go one level deeper: every two-letter combination for small alphabets
(depth 2), or only the continuations seen in the suggestions otherwise.
Apple orders suggestions by popularity; see rank_terms() for the scoring.

Scores are normalized so the market's top term is 100. This is a relative
popularity, not a search volume: Apple does not publish volumes.
"""

from __future__ import annotations

import calendar
import logging
import os
import threading
import time

from . import db, itunes
from .markets import ALPHABETS, MARKETS, scan_units

log = logging.getLogger("aso.scanner")

SCAN_INTERVAL_HOURS = float(os.environ.get("SCAN_INTERVAL_HOURS", "24"))
TOP_N = int(os.environ.get("TOP_N", "500"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "180"))
FOLLOW_ESTIMATE = 7  # average continuations followed per letter (progress estimate)

_lock = threading.Lock()


class ScanInProgress(Exception):
    pass


def estimate_prefixes(unit: dict) -> int:
    total = 0
    for seed in unit["seeds"]:
        n = len(ALPHABETS[seed["alphabet"]])
        total += n + (n * n if seed["depth"] == 2 else n * FOLLOW_ESTIMATE)
    return total


def start(locales: list[str] | None = None) -> int:
    """Start a scan in the background; returns its id."""
    units = scan_units()
    if locales:
        keys = {MARKETS[l]["scan_key"] for l in locales if l in MARKETS}
        units = [u for u in units if u["scan_key"] in keys]
    if not units:
        raise ValueError("No markets to scan")
    if not _lock.acquire(blocking=False):
        raise ScanInProgress()
    try:
        with db.tx() as conn:
            scan_id = conn.execute(
                "INSERT INTO scans (started_at, status, partial, total) VALUES (?, 'running', ?, ?)",
                (db.now(), int(bool(locales)), sum(estimate_prefixes(u) for u in units)),
            ).lastrowid
    except Exception:
        _lock.release()
        raise
    threading.Thread(target=_run, args=(scan_id, units), daemon=True).start()
    return scan_id


def _progress(scan_id: int, done: int, current: str | None = None) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE scans SET done = ?, current = COALESCE(?, current) WHERE id = ?",
                     (done, current, scan_id))


def _run(scan_id: int, units: list[dict]) -> None:
    done = 0
    try:
        for unit in units:
            _progress(scan_id, done, unit["name"])
            done = _scan_unit(scan_id, unit, done)
        with db.tx() as conn:
            conn.execute("UPDATE scans SET status='done', finished_at=?, done=? WHERE id=?",
                         (db.now(), done, scan_id))
            conn.execute(
                "DELETE FROM scans WHERE started_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
                (f"-{RETENTION_DAYS} days",),
            )
    except Exception as e:
        log.exception("scan %s failed", scan_id)
        with db.tx() as conn:
            conn.execute("UPDATE scans SET status='failed', error=?, finished_at=? WHERE id=?",
                         (str(e), db.now(), scan_id))
    finally:
        _lock.release()


def _scan_unit(scan_id: int, unit: dict, done: int) -> int:
    """Probe one storefront+alphabets combination and store its top terms."""
    # (term, prefix, position) for every suggestion seen
    seen: list[tuple[str, str, int]] = []
    probed = failures = 0

    def probe(prefix: str) -> list[str]:
        nonlocal probed, failures
        try:
            terms = itunes.hints(prefix, unit["storefront"])
        except itunes.ItunesError as e:
            failures += 1
            log.warning("%s %r: %s", unit["locale"], prefix, e)
            if failures > 20 and failures > probed // 4:
                raise
            return []
        probed += 1
        seen.extend((t, prefix, pos) for pos, t in enumerate(terms, 1))
        return terms

    for seed in unit["seeds"]:
        letters = ALPHABETS[seed["alphabet"]]
        for a in letters:
            terms = probe(a)
            if len(terms) >= itunes.HINTS_PER_PAGE:
                # A letter with less than a full page has nothing deeper.
                if seed["depth"] == 2:
                    children = list(letters)
                else:
                    # Follow only the continuations people actually type.
                    children = sorted({t[1] for t in terms if t.startswith(a) and len(t) > 1 and not t[1].isspace()})
                for b in children:
                    probe(a + b)
            done += 1 + (len(letters) if seed["depth"] == 2 else FOLLOW_ESTIMATE)
            _progress(scan_id, done)

    ranked = rank_terms(seen)[:TOP_N]
    top = ranked[0][1]["score"] if ranked else 1
    with db.tx() as conn:
        ms_id = conn.execute(
            "INSERT INTO market_scans (scan_id, scan_key, scanned_at, prefixes, unique_terms)"
            " VALUES (?, ?, ?, ?, ?)",
            (scan_id, unit["scan_key"], db.now(), probed, len({t for t, _, _ in seen})),
        ).lastrowid
        conn.executemany(
            "INSERT INTO keywords (market_scan_id, term, rank, score, hits, best_prefix, best_pos)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(ms_id, term, i, round(s["score"] / top * 100, 2), s["hits"], s["best"][0], s["best"][1])
             for i, (term, s) in enumerate(ranked, 1)],
        )
    log.info("%s: %d prefixes, %d unique terms", unit["locale"], probed, len(ranked))
    _progress(scan_id, done)
    return done


def rank_terms(seen: list[tuple[str, str, int]]) -> list[tuple[str, dict]]:
    """Estimate popularity from where suggestions appeared.

    Suggestions for a prefix are its most popular queries, in order, so within
    one prefix the order is exact. To compare prefixes we estimate how many
    popular queries live under each first letter (distinct terms found in its
    subtree) and split that evenly among the letter's probed children. A term
    at position i of a prefix of size N gets N / i; its score is the best
    estimate over all prefixes that suggested it.
    """
    subtree: dict[str, set] = {}
    children: dict[str, set] = {}
    for term, prefix, _ in seen:
        subtree.setdefault(prefix[0], set()).add(term)
        if len(prefix) > 1:
            children.setdefault(prefix[0], set()).add(prefix)

    def size(prefix: str) -> float:
        n = len(subtree.get(prefix[0], ())) or 1
        return n if len(prefix) == 1 else n / max(1, len(children.get(prefix[0], ())))

    stats: dict[str, dict] = {}
    for term, prefix, pos in seen:
        s = stats.setdefault(term, {"score": 0.0, "hits": 0, "best": None})
        s["hits"] += 1
        est = size(prefix) / pos
        if est > s["score"]:
            s["score"] = est
        if s["best"] is None or (len(prefix), pos) < (len(s["best"][0]), s["best"][1]):
            s["best"] = (prefix, pos)
    return sorted(stats.items(), key=lambda kv: (-kv[1]["score"], -kv[1]["hits"], kv[0]))


def scheduler_loop() -> None:
    """Start a full scan whenever the last one is older than the interval."""
    if SCAN_INTERVAL_HOURS <= 0:
        return
    while True:
        try:
            with db.tx() as conn:
                last = conn.execute("SELECT started_at FROM scans WHERE partial = 0 ORDER BY id DESC LIMIT 1").fetchone()
            age = time.time() - calendar.timegm(time.strptime(last["started_at"], "%Y-%m-%dT%H:%M:%SZ")) \
                if last else None
            if age is None or age >= SCAN_INTERVAL_HOURS * 3600:
                log.info("scheduled scan started: %s", start())
        except ScanInProgress:
            pass
        except Exception:
            log.exception("scheduler error")
        time.sleep(300)

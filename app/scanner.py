"""Mines App Store search suggestions per market and ranks them by popularity.

For every letter of a market's alphabets we ask App Store for suggestions,
then go one level deeper: every two-letter combination for small alphabets
(depth 2), or only the continuations seen in the suggestions otherwise.
Finally the most common inner words of the found queries are probed too:
App Store matches any word of a query, so those lists mix first letters.
Apple orders every list by popularity; ranking.fit() merges all lists into
one popularity scale.

Scores are normalized so the market's top term is 100. This is a relative
popularity, not a search volume: Apple does not publish volumes.
"""

from __future__ import annotations

import calendar
import logging
import os
import threading
import time

from . import db, itunes, ranking
from .markets import ALPHABETS, MARKETS, scan_units

log = logging.getLogger("aso.scanner")

SCAN_INTERVAL_HOURS = float(os.environ.get("SCAN_INTERVAL_HOURS", "24"))
TOP_N = int(os.environ.get("TOP_N", "500"))
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "180"))
FOLLOW_ESTIMATE = 7  # average continuations followed per letter (progress estimate)
BRIDGE_WORDS = int(os.environ.get("BRIDGE_WORDS", "150"))

_lock = threading.Lock()


class ScanInProgress(Exception):
    pass


def estimate_prefixes(unit: dict) -> int:
    total = 0
    for seed in unit["seeds"]:
        n = len(ALPHABETS[seed["alphabet"]])
        total += n + (n * n if seed["depth"] == 2 else n * FOLLOW_ESTIMATE)
    return total + BRIDGE_WORDS


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
    lists: list[tuple[str, list[str]]] = []
    probed: set[str] = set()
    failures = 0

    def probe(prefix: str) -> list[str]:
        nonlocal failures
        probed.add(prefix)
        try:
            terms = itunes.hints(prefix, unit["storefront"])
        except itunes.ItunesError as e:
            failures += 1
            log.warning("%s %r: %s", unit["locale"], prefix, e)
            if failures > 20 and failures > len(probed) // 4:
                raise
            return []
        lists.append((prefix, terms))
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

    # Words found inside queries link lists of different first letters.
    for word in ranking.bridge_words(lists, probed, BRIDGE_WORDS):
        probe(word)
    done += BRIDGE_WORDS
    _progress(scan_id, done)

    strength = ranking.fit(lists)
    hits: dict[str, int] = {}
    best: dict[str, tuple[str, int]] = {}
    for prefix, terms in lists:
        for pos, t in enumerate(terms, 1):
            hits[t] = hits.get(t, 0) + 1
            if t not in best or (len(prefix), pos) < (len(best[t][0]), best[t][1]):
                best[t] = (prefix, pos)
    ranked = sorted(strength.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]
    top = ranked[0][1] if ranked else 1.0

    with db.tx() as conn:
        ms_id = conn.execute(
            "INSERT INTO market_scans (scan_id, scan_key, scanned_at, prefixes, unique_terms)"
            " VALUES (?, ?, ?, ?, ?)",
            (scan_id, unit["scan_key"], db.now(), len(lists), len(strength)),
        ).lastrowid
        conn.executemany(
            "INSERT INTO keywords (market_scan_id, term, rank, score, hits, best_prefix, best_pos)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(ms_id, t, i, round(theta / top * 100, 3), hits[t], best[t][0], best[t][1])
             for i, (t, theta) in enumerate(ranked, 1)],
        )
    log.info("%s: %d prefixes, %d unique terms", unit["locale"], len(lists), len(strength))
    _progress(scan_id, done)
    return done


def _age_hours(conn, where: str) -> float | None:
    row = conn.execute(f"SELECT started_at FROM scans WHERE partial = 0 AND {where} ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    return (time.time() - calendar.timegm(time.strptime(row["started_at"], "%Y-%m-%dT%H:%M:%SZ"))) / 3600


def scheduler_loop() -> None:
    """Start a full scan when the last good one is older than the interval.

    Scans cut short by a restart are redone right away; scans that failed on
    Apple's side are retried after an hour.
    """
    if SCAN_INTERVAL_HOURS <= 0:
        return
    while True:
        try:
            with db.tx() as conn:
                ok = _age_hours(conn, "status IN ('done', 'running')")
                failed = _age_hours(conn, "status = 'failed' AND error != 'interrupted'")
            if (ok is None or ok >= SCAN_INTERVAL_HOURS) and (failed is None or failed >= 1):
                log.info("scheduled scan started: %s", start())
        except ScanInProgress:
            pass
        except Exception:
            log.exception("scheduler error")
        time.sleep(300)

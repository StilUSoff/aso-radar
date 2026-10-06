"""Rank checks: one background run at a time, plus a periodic scheduler."""

from __future__ import annotations

import calendar
import json
import logging
import os
import threading
import time

from . import db, itunes

log = logging.getLogger("aso.checker")

CHECK_INTERVAL_HOURS = float(os.environ.get("CHECK_INTERVAL_HOURS", "24"))

_run_lock = threading.Lock()


class RunInProgress(Exception):
    pass


def start(app_id: int | None = None) -> int:
    """Start a run in the background and return its id."""
    if not _run_lock.acquire(blocking=False):
        raise RunInProgress()
    try:
        with db.tx() as conn:
            q = "SELECT id, app_id, country, term FROM keywords"
            rows = conn.execute(q + (" WHERE app_id = ?" if app_id else ""),
                                (app_id,) if app_id else ()).fetchall()
            run_id = conn.execute(
                "INSERT INTO runs (app_id, started_at, status, total) VALUES (?, ?, 'running', ?)",
                (app_id, db.now(), len(rows)),
            ).lastrowid
    except Exception:
        _run_lock.release()
        raise
    threading.Thread(target=_execute, args=(run_id, [dict(r) for r in rows]), daemon=True).start()
    return run_id


def _execute(run_id: int, keywords: list[dict]) -> None:
    try:
        # Same term+country may be tracked for several apps: search it once.
        groups: dict[tuple[str, str], list[dict]] = {}
        for kw in keywords:
            groups.setdefault((kw["term"].lower(), kw["country"]), []).append(kw)

        done = 0
        for (term, country), kws in groups.items():
            try:
                results = itunes.search(term, country)
            except itunes.ItunesError as e:
                log.warning("search failed for %r/%s: %s", term, country, e)
                results = None
            if results is not None:
                ids = [r["id"] for r in results]
                top = json.dumps(results[:3], ensure_ascii=False)
                with db.tx() as conn:
                    for kw in kws:
                        rank = ids.index(kw["app_id"]) + 1 if kw["app_id"] in ids else None
                        conn.execute(
                            "INSERT INTO checks (keyword_id, run_id, checked_at, rank, total_results, top_apps)"
                            " VALUES (?, ?, ?, ?, ?, ?)",
                            (kw["id"], run_id, db.now(), rank, len(results), top),
                        )
            done += len(kws)
            with db.tx() as conn:
                conn.execute("UPDATE runs SET done = ? WHERE id = ?", (done, run_id))

        with db.tx() as conn:
            conn.execute("UPDATE runs SET status='done', finished_at=? WHERE id=?", (db.now(), run_id))
    except Exception as e:  # keep the run row honest
        log.exception("run %s failed", run_id)
        with db.tx() as conn:
            conn.execute("UPDATE runs SET status='failed', error=?, finished_at=? WHERE id=?",
                         (str(e), db.now(), run_id))
    finally:
        _run_lock.release()


def scheduler_loop() -> None:
    """Start a full run whenever the last one is older than the interval."""
    if CHECK_INTERVAL_HOURS <= 0:
        return
    while True:
        try:
            with db.tx() as conn:
                last = conn.execute(
                    "SELECT started_at FROM runs WHERE app_id IS NULL ORDER BY id DESC LIMIT 1"
                ).fetchone()
                has_keywords = conn.execute("SELECT 1 FROM keywords LIMIT 1").fetchone()
            due = last is None or (
                time.time() - calendar.timegm(time.strptime(last["started_at"], "%Y-%m-%dT%H:%M:%SZ"))
                >= CHECK_INTERVAL_HOURS * 3600
            )
            if due and has_keywords:
                log.info("scheduled run started: %s", start())
        except RunInProgress:
            pass
        except Exception:
            log.exception("scheduler error")
        time.sleep(300)

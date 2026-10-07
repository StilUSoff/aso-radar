"""Where an app ranks in App Store search for chosen queries, per market.

For every tracked (query, market) the iTunes Search API returns the top 200
apps of that storefront; the app's place in that list is its position.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import threading
import time

from . import db, itunes

log = logging.getLogger("aso.apps")

CHECK_INTERVAL_HOURS = float(os.environ.get("APP_CHECK_INTERVAL_HOURS", "24"))
SEARCH_DEPTH = 200

_lock = threading.Lock()
_cancel = threading.Event()


class RunInProgress(Exception):
    pass


def stop() -> bool:
    """Ask the running check to stop; positions checked so far are kept."""
    if not _lock.locked():
        return False
    _cancel.set()
    return True


def start(app_id: int | None = None) -> int:
    """Check positions in the background; returns the run id."""
    if not _lock.acquire(blocking=False):
        raise RunInProgress()
    _cancel.clear()
    try:
        with db.tx() as conn:
            q = "SELECT id, app_id, locale, country, term FROM app_keywords"
            rows = conn.execute(q + (" WHERE app_id = ?" if app_id else ""),
                                (app_id,) if app_id else ()).fetchall()
            run_id = conn.execute(
                "INSERT INTO app_runs (app_id, started_at, status, total) VALUES (?, ?, 'running', ?)",
                (app_id, db.now(), len(rows)),
            ).lastrowid
    except Exception:
        _lock.release()
        raise
    threading.Thread(target=_run, args=(run_id, [dict(r) for r in rows]), daemon=True).start()
    return run_id


def _run(run_id: int, keywords: list[dict]) -> None:
    try:
        # The same query in one storefront is searched once for all apps/markets.
        groups: dict[tuple[str, str], list[dict]] = {}
        for kw in keywords:
            groups.setdefault((kw["term"], kw["country"]), []).append(kw)

        done = 0
        for (term, country), kws in groups.items():
            if _cancel.is_set():
                with db.tx() as conn:
                    conn.execute("UPDATE app_runs SET status='stopped', finished_at=? WHERE id=?", (db.now(), run_id))
                return
            try:
                results = itunes.search(term, country, limit=SEARCH_DEPTH)
            except itunes.ItunesError as e:
                log.warning("search failed for %r/%s: %s", term, country, e)
                results = None
            if results is not None:
                ids = [r["id"] for r in results]
                top = json.dumps([{k: r[k] for k in ("id", "name", "icon")} for r in results[:3]],
                                 ensure_ascii=False)
                with db.tx() as conn:
                    for kw in kws:
                        pos = ids.index(kw["app_id"]) if kw["app_id"] in ids else None
                        conn.execute(
                            "INSERT INTO app_checks (keyword_id, run_id, checked_at, rank, total_results, top_apps)"
                            " VALUES (?, ?, ?, ?, ?, ?)",
                            (kw["id"], run_id, db.now(), pos + 1 if pos is not None else None, len(results), top),
                        )
                        if pos is not None:
                            # The listing title this storefront shows for the app.
                            conn.execute(
                                "INSERT INTO app_meta (app_id, locale, store_title, updated_at) VALUES (?, ?, ?, ?)"
                                " ON CONFLICT(app_id, locale) DO UPDATE SET store_title = excluded.store_title",
                                (kw["app_id"], kw["locale"], results[pos]["name"], db.now()),
                            )
            done += len(kws)
            with db.tx() as conn:
                conn.execute("UPDATE app_runs SET done = ? WHERE id = ?", (done, run_id))

        with db.tx() as conn:
            conn.execute("UPDATE app_runs SET status='done', finished_at=? WHERE id=?", (db.now(), run_id))
    except Exception as e:
        log.exception("app run %s failed", run_id)
        with db.tx() as conn:
            conn.execute("UPDATE app_runs SET status='failed', error=?, finished_at=? WHERE id=?",
                         (str(e), db.now(), run_id))
    finally:
        _lock.release()


def scheduler_loop() -> None:
    """Re-check all apps when the last full run is older than the interval."""
    if CHECK_INTERVAL_HOURS <= 0:
        return
    while True:
        try:
            with db.tx() as conn:
                last = conn.execute(
                    "SELECT started_at FROM app_runs WHERE app_id IS NULL AND error IS NOT 'interrupted'"
                    " AND status != 'failed'"
                    " ORDER BY id DESC LIMIT 1"
                ).fetchone()
                has_keywords = conn.execute("SELECT 1 FROM app_keywords LIMIT 1").fetchone()
            age = time.time() - calendar.timegm(time.strptime(last["started_at"], "%Y-%m-%dT%H:%M:%SZ")) \
                if last else None
            if has_keywords and (age is None or age >= CHECK_INTERVAL_HOURS * 3600):
                log.info("scheduled app check started: %s", start())
        except RunInProgress:
            pass
        except Exception:
            log.exception("app scheduler error")
        time.sleep(300)

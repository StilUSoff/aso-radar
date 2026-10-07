"""ASO Radar: top App Store search queries per market, and app positions for them."""

from __future__ import annotations

import csv
import io
import logging
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import apps_api, apptracker, db, itunes, scanner, schedule
from .markets import MARKETS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC = Path(__file__).parent / "static"
USER = os.environ.get("DASHBOARD_USER", "admin")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
TREND_POINTS = 12
TILE_TOP = 100  # "new" / "rising" are counted within this many top terms

security = HTTPBasic(auto_error=False)


def auth(request: Request, creds: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not PASSWORD or request.url.path == "/healthz":
        return  # auth disabled (local development) or container healthcheck
    if creds and secrets.compare_digest(creds.username.encode(), USER.encode()) \
            and secrets.compare_digest(creds.password.encode(), PASSWORD.encode()):
        return
    raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="ASO Radar"'})


app = FastAPI(title="ASO Radar", dependencies=[Depends(auth)], docs_url=None, redoc_url=None)
app.include_router(apps_api.router)


@app.on_event("startup")
def startup() -> None:
    db.init()
    threading.Thread(target=scanner.scheduler_loop, daemon=True).start()
    threading.Thread(target=apptracker.scheduler_loop, daemon=True).start()


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def market_or_404(locale: str) -> dict:
    m = MARKETS.get(locale)
    if not m:
        raise HTTPException(404, "Unknown market")
    return m


def public(m: dict) -> dict:
    return {k: v for k, v in m.items() if k != "scan_key"}


def recent_scans(conn, scan_key: str, n: int) -> list[dict]:
    """Latest market scans of a scan unit, newest first."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM market_scans WHERE scan_key = ? ORDER BY id DESC LIMIT ?", (scan_key, n)
    ).fetchall()]


def ranks_of(conn, market_scan_id: int, limit: int) -> dict[str, int]:
    return {r["term"]: r["rank"] for r in conn.execute(
        "SELECT term, rank FROM keywords WHERE market_scan_id = ? AND rank <= ?", (market_scan_id, limit)
    ).fetchall()}


# ---------- meta ----------

@app.get("/api/meta")
def meta():
    return {
        "markets": [public(m) for m in MARKETS.values()],
        "scan_interval_hours": scanner.SCAN_INTERVAL_HOURS,
        "schedule": schedule.describe(scanner.SCAN_INTERVAL_HOURS),
        "schedule_days": 7 if schedule.WEEKDAYS else scanner.SCAN_INTERVAL_HOURS / 24,
        "hints_per_hour": itunes.HINTS_PER_HOUR,
        "top_n": scanner.TOP_N,
    }


# ---------- markets ----------

@app.get("/api/markets")
def markets_overview():
    """Tile data for every market: freshness, leaders and movement in the top."""
    out, cache = [], {}
    with db.tx() as conn:
        for m in MARKETS.values():
            key = m["scan_key"]
            if key not in cache:
                scans = recent_scans(conn, key, 2)
                info = {"scanned_at": None, "unique_terms": 0, "prefixes": 0, "leaders": [],
                        "new": 0, "rising": 0, "falling": 0, "has_previous": len(scans) > 1}
                if scans:
                    cur = scans[0]
                    info.update(scanned_at=cur["scanned_at"], unique_terms=cur["unique_terms"],
                                prefixes=cur["prefixes"])
                    now_ranks = ranks_of(conn, cur["id"], TILE_TOP)
                    info["leaders"] = [t for t, _ in sorted(now_ranks.items(), key=lambda kv: kv[1])[:3]]
                    if len(scans) > 1:
                        prev = ranks_of(conn, scans[1]["id"], TILE_TOP)
                        for term, r in now_ranks.items():
                            if term not in prev:
                                info["new"] += 1
                            elif prev[term] - r >= 5:
                                info["rising"] += 1
                            elif r - prev[term] >= 5:
                                info["falling"] += 1
                cache[key] = info
            out.append({"locale": m["locale"], "name": m["name"], "country": m["country"],
                        "shares_with": m["shares_with"], **cache[key]})
    return out


@app.get("/api/markets/{locale}")
def market_keywords(locale: str, limit: int = Query(100, le=1000), offset: int = 0, q: str = ""):
    m = market_or_404(locale)
    with db.tx() as conn:
        scans = recent_scans(conn, m["scan_key"], TREND_POINTS)
        if not scans:
            return {"market": public(m), "scan": None, "has_previous": False,
                    "trend_dates": [], "total": 0, "items": []}
        cur = scans[0]
        where, args = "market_scan_id = ?", [cur["id"]]
        if q.strip():
            where += " AND term LIKE ?"
            args.append(f"%{q.strip().lower()}%")
        total = conn.execute(f"SELECT COUNT(*) FROM keywords WHERE {where}", args).fetchone()[0]
        rows = [dict(r) for r in conn.execute(
            f"SELECT term, rank, score, hits, best_prefix, best_pos FROM keywords WHERE {where}"
            " ORDER BY rank LIMIT ? OFFSET ?", args + [limit, offset]
        ).fetchall()]

        # Rank history of the listed terms over the recent scans.
        history: dict[str, dict[int, int]] = {}
        if rows:
            ids = [s["id"] for s in scans]
            terms = [r["term"] for r in rows]
            for h in conn.execute(
                f"SELECT market_scan_id, term, rank FROM keywords"
                f" WHERE market_scan_id IN ({','.join('?' * len(ids))})"
                f" AND term IN ({','.join('?' * len(terms))})", ids + terms
            ).fetchall():
                history.setdefault(h["term"], {})[h["market_scan_id"]] = h["rank"]

    ordered = list(reversed(scans))  # oldest -> newest
    prev_id = scans[1]["id"] if len(scans) > 1 else None
    for r in rows:
        h = history.get(r["term"], {})
        r["trend"] = [h.get(s["id"]) for s in ordered]
        r["prev_rank"] = h.get(prev_id) if prev_id else None
        r["is_new"] = prev_id is not None and prev_id not in h
    return {"market": public(m), "scan": cur, "has_previous": prev_id is not None,
            "trend_dates": [s["scanned_at"] for s in ordered], "total": total, "items": rows}


@app.get("/api/search")
def search_keyword(q: str = Query(..., min_length=2), limit: int = 40):
    """Where a query (substring) shows up across markets, by latest scans."""
    q = q.strip().lower()
    with db.tx() as conn:
        latest = {r["scan_key"]: r["id"] for r in conn.execute(
            "SELECT scan_key, MAX(id) AS id FROM market_scans GROUP BY scan_key").fetchall()}
        if not latest:
            return []
        rows = conn.execute(
            f"SELECT market_scan_id, term, rank, score FROM keywords"
            f" WHERE market_scan_id IN ({','.join('?' * len(latest))}) AND term LIKE ?",
            list(latest.values()) + [f"%{q}%"],
        ).fetchall()
    key_of_scan = {v: k for k, v in latest.items()}
    terms: dict[str, list] = {}
    for r in rows:
        key = key_of_scan[r["market_scan_id"]]
        for m in MARKETS.values():
            if m["scan_key"] == key:
                terms.setdefault(r["term"], []).append(
                    {"locale": m["locale"], "name": m["name"], "country": m["country"],
                     "rank": r["rank"], "score": r["score"]})
    out = [{"term": t, "markets": sorted(ms, key=lambda x: x["rank"])} for t, ms in terms.items()]
    out.sort(key=lambda x: (x["term"] != q, -len(x["markets"]), x["markets"][0]["rank"]))
    return out[:limit]


_apps_cache: dict[tuple[str, str], tuple[float, list]] = {}


@app.get("/api/top-apps")
def top_apps(term: str, country: str):
    """Apps App Store ranks first for a query (cached for an hour)."""
    key = (term.lower(), country.lower())
    hit = _apps_cache.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        apps = itunes.search(term, country)
    except itunes.RateLimited as e:
        raise HTTPException(429, f"Apple временно ограничил запросы, попробуйте через {e.retry_after // 60 + 1} мин")
    except itunes.ItunesError as e:
        raise HTTPException(502, str(e))
    _apps_cache[key] = (time.time(), apps)
    return apps


@app.get("/api/markets/{locale}/export.csv")
def export_market(locale: str):
    m = market_or_404(locale)
    data = market_keywords(locale, limit=1000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["market", "locale", "storefront", "rank", "keyword", "popularity", "previous_rank",
                "hits", "best_prefix", "best_position", "scanned_at"])
    scanned = data["scan"]["scanned_at"] if data["scan"] else ""
    for r in data["items"]:
        w.writerow([m["name"], locale, m["country"], r["rank"], r["term"], r["score"], r["prev_rank"] or "",
                    r["hits"], r["best_prefix"], r["best_pos"], scanned])
    return StreamingResponse(
        iter(["﻿" + buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="aso-{locale}.csv"'},
    )


# ---------- scans ----------

class ScanIn(BaseModel):
    locales: list[str] | None = None


@app.post("/api/scans")
def start_scan(body: ScanIn):
    try:
        return {"scan_id": scanner.start(body.locales)}
    except scanner.ScanInProgress:
        raise HTTPException(409, "A scan is already running")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/scans/stop")
def stop_scan():
    return {"stopping": scanner.stop()}


@app.get("/api/scans/latest")
def latest_scan():
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None

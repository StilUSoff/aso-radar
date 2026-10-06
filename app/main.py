"""ASO Radar: keyword rankings by App Store storefront."""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import secrets
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import asc, checker, db, itunes
from .locales import COUNTRIES, LOCALE_STOREFRONTS, storefronts_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC = Path(__file__).parent / "static"
USER = os.environ.get("DASHBOARD_USER", "admin")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

security = HTTPBasic(auto_error=False)


def auth(creds: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not PASSWORD:
        return  # auth disabled (local development)
    if creds and secrets.compare_digest(creds.username.encode(), USER.encode()) \
            and secrets.compare_digest(creds.password.encode(), PASSWORD.encode()):
        return
    raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="ASO Radar"'})


app = FastAPI(title="ASO Radar", dependencies=[Depends(auth)], docs_url=None, redoc_url=None)


@app.on_event("startup")
def startup() -> None:
    db.init()
    threading.Thread(target=checker.scheduler_loop, daemon=True).start()


@app.get("/healthz", dependencies=[])
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------- meta ----------

@app.get("/api/meta")
def meta():
    return {
        "countries": COUNTRIES,
        "locales": LOCALE_STOREFRONTS,
        "asc_configured": asc.configured(),
        "check_interval_hours": checker.CHECK_INTERVAL_HOURS,
    }


# ---------- apps ----------

class AppIn(BaseModel):
    app: str = Field(..., description="App Store ID or App Store URL")
    country: str = "us"


def get_app(conn, app_id: int):
    row = conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(404, "App not found")
    return row


@app.get("/api/apps")
def list_apps():
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT a.*, (SELECT COUNT(*) FROM keywords k WHERE k.app_id = a.id) AS keyword_count"
            " FROM apps a ORDER BY a.name"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/apps")
def add_app(body: AppIn):
    m = re.search(r"(?:id)?(\d{6,})", body.app)
    if not m:
        raise HTTPException(400, "Provide a numeric App Store ID or an apps.apple.com URL")
    app_id = int(m.group(1))
    try:
        info = itunes.lookup(app_id, body.country.lower())
    except itunes.ItunesError as e:
        raise HTTPException(502, str(e))
    if not info:
        raise HTTPException(404, f"App {app_id} not found in the '{body.country}' storefront")
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO apps (id, name, bundle_id, icon, seller, created_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name, icon=excluded.icon",
            (app_id, info.get("trackName", str(app_id)), info.get("bundleId"),
             info.get("artworkUrl100"), info.get("sellerName"), db.now()),
        )
        return dict(get_app(conn, app_id))


@app.delete("/api/apps/{app_id}")
def delete_app(app_id: int):
    with db.tx() as conn:
        conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))
    return {"ok": True}


# ---------- keywords ----------

class KeywordsIn(BaseModel):
    countries: list[str]
    terms: str = Field(..., description="Comma or newline separated")


def split_terms(text: str) -> list[str]:
    seen, out = set(), []
    for t in re.split(r"[,\n]", text):
        t = " ".join(t.split()).lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def insert_keywords(conn, app_id: int, pairs, source: str) -> int:
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO keywords (app_id, country, term, locale, source, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(app_id, c, t, loc, source, db.now()) for c, t, loc in pairs],
    )
    return conn.total_changes - before


@app.get("/api/apps/{app_id}/keywords")
def list_keywords(app_id: int):
    with db.tx() as conn:
        get_app(conn, app_id)
        rows = conn.execute(db.KEYWORDS_WITH_RANKS, {"app_id": app_id}).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["top_apps"] = json.loads(d["top_apps"]) if d["top_apps"] else []
        out.append(d)
    return out


@app.post("/api/apps/{app_id}/keywords")
def add_keywords(app_id: int, body: KeywordsIn):
    countries = [c.lower() for c in body.countries if c.lower() in COUNTRIES]
    terms = split_terms(body.terms)
    if not countries or not terms:
        raise HTTPException(400, "Pick at least one country and one keyword")
    with db.tx() as conn:
        get_app(conn, app_id)
        added = insert_keywords(conn, app_id, [(c, t, None) for c in countries for t in terms], "manual")
    return {"added": added}


@app.delete("/api/keywords/{keyword_id}")
def delete_keyword(keyword_id: int):
    with db.tx() as conn:
        conn.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
    return {"ok": True}


@app.get("/api/keywords/{keyword_id}/history")
def keyword_history(keyword_id: int):
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT checked_at, rank, total_results FROM checks WHERE keyword_id = ?"
            " ORDER BY checked_at DESC LIMIT 90",
            (keyword_id,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


class AscImportIn(BaseModel):
    include_secondary: bool = False
    replace: bool = False


@app.post("/api/apps/{app_id}/import-asc")
def import_asc(app_id: int, body: AscImportIn):
    with db.tx() as conn:
        get_app(conn, app_id)
    try:
        version, by_locale = asc.fetch_keywords(app_id)
    except asc.AscError as e:
        raise HTTPException(400, str(e))

    pairs, skipped = [], []
    for locale, words in by_locale.items():
        stores = storefronts_for(locale, body.include_secondary)
        if not stores:
            skipped.append(locale)
        pairs += [(c, w.lower(), locale) for c in stores for w in words]
    with db.tx() as conn:
        if body.replace:
            conn.execute("DELETE FROM keywords WHERE app_id = ? AND source = 'asc'", (app_id,))
        added = insert_keywords(conn, app_id, pairs, "asc")
    return {"version": version, "locales": len(by_locale), "added": added, "skipped_locales": skipped}


# ---------- summary / export ----------

def delta(r) -> int:
    """Positions gained since the previous check (entering top-200 counts as a gain)."""
    if not r["prev_checked_at"]:
        return 0
    cur = r["rank"] or 201
    prev = r["prev_rank"] or 201
    return prev - cur


def bucket_stats(rows) -> dict:
    ranks = [r["rank"] for r in rows if r["rank"] is not None]
    return {
        "keywords": len(rows),
        "checked": sum(1 for r in rows if r["checked_at"]),
        "ranked": len(ranks),
        "top10": sum(1 for r in ranks if r <= 10),
        "top50": sum(1 for r in ranks if r <= 50),
        "best": min(ranks) if ranks else None,
        "avg": round(sum(ranks) / len(ranks), 1) if ranks else None,
        "improved": sum(1 for r in rows if delta(r) > 0),
        "declined": sum(1 for r in rows if delta(r) < 0),
    }


@app.get("/api/apps/{app_id}/summary")
def summary(app_id: int):
    with db.tx() as conn:
        get_app(conn, app_id)
        rows = [dict(r) for r in conn.execute(db.KEYWORDS_WITH_RANKS, {"app_id": app_id}).fetchall()]
        last = conn.execute("SELECT MAX(checked_at) AS t FROM checks c JOIN keywords k ON k.id = c.keyword_id"
                            " WHERE k.app_id = ?", (app_id,)).fetchone()["t"]
    by_country: dict[str, list] = {}
    for r in rows:
        by_country.setdefault(r["country"], []).append(r)
    countries = [{"country": c, "name": COUNTRIES.get(c, c.upper()), **bucket_stats(rs)}
                 for c, rs in by_country.items()]
    countries.sort(key=lambda c: (-c["top10"], -c["top50"], -c["ranked"], c["country"]))
    return {"total": bucket_stats(rows), "countries": countries, "last_checked": last}


@app.get("/api/apps/{app_id}/export.csv")
def export_csv(app_id: int):
    rows = list_keywords(app_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["country", "keyword", "rank", "previous_rank", "results", "checked_at", "source", "locale", "top1"])
    for r in rows:
        w.writerow([r["country"], r["term"], r["rank"] or "", r["prev_rank"] or "", r["total_results"] or "",
                    r["checked_at"] or "", r["source"], r["locale"] or "",
                    r["top_apps"][0]["name"] if r["top_apps"] else ""])
    return StreamingResponse(
        iter(["﻿" + buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="aso-{app_id}.csv"'},
    )


# ---------- runs ----------

class RunIn(BaseModel):
    app_id: int | None = None


@app.post("/api/runs")
def start_run(body: RunIn):
    try:
        return {"run_id": checker.start(body.app_id)}
    except checker.RunInProgress:
        raise HTTPException(409, "A check is already running")


@app.get("/api/runs/latest")
def latest_run():
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None

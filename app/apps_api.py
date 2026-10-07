"""API for app tracking: positions of an app for chosen queries per market."""

from __future__ import annotations

import csv
import io
import json
import re
import statistics

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import apptracker, db, itunes
from .markets import MARKETS

router = APIRouter(prefix="/api")

TREND_POINTS = 12
LIMITS = {"title": 30, "subtitle": 30, "keywords": 100}

KEYWORD_CHECKS = """
SELECT k.id, k.locale, k.country, k.term,
       c.rank, c.checked_at, c.total_results, c.top_apps
FROM app_keywords k
LEFT JOIN app_checks c ON c.keyword_id = k.id
WHERE k.app_id = ?
ORDER BY k.id, c.checked_at DESC, c.id DESC
"""


def get_app(conn, app_id: int):
    row = conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(404, "App not found")
    return row


# ---------- apps ----------

class AppIn(BaseModel):
    app: str = Field(..., description="App Store ID or apps.apple.com URL")
    country: str = "us"


@router.get("/apps")
def list_apps():
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT a.*, (SELECT COUNT(*) FROM app_keywords k WHERE k.app_id = a.id) AS keyword_count"
            " FROM apps a ORDER BY a.name"
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/apps")
def add_app(body: AppIn):
    m = re.search(r"(?:id)?(\d{6,})", body.app)
    if not m:
        raise HTTPException(400, "Укажите App Store ID или ссылку apps.apple.com")
    app_id = int(m.group(1))
    url_country = re.search(r"apps\.apple\.com/([a-z]{2})/", body.app)
    country = (url_country.group(1) if url_country else body.country).lower()
    try:
        info = itunes.lookup(app_id, country)
    except itunes.ItunesError as e:
        raise HTTPException(502, str(e))
    if not info:
        raise HTTPException(404, f"Приложение {app_id} не найдено в витрине '{country}'")
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO apps (id, name, bundle_id, icon, seller, created_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name, icon=excluded.icon",
            (app_id, info.get("trackName", str(app_id)), info.get("bundleId"),
             info.get("artworkUrl100"), info.get("sellerName"), db.now()),
        )
        return dict(get_app(conn, app_id))


@router.delete("/apps/{app_id}")
def delete_app(app_id: int):
    with db.tx() as conn:
        conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))
    return {"ok": True}


# ---------- markets of an app ----------

def keyword_rows(conn, app_id: int) -> list[dict]:
    """One row per keyword: latest check, previous rank and trend."""
    out: dict[int, dict] = {}
    for r in conn.execute(KEYWORD_CHECKS, (app_id,)).fetchall():
        kw = out.get(r["id"])
        if kw is None:
            kw = out[r["id"]] = {
                "id": r["id"], "locale": r["locale"], "country": r["country"], "term": r["term"],
                "rank": r["rank"], "checked_at": r["checked_at"],
                "top_apps": json.loads(r["top_apps"]) if r["top_apps"] else [],
                "prev_rank": None, "prev_checked_at": None, "trend": [],
            }
        if r["checked_at"] is None:
            continue
        if len(kw["trend"]) == 1:
            kw["prev_rank"], kw["prev_checked_at"] = r["rank"], r["checked_at"]
        if len(kw["trend"]) < TREND_POINTS:
            kw["trend"].append(r["rank"])
    for kw in out.values():
        kw["trend"].reverse()  # oldest -> newest
    return list(out.values())


def grade(median: float | None, ranked: int, total: int) -> str:
    """Tile color: how the app does in a market overall."""
    if not ranked:
        return "none"
    coverage = ranked / total
    if median <= 25 and coverage >= 0.6:
        return "good"
    if median > 100 or coverage < 0.34:
        return "bad"
    return "mid"


@router.get("/apps/{app_id}/markets")
def app_markets(app_id: int):
    with db.tx() as conn:
        app = dict(get_app(conn, app_id))
        kws = keyword_rows(conn, app_id)
        metas = {r["locale"]: dict(r) for r in conn.execute(
            "SELECT * FROM app_meta WHERE app_id = ?", (app_id,)).fetchall()}

    by_locale: dict[str, list[dict]] = {}
    for k in kws:
        by_locale.setdefault(k["locale"], []).append(k)

    markets = []
    for loc, m in MARKETS.items():
        items = by_locale.get(loc)
        if not items:
            continue
        items.sort(key=lambda k: (k["rank"] or 100000, k["term"]))
        ranked = [k for k in items if k["rank"]]
        ranks = [k["rank"] for k in ranked]
        median = statistics.median(ranks) if ranks else None
        checked = [k["checked_at"] for k in items if k["checked_at"]]
        markets.append({
            "locale": loc, "name": m["name"], "country": m["country"],
            "keywords": len(items), "ranked": len(ranks),
            "median": round(median) if median is not None else None,
            "best": {"term": ranked[0]["term"], "rank": ranked[0]["rank"]} if ranked else None,
            "last_checked": max(checked) if checked else None,
            "grade": grade(median, len(ranks), len(items)),
            "meta": metas.get(loc), "items": items,
        })
    all_ranks = [k["rank"] for k in kws if k["rank"]]
    return {
        "app": app, "limits": LIMITS, "markets": markets,
        "total": {"keywords": len(kws), "ranked": len(all_ranks),
                  "top50": sum(1 for r in all_ranks if r <= 50), "top10": sum(1 for r in all_ranks if r <= 10)},
    }


class MetaIn(BaseModel):
    title: str = ""
    subtitle: str = ""
    keywords: str = ""


@router.put("/apps/{app_id}/meta/{locale}")
def save_meta(app_id: int, locale: str, body: MetaIn):
    if locale not in MARKETS:
        raise HTTPException(404, "Unknown market")
    with db.tx() as conn:
        get_app(conn, app_id)
        conn.execute(
            "INSERT INTO app_meta (app_id, locale, title, subtitle, keywords, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(app_id, locale) DO UPDATE SET title=excluded.title, subtitle=excluded.subtitle,"
            " keywords=excluded.keywords, updated_at=excluded.updated_at",
            (app_id, locale, body.title.strip(), body.subtitle.strip(), body.keywords.strip(), db.now()),
        )
    return {"ok": True}


# ---------- keywords ----------

class KeywordsIn(BaseModel):
    locales: list[str]
    terms: str = Field(..., description="Comma or newline separated")


def split_terms(text: str) -> list[str]:
    seen, out = set(), []
    for t in re.split(r"[,\n]", text):
        t = " ".join(t.split()).lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


@router.post("/apps/{app_id}/keywords")
def add_keywords(app_id: int, body: KeywordsIn):
    locales = [l for l in body.locales if l in MARKETS]
    terms = split_terms(body.terms)
    if not locales or not terms:
        raise HTTPException(400, "Выберите хотя бы один рынок и одно ключевое слово")
    with db.tx() as conn:
        get_app(conn, app_id)
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO app_keywords (app_id, locale, country, term, created_at) VALUES (?, ?, ?, ?, ?)",
            [(app_id, l, MARKETS[l]["country"], t, db.now()) for l in locales for t in terms],
        )
        added = conn.total_changes - before
    return {"added": added}


@router.delete("/app-keywords/{keyword_id}")
def delete_keyword(keyword_id: int):
    with db.tx() as conn:
        conn.execute("DELETE FROM app_keywords WHERE id = ?", (keyword_id,))
    return {"ok": True}


@router.get("/apps/{app_id}/export.csv")
def export_csv(app_id: int):
    data = app_markets(app_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["market", "locale", "storefront", "keyword", "position", "previous_position", "checked_at", "top1"])
    for m in data["markets"]:
        for k in m["items"]:
            w.writerow([m["name"], m["locale"], m["country"], k["term"], k["rank"] or ">200", k["prev_rank"] or "",
                        k["checked_at"] or "", k["top_apps"][0]["name"] if k["top_apps"] else ""])
    return StreamingResponse(
        iter(["﻿" + buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="aso-app-{app_id}.csv"'},
    )


# ---------- runs ----------

class RunIn(BaseModel):
    app_id: int | None = None


@router.post("/app-runs")
def start_run(body: RunIn):
    try:
        return {"run_id": apptracker.start(body.app_id)}
    except apptracker.RunInProgress:
        raise HTTPException(409, "Проверка позиций уже идёт")


@router.get("/app-runs/latest")
def latest_run():
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM app_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None

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

from . import apptracker, asc, db, itunes
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
        has_asc = conn.execute("SELECT 1 FROM asc_accounts LIMIT 1").fetchone() is not None
    for m in metas.values():
        m["iap_names"] = json.loads(m["iap_names"]) if m.get("iap_names") else []

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
        "app": app, "limits": LIMITS, "markets": markets, "asc_connected": has_asc,
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
            " keywords=excluded.keywords, source='manual', updated_at=excluded.updated_at",
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
        added = insert_keywords(conn, app_id, [(l, t) for l in locales for t in terms])
    return {"added": added}


def insert_keywords(conn, app_id: int, pairs) -> int:
    """pairs: iterable of (locale, term). Returns how many were new."""
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO app_keywords (app_id, locale, country, term, created_at) VALUES (?, ?, ?, ?, ?)",
        [(app_id, l, MARKETS[l]["country"], t, db.now()) for l, t in pairs],
    )
    return conn.total_changes - before


def latest_market_terms(conn, locale: str, top: int, q: str = "") -> list[str]:
    """Top queries of a market's latest scan (see scanner)."""
    row = conn.execute("SELECT id FROM market_scans WHERE scan_key = ? ORDER BY id DESC LIMIT 1",
                       (MARKETS[locale]["scan_key"],)).fetchone()
    if not row:
        return []
    sql, args = "SELECT term FROM keywords WHERE market_scan_id = ?", [row["id"]]
    if q.strip():
        sql += " AND term LIKE ?"
        args.append(f"%{q.strip().lower()}%")
    return [r["term"] for r in conn.execute(sql + " ORDER BY rank LIMIT ?", args + [top]).fetchall()]


class FromMarketIn(BaseModel):
    locales: list[str]
    top: int = Field(100, ge=1, le=1000)
    q: str = ""


@router.post("/apps/{app_id}/keywords/from-market")
def keywords_from_market(app_id: int, body: FromMarketIn):
    """Track the top queries of each chosen market (optionally filtered)."""
    locales = [l for l in body.locales if l in MARKETS]
    if not locales:
        raise HTTPException(400, "Выберите хотя бы один рынок")
    with db.tx() as conn:
        get_app(conn, app_id)
        pairs, empty = [], []
        for loc in locales:
            terms = latest_market_terms(conn, loc, body.top, body.q)
            if not terms:
                empty.append(MARKETS[loc]["name"])
            pairs += [(loc, t) for t in terms]
        added = insert_keywords(conn, app_id, pairs)
    return {"added": added, "markets_without_data": empty}


# Words too common to say anything about an app.
STOPWORDS = set("""
and the for with your you app apps free new pro best from all get top our more
de des la le les et pour avec du un une en au aux votre vos sur par
der die das und für mit ein eine dein deine von zum zur im
el los las y para con un una tu tus del al por en
il lo gli e per con un una il tuo tua di da
o os as e para com um uma seu sua do da dos das no na
и для с в на по от к из ваш твой
""".split())


def listing_tokens(*texts: str) -> set[str]:
    words = set()
    for text in texts:
        for w in re.split(r"[^\w]+", (text or "").lower()):
            if len(w) >= 3 and not w.isdigit() and w not in STOPWORDS:
                words.add(w)
    return words


def title_phrases(*texts: str) -> list[str]:
    """'Dream Journal – AI Analysis' -> ['dream journal', 'ai analysis']"""
    out = []
    for text in texts:
        for part in re.split(r"\s[-–—|:]\s|[,:|&•·]", (text or "").lower()):
            part = " ".join(part.split())
            if len(part) >= 3 and part not in out:
                out.append(part)
    return out


class FromListingIn(BaseModel):
    locales: list[str]
    per_market: int = Field(50, ge=1, le=500)


@router.post("/apps/{app_id}/keywords/from-listing")
def keywords_from_listing(app_id: int, body: FromListingIn):
    """No credentials needed: read the public App Store listing (title,
    subtitle, description) in each market and pick that market's top queries
    sharing its words. Saves title/subtitle as the market's store metadata."""
    locales = [l for l in body.locales if l in MARKETS]
    if not locales:
        raise HTTPException(400, "Выберите хотя бы один рынок")
    with db.tx() as conn:
        get_app(conn, app_id)

    listings: dict[str, dict | None] = {}  # per storefront country
    added_total, report = 0, []
    for loc in locales:
        cc = MARKETS[loc]["country"]
        if cc not in listings:
            try:
                info = itunes.lookup(app_id, cc)
                listings[cc] = None if not info else {
                    "title": info.get("trackName", ""),
                    "subtitle": itunes.app_subtitle(app_id, cc),
                    "description": info.get("description", ""),
                }
            except itunes.ItunesError as e:
                raise HTTPException(502, str(e))
        listing = listings[cc]
        if not listing:
            report.append({"market": MARKETS[loc]["name"], "error": "приложения нет в этой витрине"})
            continue

        # The app's own words: title and subtitle, plus words its description repeats.
        own = listing_tokens(listing["title"], listing["subtitle"])
        desc_words = re.split(r"[^\w]+", listing["description"].lower())
        own |= {w for w in listing_tokens(listing["description"]) if desc_words.count(w) >= 3}

        with db.tx() as conn:
            market = latest_market_terms(conn, loc, 1000)
            related = [t for t in market if own & listing_tokens(t)][: body.per_market]
            terms = list(dict.fromkeys(title_phrases(listing["title"], listing["subtitle"]) + related))
            added = insert_keywords(conn, app_id, [(loc, t) for t in terms])
            conn.execute(
                "INSERT INTO app_meta (app_id, locale, store_title, store_subtitle, updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(app_id, locale) DO UPDATE SET store_title=excluded.store_title,"
                " store_subtitle=excluded.store_subtitle",
                (app_id, loc, listing["title"], listing["subtitle"], db.now()),
            )
        added_total += added
        report.append({"market": MARKETS[loc]["name"], "added": added, "from_market": len(related),
                       "market_scanned": bool(market)})
    return {"added": added_total, "markets": report}


# ---------- App Store Connect ----------

class AscAccountIn(BaseModel):
    name: str = ""
    key_id: str
    issuer_id: str
    private_key: str


def asc_client(row) -> asc.Client:
    return asc.Client(row["key_id"], row["issuer_id"], row["private_key"])


@router.get("/asc-accounts")
def list_asc_accounts():
    with db.tx() as conn:
        rows = conn.execute("SELECT id, name, key_id, issuer_id, created_at FROM asc_accounts ORDER BY id").fetchall()
    return [dict(r) for r in rows]


@router.post("/asc-accounts")
def add_asc_account(body: AscAccountIn):
    if "PRIVATE KEY" not in body.private_key:
        raise HTTPException(400, "Вставьте содержимое .p8 файла целиком, вместе со строками BEGIN/END PRIVATE KEY")
    client = asc.Client(body.key_id, body.issuer_id, body.private_key)
    try:
        apps = client.list_apps()  # proves the key works
    except asc.AscError as e:
        raise HTTPException(400, str(e))
    with db.tx() as conn:
        acc_id = conn.execute(
            "INSERT INTO asc_accounts (name, key_id, issuer_id, private_key, created_at) VALUES (?, ?, ?, ?, ?)",
            (body.name.strip() or body.key_id.strip(), body.key_id.strip(), body.issuer_id.strip(),
             body.private_key.strip(), db.now()),
        ).lastrowid
    return {"id": acc_id, "apps": apps}


@router.delete("/asc-accounts/{account_id}")
def delete_asc_account(account_id: int):
    with db.tx() as conn:
        conn.execute("DELETE FROM asc_accounts WHERE id = ?", (account_id,))
    return {"ok": True}


@router.get("/asc-accounts/apps")
def asc_apps():
    """Apps visible to every connected account, to add them in one click."""
    with db.tx() as conn:
        rows = conn.execute("SELECT * FROM asc_accounts").fetchall()
    out = []
    for row in rows:
        try:
            out += [{**a, "account": row["name"]} for a in asc_client(row).list_apps()]
        except asc.AscError as e:
            out.append({"account": row["name"], "error": str(e)})
    return out


class AscImportIn(BaseModel):
    replace: bool = False


@router.post("/apps/{app_id}/import-asc")
def import_asc(app_id: int, body: AscImportIn):
    """Title, subtitle, the hidden Keywords field and IAP names of every
    localization; the Keywords become tracked queries in their markets."""
    with db.tx() as conn:
        get_app(conn, app_id)
        accounts = conn.execute("SELECT * FROM asc_accounts").fetchall()
    if not accounts:
        raise HTTPException(400, "Не подключён ни один ключ App Store Connect")
    client = next((asc_client(a) for a in accounts if asc_client(a).has_app(app_id)), None)
    if not client:
        raise HTTPException(404, "Ни один подключённый ключ не видит это приложение")
    try:
        data = client.fetch_metadata(app_id)
    except asc.AscError as e:
        raise HTTPException(400, str(e))

    pairs, skipped = [], []
    with db.tx() as conn:
        for loc, m in data["locales"].items():
            if loc not in MARKETS:
                skipped.append(loc)
                continue
            pairs += [(loc, " ".join(w.split()).lower()) for w in (m.get("keywords") or "").split(",") if w.strip()]
            pairs += [(loc, p) for p in title_phrases(m.get("title", ""), m.get("subtitle", ""))]
            conn.execute(
                "INSERT INTO app_meta (app_id, locale, title, subtitle, keywords, iap_names, version, source, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'asc', ?)"
                " ON CONFLICT(app_id, locale) DO UPDATE SET title=excluded.title, subtitle=excluded.subtitle,"
                " keywords=excluded.keywords, iap_names=excluded.iap_names, version=excluded.version,"
                " source='asc', updated_at=excluded.updated_at",
                (app_id, loc, m.get("title", ""), m.get("subtitle", ""), m.get("keywords", ""),
                 json.dumps(m.get("iap_names", []), ensure_ascii=False), data["version"],
                 data["date"] or db.now()),
            )
        if body.replace:
            conn.execute("DELETE FROM app_keywords WHERE app_id = ?", (app_id,))
        added = insert_keywords(conn, app_id, pairs)
    return {"version": data["version"], "locales": len(data["locales"]) - len(skipped),
            "added": added, "skipped_locales": skipped}


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


@router.post("/app-runs/stop")
def stop_run():
    return {"stopping": apptracker.stop()}


@router.get("/app-runs/latest")
def latest_run():
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM app_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None

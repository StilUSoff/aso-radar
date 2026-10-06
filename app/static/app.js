"use strict";

const $ = (s, root = document) => root.querySelector(s);
const PAGE = 100;
const state = { meta: null, overview: [], open: new Map() }; // locale -> {data, q, limit}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const flag = (cc) => cc ? cc.toUpperCase().replace(/./g, (c) => String.fromCodePoint(0x1f1a5 + c.charCodeAt(0))) : "";
const fmtDate = (s) => s ? new Date(s).toLocaleDateString("ru-RU") : "—";
const fmtDateTime = (s) => s ? new Date(s).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—";
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};
const marketOf = (loc) => state.meta.markets.find((m) => m.locale === loc);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.detail || res.statusText);
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), 4000);
}

function storageGet(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} }

// ---------- tiles ----------

function tileClass(m) {
  if (!m.scanned_at) return "none";
  const ageH = (Date.now() - new Date(m.scanned_at)) / 36e5;
  if (state.meta.scan_interval_hours > 0 && ageH > state.meta.scan_interval_hours * 2 + 6) return "stale";
  return m.new >= 10 ? "hot" : "";
}

function renderTiles() {
  $("#tiles").innerHTML = state.overview.map((m) => {
    const lead = m.leaders[0];
    const moves = m.has_previous ? ` · <span title="новых в топ‑100">+${m.new} new</span>` : "";
    return `<button class="tile ${tileClass(m)}" data-loc="${m.locale}" title="${esc(m.leaders.join(" · "))}">
      <span class="name"><span class="flag">${flag(m.country)}</span>${esc(m.name)}</span>
      ${lead ? `<span class="lead"><small>#1</small> ${esc(lead)}</span>` : `<span class="lead"><small>— нет данных</small></span>`}
      <span class="sub">${m.scanned_at ? `${m.unique_terms} ${plural(m.unique_terms, "запрос", "запроса", "запросов")}${moves}` : "не сканировался"}</span>
      ${m.shares_with ? `<span class="sub">= ${esc(marketOf(m.shares_with).name)}</span>` : ""}
    </button>`;
  }).join("");
}

// ---------- market sections ----------

function marketHeadStats(m) {
  if (!m.scanned_at) return `<span class="stats">ещё не сканировался</span>`;
  return `<span class="stats"><b>${m.unique_terms}</b> запросов · ${m.prefixes} префиксов · ${fmtDate(m.scanned_at)}` +
    (m.leaders[0] ? ` &nbsp; Top: <b>${esc(m.leaders[0])}</b>` : "") + `</span>`;
}

function renderMarkets() {
  $("#markets").innerHTML = state.overview.map((m) => `
    <section class="market ${state.open.has(m.locale) ? "open" : ""}" id="m-${m.locale}" data-loc="${m.locale}">
      <div class="market-head">
        <span class="title"><span class="flag">${flag(m.country)}</span>${esc(m.name)}</span>
        ${marketHeadStats(m)}
        <span class="spacer"></span>
        <button class="act-prompt" ${m.scanned_at ? "" : "disabled"}>Copy /strategy prompt</button>
        <span class="chev">▼</span>
      </div>
      <div class="market-body ${state.open.has(m.locale) ? "" : "hidden"}"></div>
    </section>`).join("");
  for (const loc of state.open.keys()) renderMarketBody(loc);
}

async function loadMarket(loc) {
  const st = state.open.get(loc);
  const params = new URLSearchParams({ limit: st.limit, q: st.q });
  st.data = await api(`/api/markets/${encodeURIComponent(loc)}?${params}`);
  renderMarketBody(loc);
}

function seedText(seeds) {
  return seeds.map((s) => `${s.alphabet}${s.depth === 2 ? " ×2" : ""}`).join(" + ");
}

function renderMarketBody(loc) {
  const sec = document.getElementById(`m-${loc}`);
  const st = state.open.get(loc);
  if (!sec || !st?.data) return;
  const { market, scan, items, total, has_previous, trend_dates } = st.data;
  const body = $(".market-body", sec);
  const shared = market.shares_with ? marketOf(market.shares_with) : null;
  const info = `
    <div class="meta-box">
      <div class="hdr">Параметры сбора
        <span class="pill">App Store ${market.country.toUpperCase()} · ${market.storefront}</span>
        <span class="pill">${esc(market.locale)}</span>
        ${scan ? `<span class="pill">${fmtDateTime(scan.scanned_at)}</span>` : ""}
      </div>
      <dl class="meta-grid">
        <dt>Алфавиты</dt><dd class="mono">${esc(seedText(market.seeds))}</dd>
        <dt>Префиксов</dt><dd>${scan ? scan.prefixes : "—"}</dd>
        <dt>Уникальных</dt><dd>${scan ? scan.unique_terms : "—"} запросов, в рейтинге топ‑${state.meta.top_n}</dd>
        <dt>Сканов</dt><dd>${trend_dates.length ? `${trend_dates.length} (с ${fmtDate(trend_dates[0])})` : "—"}</dd>
        ${shared ? `<dt>Совпадает с</dt><dd>${esc(shared.name)}: та же витрина и тот же алфавит</dd>` : ""}
      </dl>
    </div>`;

  if (!scan) {
    body.innerHTML = info + `<div class="empty">Данных нет. <button class="act-scan">Сканировать этот рынок</button></div>`;
    return;
  }

  const rows = items.map((k) => `
    <tr class="row" data-term="${esc(k.term)}">
      <td>${esc(k.term)}</td>
      <td><span class="pos ${k.rank <= 10 ? "p1" : k.rank <= 50 ? "p2" : "p3"}">#${k.rank}</span></td>
      <td>${deltaCell(k, has_previous)}</td>
      <td>${sparkline(k.trend)}</td>
      <td><div class="bar" title="популярность ${k.score}"><span style="width:${Math.max(2, k.score)}%"></span></div></td>
      <td class="mono muted" title="кратчайший префикс и позиция в подсказках">${esc(k.best_prefix)} → ${k.best_pos}</td>
      <td class="muted">${fmtDate(scan.scanned_at)}</td>
    </tr>`).join("");

  body.innerHTML = info + `
    <div class="toolbar">
      <input type="search" class="market-q" placeholder="Фильтр запросов" value="${esc(st.q)}">
      <span class="muted">${total} ${plural(total, "запрос", "запроса", "запросов")}</span>
      <span class="spacer"></span>
      <a href="/api/markets/${encodeURIComponent(loc)}/export.csv"><button>CSV</button></a>
      <button class="act-scan">Пересканировать</button>
    </div>
    <div class="table-wrap"><table class="kw">
      <thead><tr><th>Keyword</th><th>Position</th><th>Δ</th><th>Trend</th><th>Popularity</th><th>Prefix → pos</th><th>Last</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="7" class="empty">Ничего не найдено</td></tr>`}</tbody>
    </table></div>
    ${items.length < total ? `<button class="more act-more">Показать ещё (${total - items.length})</button>` : ""}`;
  const input = $(".market-q", body);
  if (st.focus) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); st.focus = false; }
}

function deltaCell(k, hasPrev) {
  if (!hasPrev) return "";
  if (k.is_new) return `<span class="d-new">new</span>`;
  if (!k.prev_rank) return "";
  const d = k.prev_rank - k.rank;
  if (d === 0) return `<span class="d-eq">=</span>`;
  return d > 0 ? `<span class="d-up">↑${d}</span>` : `<span class="d-down">↓${-d}</span>`;
}

// Rank history: higher line = better position; gaps where the term was outside the top.
function sparkline(trend) {
  if (!trend || trend.filter((r) => r != null).length < 2) return "";
  const W = 74, H = 20, P = 2;
  const vals = trend.filter((r) => r != null);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const x = (i) => P + (i / (trend.length - 1)) * (W - 2 * P);
  const y = (r) => hi === lo ? H / 2 : P + ((r - lo) / (hi - lo)) * (H - 2 * P);
  let d = "", pen = false;
  trend.forEach((r, i) => {
    if (r == null) { pen = false; return; }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(r).toFixed(1)}`;
    pen = true;
  });
  return `<svg class="spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">
    <path d="${d}" fill="none" stroke="var(--muted)" stroke-width="1.4"/></svg>`;
}

async function toggleApps(tr, loc) {
  const next = tr.nextElementSibling;
  if (next?.classList.contains("apps")) { next.remove(); return; }
  const row = document.createElement("tr");
  row.className = "apps";
  row.innerHTML = `<td colspan="7" class="muted">Загружаю топ приложений по запросу…</td>`;
  tr.after(row);
  try {
    const apps = await api(`/api/top-apps?term=${encodeURIComponent(tr.dataset.term)}&country=${marketOf(loc).country}`);
    row.innerHTML = `<td colspan="7"><div class="apps-list">${apps.map((a, i) => `
      <a class="app" href="${esc(a.url)}" target="_blank" rel="noopener">
        <img src="${esc(a.icon)}" alt="" loading="lazy">
        <div><b>${i + 1}. ${esc(a.name)}</b><small>${esc(a.seller)}</small></div></a>`).join("")
      || '<span class="muted">Приложений не найдено</span>'}</div></td>`;
  } catch (e) {
    row.innerHTML = `<td colspan="7" class="muted">${esc(e.message)}</td>`;
  }
}

async function toggleMarket(loc, forceOpen = false) {
  const sec = document.getElementById(`m-${loc}`);
  const isOpen = state.open.has(loc);
  if (isOpen && !forceOpen) {
    state.open.delete(loc);
    sec.classList.remove("open");
    $(".market-body", sec).classList.add("hidden");
  } else if (!isOpen) {
    state.open.set(loc, { q: "", limit: PAGE, data: null });
    sec.classList.add("open");
    const body = $(".market-body", sec);
    body.classList.remove("hidden");
    body.innerHTML = `<div class="empty">Загрузка…</div>`;
    await loadMarket(loc);
  }
  storageSet("open", [...state.open.keys()]);
}

// ---------- strategy prompt ----------

async function copyPrompt(loc) {
  const m = marketOf(loc);
  const data = await api(`/api/markets/${encodeURIComponent(loc)}?limit=150`);
  const lines = data.items.map((k) => {
    let delta = "";
    if (data.has_previous) delta = k.is_new ? " (новый в топе)" : k.prev_rank && k.prev_rank !== k.rank ? ` (было #${k.prev_rank})` : "";
    return `#${k.rank} ${k.term} — популярность ${k.score}${delta}`;
  });
  const text = `Ты ASO-эксперт по App Store. Рынок: ${m.name} (витрина ${m.country.toUpperCase()}, локаль ASC ${m.locale}).

Ниже топ поисковых запросов этого рынка, собранный из подсказок поиска App Store (перебор префиксов). Популярность относительная: 100 — самый популярный запрос рынка; абсолютных объёмов Apple не даёт. Скан от ${fmtDate(data.scan.scanned_at)}.

${lines.join("\n")}

Задачи:
1. Сгруппируй запросы в тематические кластеры (бренды, категории, функции) и отметь, какие из них небрендовые — по ним реально ранжироваться новому приложению.
2. Выдели растущие и новые запросы — сигналы спроса и трендов.
3. Предложи 5–10 идей приложений или ниш под этот рынок с обоснованием по данным.
4. Для лучшей идеи предложи метаданные на языке рынка: Title (≤30 символов), Subtitle (≤30), Keywords (≤100, через запятую без пробелов, без повторов слов из Title/Subtitle).`;
  await navigator.clipboard.writeText(text);
  toast(`Промпт для ${m.name} скопирован`);
}

// ---------- global search ----------

let searchTimer;
async function runGlobalSearch() {
  const q = $("#globalSearch").value.trim();
  const box = $("#searchBox");
  if (q.length < 2) { box.classList.add("hidden"); return; }
  const res = await api(`/api/search?q=${encodeURIComponent(q)}`);
  box.classList.remove("hidden");
  $("#searchResults").innerHTML = res.length ? `<div class="table-wrap"><table class="kw">
    <thead><tr><th>Запрос</th><th>Рынков</th><th>Позиции по гео</th></tr></thead>
    <tbody>${res.map((r) => `<tr><td>${esc(r.term)}</td><td>${r.markets.length}</td>
      <td><div class="chips">${r.markets.map((m) =>
        `<span class="chip" data-loc="${m.locale}" title="${esc(m.name)} · популярность ${m.score}">${flag(m.country)} ${esc(m.name)} <b>#${m.rank}</b></span>`).join("")}</div></td></tr>`).join("")}
    </tbody></table></div>` : `<div class="muted">«${esc(q)}» не найден в топе ни одного рынка.</div>`;
}

// ---------- scans ----------

async function pollScan() {
  const scan = await api("/api/scans/latest").catch(() => null);
  const running = scan?.status === "running";
  $("#scanAllBtn").disabled = running;
  if (running) {
    const pct = scan.total ? Math.min(99, Math.round((scan.done / scan.total) * 100)) : 0;
    $("#scanStatus").textContent = `Сканирую ${scan.current || ""}: ${pct}%`;
  } else if (scan?.status === "failed") {
    $("#scanStatus").textContent = `Скан прерван: ${scan.error}`;
  } else {
    $("#scanStatus").textContent = scan ? `Последний скан: ${fmtDateTime(scan.finished_at)}` : "";
  }
  // Refresh as markets finish, and once more when the scan ends.
  if ((running && scan.current !== pollScan.current) || (pollScan.running && !running)) await refresh();
  pollScan.current = scan?.current;
  pollScan.running = running;
  setTimeout(pollScan, running ? 4000 : 30000);
}

async function startScan(locales) {
  try {
    await api("/api/scans", { method: "POST", body: { locales } });
    toast(locales ? "Скан рынка запущен" : "Скан всех рынков запущен — это займёт пару часов");
    $("#scanAllBtn").disabled = true;
  } catch (e) { toast(e.message); }
}

async function refresh() {
  state.overview = await api("/api/markets");
  renderTiles();
  // Keep open sections and their filters while updating headers.
  for (const m of state.overview) {
    const sec = document.getElementById(`m-${m.locale}`);
    if (sec) $(".stats", sec).outerHTML = marketHeadStats(m);
  }
  if (!$("#markets").children.length) renderMarkets();
  for (const loc of state.open.keys()) loadMarket(loc);
}

// ---------- events ----------

$("#tiles").onclick = (e) => {
  const tile = e.target.closest(".tile");
  if (!tile) return;
  const loc = tile.dataset.loc;
  toggleMarket(loc, true);
  document.getElementById(`m-${loc}`).scrollIntoView({ behavior: "smooth" });
};
$("#searchResults").onclick = (e) => {
  const chip = e.target.closest(".chip");
  if (chip) $("#tiles").onclick({ target: $(`.tile[data-loc="${chip.dataset.loc}"]`) });
};
$("#globalSearch").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(runGlobalSearch, 250); };
$("#scanAllBtn").onclick = () => startScan(null);

$("#markets").addEventListener("click", (e) => {
  const sec = e.target.closest(".market");
  if (!sec) return;
  const loc = sec.dataset.loc;
  if (e.target.closest(".act-prompt")) { copyPrompt(loc).catch((err) => toast(err.message)); return; }
  if (e.target.closest(".act-scan")) { startScan([loc]); return; }
  if (e.target.closest(".act-more")) { state.open.get(loc).limit += PAGE; loadMarket(loc); return; }
  const row = e.target.closest("tr.row");
  if (row) { toggleApps(row, loc); return; }
  if (e.target.closest(".market-head")) toggleMarket(loc);
});
$("#markets").addEventListener("input", (e) => {
  if (!e.target.classList.contains("market-q")) return;
  const loc = e.target.closest(".market").dataset.loc;
  const st = state.open.get(loc);
  st.q = e.target.value;
  st.limit = PAGE;
  st.focus = true;
  clearTimeout(st.timer);
  st.timer = setTimeout(() => loadMarket(loc), 250);
});

(async function init() {
  state.meta = await api("/api/meta");
  for (const loc of storageGet("open") || []) {
    if (marketOf(loc)) state.open.set(loc, { q: "", limit: PAGE, data: null });
  }
  state.overview = await api("/api/markets");
  renderTiles();
  renderMarkets();
  for (const loc of state.open.keys()) loadMarket(loc);
  pollScan();
})().catch((e) => toast(e.message));

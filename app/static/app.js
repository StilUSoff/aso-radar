"use strict";

const $ = (s, root = document) => root.querySelector(s);
const PAGE = 100;
const state = {
  meta: null,
  view: "market",
  // market view
  overview: [], open: new Map(), // locale -> {data, q, limit}
  // apps view
  apps: [], appId: null, appData: null, appOpen: new Set(), editing: null,
};

// ---------- helpers ----------

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
const currentApp = () => state.apps.find((a) => a.id === state.appId);
const fmtScore = (s) => s >= 10 ? s.toFixed(0) : s >= 1 ? s.toFixed(1) : s.toFixed(2);

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

function posClass(rank) { return rank <= 10 ? "p1" : rank <= 50 ? "p2" : "p3"; }

function deltaCell(rank, prev, hasPrev, isNew) {
  if (!hasPrev) return "";
  if (isNew) return `<span class="d-new">new</span>`;
  if (!prev && !rank) return `<span class="d-eq">=</span>`;
  if (!prev) return `<span class="d-up">new</span>`;
  if (!rank) return `<span class="d-down">out</span>`;
  const d = prev - rank;
  if (d === 0) return `<span class="d-eq">=</span>`;
  return d > 0 ? `<span class="d-up">↑${d}</span>` : `<span class="d-down">↓${-d}</span>`;
}

// Rank history: higher line = better position; gaps where it was outside the top.
function sparkline(trend, floor) {
  const vals = (trend || []).map((r) => r ?? floor ?? null);
  if (vals.filter((r) => r != null).length < 2) return "";
  const W = 74, H = 20, P = 2;
  const present = vals.filter((r) => r != null);
  const lo = Math.min(...present), hi = Math.max(...present);
  const x = (i) => P + (i / (vals.length - 1)) * (W - 2 * P);
  const y = (r) => hi === lo ? H / 2 : P + ((r - lo) / (hi - lo)) * (H - 2 * P);
  let d = "", pen = false;
  vals.forEach((r, i) => {
    if (r == null) { pen = false; return; }
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(r).toFixed(1)}`;
    pen = true;
  });
  return `<svg class="spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">
    <path d="${d}" fill="none" stroke="var(--muted)" stroke-width="1.4"/></svg>`;
}

function appsList(apps) {
  return `<div class="apps-list">${apps.map((a, i) => `
    <a class="app" ${a.url ? `href="${esc(a.url)}"` : `href="https://apps.apple.com/app/id${a.id}"`} target="_blank" rel="noopener">
      <img src="${esc(a.icon)}" alt="" loading="lazy">
      <div><b>${i + 1}. ${esc(a.name)}</b>${a.seller ? `<small>${esc(a.seller)}</small>` : ""}</div></a>`).join("")
    || '<span class="muted">Приложений не найдено</span>'}</div>`;
}

async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${label} скопирован`);
  } catch {
    openDialog(label, `<textarea readonly style="min-height:300px">${esc(text)}</textarea>`, async () => {});
  }
}

// ---------- dialog ----------

function openDialog(title, bodyHtml, onOk, okLabel = "OK") {
  const dlg = $("#dlg");
  $("#dlgTitle").textContent = title;
  $("#dlgBody").innerHTML = bodyHtml;
  $("#dlgError").textContent = "";
  $("#dlgOk").textContent = okLabel;
  $("#dlgOk").disabled = false;
  $("#dlgForm").onsubmit = async (e) => {
    if (e.submitter?.value !== "ok") return;
    e.preventDefault();
    $("#dlgOk").disabled = true;
    try {
      await onOk(new FormData($("#dlgForm")));
      dlg.close();
    } catch (err) {
      $("#dlgError").textContent = err.message;
    } finally {
      $("#dlgOk").disabled = false;
    }
  };
  dlg.showModal();
}

function marketPicker(checked = []) {
  return `<div class="pick">${state.meta.markets.map((m) =>
    `<label><input type="checkbox" name="loc" value="${m.locale}" ${checked.includes(m.locale) ? "checked" : ""}>${flag(m.country)} ${esc(m.name)}</label>`).join("")}</div>`;
}

// =====================================================================
// Market view
// =====================================================================

function tileClass(m) {
  if (!m.scanned_at) return "none";
  const ageH = (Date.now() - new Date(m.scanned_at)) / 36e5;
  if (state.meta.scan_interval_hours > 0 && ageH > state.meta.scan_interval_hours * 2 + 6) return "stale";
  return m.new >= 10 ? "hot" : "";
}

function renderMarketTiles() {
  $("#marketTiles").innerHTML = state.overview.map((m) => {
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

function marketHeadStats(m) {
  if (!m.scanned_at) return `<span class="stats">ещё не сканировался</span>`;
  return `<span class="stats"><b>${m.unique_terms}</b> запросов · ${m.prefixes} префиксов · ${fmtDate(m.scanned_at)}` +
    (m.leaders[0] ? ` &nbsp; Top: <b>${esc(m.leaders[0])}</b>` : "") + `</span>`;
}

function renderMarketSections() {
  $("#marketSections").innerHTML = state.overview.map((m) => `
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
  return seeds.map((s) => `${s.alphabet}${s.depth === 2 ? " ×2" : " +"}`).join(" · ");
}

function trackedTerms(loc) {
  const d = state.appData;
  if (!d || d.app.id !== state.appId) return new Set();
  const m = d.markets.find((x) => x.locale === loc);
  return new Set(m ? m.items.map((k) => k.term) : []);
}

function renderMarketBody(loc) {
  const sec = document.getElementById(`m-${loc}`);
  const st = state.open.get(loc);
  if (!sec || !st?.data) return;
  const { market, scan, items, total, has_previous, trend_dates } = st.data;
  const body = $(".market-body", sec);
  const shared = market.shares_with ? marketOf(market.shares_with) : null;
  const app = currentApp();
  const tracked = trackedTerms(loc);
  const info = `
    <div class="meta-box">
      <div class="hdr">Параметры сбора
        <span class="pill">App Store ${market.country.toUpperCase()} · ${market.storefront}</span>
        <span class="pill">${esc(market.locale)}</span>
        ${scan ? `<span class="pill">${fmtDateTime(scan.scanned_at)}</span>` : ""}
      </div>
      <dl class="meta-grid">
        <dt>Алфавиты</dt><dd class="mono">${esc(seedText(market.seeds))} · слова‑мосты</dd>
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
      <td><span class="pos ${posClass(k.rank)}">#${k.rank}</span></td>
      <td>${deltaCell(k.rank, k.prev_rank, has_previous, k.is_new)}</td>
      <td>${sparkline(k.trend)}</td>
      <td title="относительная популярность (лидер рынка = 100)"><div class="bar"><span style="width:${Math.max(1.5, Math.log10(1 + k.score * 9.99) * 50)}%"></span></div></td>
      <td class="muted num">${fmtScore(k.score)}</td>
      <td class="mono muted" title="кратчайший префикс и позиция в подсказках">${esc(k.best_prefix)} → ${k.best_pos}</td>
      ${app ? `<td><button class="iconbtn track ${tracked.has(k.term) ? "done" : ""}" title="${tracked.has(k.term) ? "уже отслеживается" : `отслеживать позицию «${esc(app.name)}» по этому запросу`}">${tracked.has(k.term) ? "✓" : "+"}</button></td>` : ""}
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
      <thead><tr><th>Keyword</th><th>Position</th><th>Δ</th><th>Trend</th><th colspan="2">Popularity</th><th>Prefix → pos</th>${app ? "<th></th>" : ""}</tr></thead>
      <tbody>${rows || `<tr><td colspan="8" class="empty">Ничего не найдено</td></tr>`}</tbody>
    </table></div>
    ${items.length < total ? `<button class="more act-more">Показать ещё (${total - items.length})</button>` : ""}`;
  const input = $(".market-q", body);
  if (st.focus) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); st.focus = false; }
}

async function toggleTopApps(tr, country, cols) {
  const next = tr.nextElementSibling;
  if (next?.classList.contains("apps")) { next.remove(); return; }
  const row = document.createElement("tr");
  row.className = "apps";
  row.innerHTML = `<td colspan="${cols}" class="muted">Загружаю топ приложений по запросу…</td>`;
  tr.after(row);
  try {
    const apps = await api(`/api/top-apps?term=${encodeURIComponent(tr.dataset.term)}&country=${country}`);
    row.innerHTML = `<td colspan="${cols}">${appsList(apps)}</td>`;
  } catch (e) {
    row.innerHTML = `<td colspan="${cols}" class="muted">${esc(e.message)}</td>`;
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

async function marketPrompt(loc) {
  const m = marketOf(loc);
  const data = await api(`/api/markets/${encodeURIComponent(loc)}?limit=150`);
  const lines = data.items.map((k) => {
    let delta = "";
    if (data.has_previous) delta = k.is_new ? " (новый в топе)" : k.prev_rank && k.prev_rank !== k.rank ? ` (было #${k.prev_rank})` : "";
    return `#${k.rank} ${k.term} — популярность ${fmtScore(k.score)}${delta}`;
  });
  return `Ты ASO-эксперт по App Store. Рынок: ${m.name} (витрина ${m.country.toUpperCase()}, локаль ASC ${m.locale}).

Ниже топ поисковых запросов этого рынка, собранный из подсказок поиска App Store. Популярность относительная: 100 — самый популярный запрос рынка, 10 — примерно в 10 раз реже; абсолютных объёмов Apple не даёт. Скан от ${fmtDate(data.scan.scanned_at)}.

${lines.join("\n")}

Задачи:
1. Сгруппируй запросы в тематические кластеры (бренды, категории, функции) и отметь небрендовые — по ним реально ранжироваться новому приложению.
2. Выдели растущие и новые запросы — сигналы спроса и трендов.
3. Предложи 5–10 идей приложений или ниш под этот рынок с обоснованием по данным.
4. Для лучшей идеи предложи метаданные на языке рынка: Title (≤30 символов), Subtitle (≤30), Keywords (≤100, через запятую без пробелов, без повторов слов из Title/Subtitle).`;
}

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
        `<span class="chip" data-loc="${m.locale}" title="${esc(m.name)} · популярность ${fmtScore(m.score)}">${flag(m.country)} ${esc(m.name)} <b>#${m.rank}</b></span>`).join("")}</div></td></tr>`).join("")}
    </tbody></table></div>` : `<div class="muted">«${esc(q)}» не найден в топе ни одного рынка.</div>`;
}

async function refreshMarket() {
  state.overview = await api("/api/markets");
  renderMarketTiles();
  if (!$("#marketSections").children.length) { renderMarketSections(); return; }
  for (const m of state.overview) {
    const sec = document.getElementById(`m-${m.locale}`);
    if (sec) $(".stats", sec).outerHTML = marketHeadStats(m);
  }
  for (const loc of state.open.keys()) loadMarket(loc);
}

function updateTrackHint() {
  const app = currentApp();
  const el = $("#trackHint");
  el.classList.toggle("hidden", !app);
  if (app) el.textContent = `«+» добавляет запрос к «${app.name}»`;
}

async function trackFromMarket(btn, loc) {
  const app = currentApp();
  if (!app || btn.classList.contains("done")) return;
  const term = btn.closest("tr").dataset.term;
  await api(`/api/apps/${app.id}/keywords`, { method: "POST", body: { locales: [loc], terms: term } });
  btn.classList.add("done");
  btn.textContent = "✓";
  toast(`«${term}» отслеживается для ${app.name} · ${marketOf(loc).name}`);
  loadApps(); // refresh counts and tracked sets in the background
}

// =====================================================================
// Apps view
// =====================================================================

async function loadApps(selectId) {
  state.apps = await api("/api/apps");
  const saved = storageGet("appId");
  const pick = selectId || (state.apps.some((a) => a.id === state.appId) ? state.appId
    : state.apps.some((a) => a.id === saved) ? saved : state.apps[0]?.id) || null;
  state.appId = pick;
  $("#appSelect").innerHTML = state.apps.map((a) =>
    `<option value="${a.id}">${esc(a.name)} (${a.keyword_count})</option>`).join("");
  $("#appSelect").value = pick || "";
  $("#appSelect").classList.toggle("hidden", !state.apps.length);
  document.querySelectorAll(".needs-app").forEach((el) => el.classList.toggle("disabled", !pick));
  $("#appEmpty").classList.toggle("hidden", !!state.apps.length);
  $("#appContent").classList.toggle("hidden", !state.apps.length);
  updateTrackHint();
  if (pick) await loadApp(); else state.appData = null;
}

async function loadApp() {
  storageSet("appId", state.appId);
  state.appData = await api(`/api/apps/${state.appId}/markets`);
  renderApp();
}

function renderApp() {
  const { app, markets, total } = state.appData;
  $("#appIcon").src = app.icon || "";
  $("#appName").textContent = app.name;
  $("#appSub").textContent = `ID ${app.id}${app.seller ? " · " + app.seller : ""} · ${total.keywords} ключей · ` +
    `ранжируется ${total.ranked} · в топ‑50 ${total.top50} · в топ‑10 ${total.top10}`;
  $("#appCsv").href = `/api/apps/${app.id}/export.csv`;

  $("#appTiles").innerHTML = markets.map((m) => `
    <button class="tile g-${m.grade}" data-loc="${m.locale}">
      <span class="name"><span class="flag">${flag(m.country)}</span>${esc(m.name)}</span>
      <span class="med">${m.median != null ? `#${m.median} <small>median</small>` : `<small>— median</small>`}</span>
      <span class="sub">ranked ${m.ranked}/${m.keywords}</span>
    </button>`).join("") || `<div class="muted">Пока нет ключей. Нажмите «+ Ключевые слова» или добавьте их из топа на вкладке «Рынок».</div>`;

  $("#appSections").innerHTML = markets.map((m) => appSection(m)).join("");
}

function metaRow(label, value, limit, field, editing) {
  if (editing) {
    return `<dt>${label}</dt><dd><input type="text" name="${field}" value="${esc(value)}" maxlength="${limit * 2}" data-limit="${limit}"></dd>`;
  }
  const len = [...(value || "")].length;
  return `<dt>${label}</dt><dd class="mono">${value ? esc(value) : '<span class="faint">—</span>'}${value ? `<span class="count ${len > limit ? "over" : ""}">${len}/${limit}</span>` : ""}</dd>`;
}

function appSection(m) {
  const open = state.appOpen.has(m.locale);
  const meta = m.meta || {};
  const limits = state.appData.limits;
  const editing = state.editing === m.locale;
  const rows = m.items.map((k) => `
    <tr class="row" data-id="${k.id}" data-term="${esc(k.term)}">
      <td>${esc(k.term)}</td>
      <td>${k.checked_at ? (k.rank ? `<span class="pos ${posClass(k.rank)}">#${k.rank}</span>` : `<span class="pos p3">&gt;200</span>`) : '<span class="faint">?</span>'}</td>
      <td>${deltaCell(k.rank, k.prev_rank, !!k.prev_checked_at, false)}</td>
      <td>${sparkline(k.trend, 201)}</td>
      <td class="muted">${k.checked_at ? fmtDate(k.checked_at) : "—"}</td>
      <td><button class="iconbtn del" title="Удалить ключ">✕</button></td>
    </tr>`).join("");
  return `
    <section class="market ${open ? "open" : ""}" id="a-${m.locale}" data-loc="${m.locale}">
      <div class="market-head">
        <span class="title"><span class="flag">${flag(m.country)}</span>${esc(m.name)}</span>
        <span class="stats">${m.ranked}/${m.keywords} indexed${m.best ? ` &nbsp; Best: <b>${esc(m.best.term)} #${m.best.rank}</b>` : ""}</span>
        <span class="spacer"></span>
        <button class="act-prompt">Copy /strategy prompt</button>
        <span class="chev">▼</span>
      </div>
      <div class="market-body ${open ? "" : "hidden"}">
        <form class="meta-box meta-form">
          <div class="hdr">Current metadata
            <span class="pill">${esc(m.locale)}</span>
            <span class="pill">App Store ${m.country.toUpperCase()}</span>
            ${meta.updated_at ? `<span class="pill">${fmtDate(meta.updated_at)}</span>` : ""}
            <span class="spacer"></span>
            ${editing ? `<button type="submit" class="primary">Сохранить</button><button type="button" class="act-cancel">Отмена</button>`
              : `<button type="button" class="act-edit">Редактировать</button>`}
          </div>
          <dl class="meta-grid">
            ${metaRow("Title", meta.title || (editing ? "" : meta.store_title), limits.title, "title", editing)}
            ${metaRow("Subtitle", meta.subtitle, limits.subtitle, "subtitle", editing)}
            ${metaRow("Keywords", meta.keywords, limits.keywords, "keywords", editing)}
            ${!editing && !meta.title && meta.store_title ? `<dt></dt><dd class="faint">Title взят из выдачи App Store. Subtitle и Keywords видны только владельцу — впишите их вручную, если знаете.</dd>` : ""}
          </dl>
        </form>
        <div class="table-wrap"><table class="kw">
          <thead><tr><th>Keyword</th><th>Position</th><th>Δ</th><th>Trend</th><th>Last</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>
      </div>
    </section>`;
}

function toggleAppSection(loc, forceOpen = false) {
  if (state.appOpen.has(loc) && !forceOpen) state.appOpen.delete(loc); else state.appOpen.add(loc);
  storageSet("appOpen", [...state.appOpen]);
  const sec = document.getElementById(`a-${loc}`);
  const open = state.appOpen.has(loc);
  sec.classList.toggle("open", open);
  $(".market-body", sec).classList.toggle("hidden", !open);
}

function appPrompt(loc) {
  const { app } = state.appData;
  const m = state.appData.markets.find((x) => x.locale === loc);
  const meta = m.meta || {};
  const lines = m.items.map((k) => {
    const comp = k.top_apps.filter((a) => a.id !== app.id).slice(0, 3).map((a) => a.name).join("; ");
    return `- ${k.term}: ${k.rank ? `#${k.rank}` : ">200"}${k.prev_rank && k.prev_rank !== k.rank ? ` (было #${k.prev_rank})` : ""}${comp ? ` | лидеры: ${comp}` : ""}`;
  });
  return `Ты ASO-эксперт по App Store. Приложение: ${app.name} (ID ${app.id}). Рынок: ${m.name} (витрина ${m.country.toUpperCase()}, локаль ASC ${m.locale}).

Текущие метаданные:
Title: ${meta.title || meta.store_title || "—"}
Subtitle: ${meta.subtitle || "—"}
Keywords: ${meta.keywords || "—"}

Позиции приложения в поиске App Store (топ‑200, ${fmtDate(m.last_checked)}):
${lines.join("\n")}

Задачи:
1. Оцени, какие ключи работают, а какие нет, и почему (релевантность, конкуренция с лидерами).
2. Предложи новые Title (≤30 символов), Subtitle (≤30) и Keywords (≤100, через запятую без пробелов, без повторов слов из Title/Subtitle) на языке рынка.
3. Предложи ключи, которые стоит добавить в отслеживание, и какие убрать.`;
}

function addAppDialog() {
  openDialog("Добавить приложение", `
    <label>App Store ID или ссылка</label>
    <input type="text" name="app" placeholder="https://apps.apple.com/fr/app/…/id1234567890" required>
    <label>Витрина (если в ссылке нет страны)</label>
    <input type="text" name="country" value="us" maxlength="2">`,
  async (fd) => {
    const app = await api("/api/apps", { method: "POST", body: { app: fd.get("app"), country: fd.get("country") || "us" } });
    await loadApps(app.id);
    toast(`Добавлено: ${app.name}`);
  }, "Добавить");
}

function addKeywordsDialog() {
  openDialog("Добавить ключевые слова", `
    <label>Ключевые слова (через запятую или с новой строки)</label>
    <textarea name="terms" required placeholder="dream journal, interprétation des rêves"></textarea>
    <label>Рынки</label>
    ${marketPicker([...state.appOpen])}
    <p class="muted">Удобнее: откройте нужный рынок на вкладке «Рынок» и жмите «+» у запросов из топа.</p>`,
  async (fd) => {
    const r = await api(`/api/apps/${state.appId}/keywords`, { method: "POST",
      body: { locales: fd.getAll("loc"), terms: fd.get("terms") } });
    await loadApps();
    toast(`Добавлено ключей: ${r.added}. Запустите проверку позиций.`);
  }, "Добавить");
}

// =====================================================================
// Status polling (market scans + app checks)
// =====================================================================

async function poll() {
  const [scan, run] = await Promise.all([
    api("/api/scans/latest").catch(() => null), api("/api/app-runs/latest").catch(() => null)]);
  const scanning = scan?.status === "running";
  const checking = run?.status === "running";
  $("#scanAllBtn").disabled = scanning;
  $("#checkBtn").disabled = checking;
  const parts = [];
  if (scanning) parts.push(`Скан рынков: ${scan.current || ""} ${scan.total ? Math.min(99, Math.round(scan.done / scan.total * 100)) : 0}%`);
  if (checking) parts.push(`Позиции: ${run.done}/${run.total} (~${Math.ceil((run.total - run.done) * 3.3 / 60)} мин)`);
  if (!parts.length && scan?.finished_at) parts.push(`Рынки обновлены ${fmtDateTime(scan.finished_at)}`);
  $("#status").textContent = parts.join(" · ");

  if ((scanning && scan.current !== poll.current) || (poll.scanning && !scanning)) refreshMarket();
  if (state.appId && ((checking && run.done !== poll.done && run.done % 10 === 0) || (poll.checking && !checking))) loadApp();
  poll.current = scan?.current;
  poll.done = run?.done;
  poll.scanning = scanning;
  poll.checking = checking;
  setTimeout(poll, scanning || checking ? 4000 : 30000);
}

// =====================================================================
// Events
// =====================================================================

function setView(view) {
  state.view = view;
  storageSet("view", view);
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${view}`));
  if (view === "market") for (const loc of state.open.keys()) renderMarketBody(loc);
}
document.querySelectorAll(".tab").forEach((t) => { t.onclick = () => setView(t.dataset.view); });

// market view
$("#marketTiles").onclick = (e) => {
  const tile = e.target.closest(".tile");
  if (!tile) return;
  toggleMarket(tile.dataset.loc, true);
  document.getElementById(`m-${tile.dataset.loc}`).scrollIntoView({ behavior: "smooth" });
};
$("#searchResults").onclick = (e) => {
  const chip = e.target.closest(".chip");
  if (chip) $("#marketTiles").onclick({ target: $(`#marketTiles .tile[data-loc="${chip.dataset.loc}"]`) });
};
$("#globalSearch").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(runGlobalSearch, 250); };
$("#scanAllBtn").onclick = async () => {
  try {
    await api("/api/scans", { method: "POST", body: {} });
    toast("Скан всех рынков запущен — это займёт несколько часов");
    $("#scanAllBtn").disabled = true;
  } catch (e) { toast(e.message); }
};
$("#marketSections").addEventListener("click", async (e) => {
  const sec = e.target.closest(".market");
  if (!sec) return;
  const loc = sec.dataset.loc;
  try {
    if (e.target.closest(".act-prompt")) { await copyText(await marketPrompt(loc), "Промпт"); return; }
    if (e.target.closest(".act-scan")) {
      await api("/api/scans", { method: "POST", body: { locales: [loc] } });
      toast("Скан рынка запущен");
      return;
    }
    if (e.target.closest(".act-more")) { state.open.get(loc).limit += PAGE; loadMarket(loc); return; }
    if (e.target.closest(".track")) { await trackFromMarket(e.target.closest(".track"), loc); return; }
  } catch (err) { toast(err.message); return; }
  const row = e.target.closest("tr.row");
  if (row) { toggleTopApps(row, marketOf(loc).country, 8); return; }
  if (e.target.closest(".market-head")) toggleMarket(loc);
});
$("#marketSections").addEventListener("input", (e) => {
  if (!e.target.classList.contains("market-q")) return;
  const loc = e.target.closest(".market").dataset.loc;
  const st = state.open.get(loc);
  st.q = e.target.value;
  st.limit = PAGE;
  st.focus = true;
  clearTimeout(st.timer);
  st.timer = setTimeout(() => loadMarket(loc), 250);
});

// apps view
$("#appSelect").onchange = (e) => { state.appId = Number(e.target.value); state.editing = null; loadApp(); updateTrackHint(); };
$("#addAppBtn").onclick = addAppDialog;
$("#emptyAddApp").onclick = addAppDialog;
$("#addKwBtn").onclick = addKeywordsDialog;
$("#checkBtn").onclick = async () => {
  try {
    await api("/api/app-runs", { method: "POST", body: { app_id: state.appId } });
    toast("Проверка позиций запущена: ~3 сек на ключ из‑за лимитов Apple");
    $("#checkBtn").disabled = true;
    poll.checking = true;
  } catch (e) { toast(e.message); }
};
$("#deleteAppBtn").onclick = async () => {
  const app = currentApp();
  if (!app || !confirm(`Удалить «${app.name}» вместе с ключами и историей позиций?`)) return;
  await api(`/api/apps/${app.id}`, { method: "DELETE" });
  state.appId = null;
  await loadApps();
};
$("#appTiles").onclick = (e) => {
  const tile = e.target.closest(".tile");
  if (!tile) return;
  toggleAppSection(tile.dataset.loc, true);
  document.getElementById(`a-${tile.dataset.loc}`).scrollIntoView({ behavior: "smooth" });
};
$("#appSections").addEventListener("click", async (e) => {
  const sec = e.target.closest(".market");
  if (!sec) return;
  const loc = sec.dataset.loc;
  if (e.target.closest(".act-prompt")) { copyText(appPrompt(loc), "Промпт"); return; }
  if (e.target.closest(".act-edit")) { state.editing = loc; renderApp(); return; }
  if (e.target.closest(".act-cancel")) { state.editing = null; renderApp(); return; }
  if (e.target.closest(".del")) {
    await api(`/api/app-keywords/${e.target.closest("tr").dataset.id}`, { method: "DELETE" });
    await loadApps();
    return;
  }
  const row = e.target.closest("tr.row");
  if (row) {
    const next = row.nextElementSibling;
    if (next?.classList.contains("apps")) { next.remove(); return; }
    const kw = state.appData.markets.find((m) => m.locale === loc).items.find((k) => String(k.id) === row.dataset.id);
    const tr = document.createElement("tr");
    tr.className = "apps";
    tr.innerHTML = `<td colspan="6">${kw.top_apps.length ? `<div class="muted" style="margin-bottom:6px">Топ выдачи на ${fmtDate(kw.checked_at)}:</div>${appsList(kw.top_apps)}` : '<span class="muted">Ещё не проверялось</span>'}</td>`;
    row.after(tr);
    return;
  }
  if (e.target.closest(".market-head")) toggleAppSection(loc);
});
$("#appSections").addEventListener("submit", async (e) => {
  e.preventDefault();
  const loc = e.target.closest(".market").dataset.loc;
  const fd = new FormData(e.target);
  try {
    await api(`/api/apps/${state.appId}/meta/${encodeURIComponent(loc)}`, { method: "PUT",
      body: { title: fd.get("title"), subtitle: fd.get("subtitle"), keywords: fd.get("keywords") } });
    state.editing = null;
    await loadApp();
  } catch (err) { toast(err.message); }
});

(async function init() {
  state.meta = await api("/api/meta");
  for (const loc of storageGet("open") || []) {
    if (marketOf(loc)) state.open.set(loc, { q: "", limit: PAGE, data: null });
  }
  for (const loc of storageGet("appOpen") || []) state.appOpen.add(loc);
  setView(storageGet("view") === "apps" ? "apps" : "market");
  state.overview = await api("/api/markets");
  renderMarketTiles();
  renderMarketSections();
  await loadApps();
  for (const loc of state.open.keys()) loadMarket(loc);
  poll();
})().catch((e) => toast(e.message));

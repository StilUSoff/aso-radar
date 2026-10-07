"use strict";

const $ = (s, root = document) => root.querySelector(s);
const PAGE = 100;
const state = {
  meta: null,
  view: "market",
  overview: [],                  // market tiles
  apps: [], appId: null, appData: null,
  modal: null,                   // {kind: "market", loc, q, limit, data} | {kind: "app", loc, editing}
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
const posClass = (rank) => rank <= 10 ? "p1" : rank <= 50 ? "p2" : "p3";

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
  toast.timer = setTimeout(() => t.classList.add("hidden"), 4500);
}

function storageGet(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } }
function storageSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} }

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
    <a class="app" href="${esc(a.url || `https://apps.apple.com/app/id${a.id}`)}" target="_blank" rel="noopener">
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

// ---------- small form dialog ----------

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
    $("#dlgError").textContent = "";
    try {
      await onOk(new FormData($("#dlgForm")));
      dlg.close();
    } catch (err) {
      $("#dlgError").textContent = err.message;
    } finally {
      $("#dlgOk").disabled = false;
    }
  };
  if (!dlg.open) dlg.showModal();
}

function marketPicker(checked = []) {
  return `
    <div class="pick-tools">
      <button type="button" data-pick="all">Все</button>
      <button type="button" data-pick="none">Снять</button>
      <span class="muted pick-count"></span>
    </div>
    <div class="pick">${state.meta.markets.map((m) =>
      `<label><input type="checkbox" name="loc" value="${m.locale}" ${checked.includes(m.locale) ? "checked" : ""}>${flag(m.country)} ${esc(m.name)}</label>`).join("")}</div>`;
}

function updatePickCount(root) {
  const n = root.querySelectorAll(".pick input:checked").length;
  const el = root.querySelector(".pick-count");
  if (el) el.textContent = `выбрано: ${n}`;
}

$("#dlg").addEventListener("click", (e) => {
  const tool = e.target.closest("[data-pick]");
  if (!tool) return;
  $("#dlg").querySelectorAll(".pick input").forEach((i) => { i.checked = tool.dataset.pick === "all"; });
  updatePickCount($("#dlg"));
});
$("#dlg").addEventListener("change", () => updatePickCount($("#dlg")));

// ---------- detail modal ----------

function openDetail() {
  const d = $("#detail");
  if (!d.open) d.showModal();
}

function closeDetail() {
  state.modal = null;
  if ($("#detail").open) $("#detail").close();
}

$("#detail").addEventListener("close", () => { state.modal = null; });
$("#detailClose").onclick = closeDetail;
$("#detail").addEventListener("click", (e) => { if (e.target === $("#detail")) closeDetail(); });

function setDetailHead(country, title, stats, actions) {
  $("#detailTitle").innerHTML = `<span class="flag">${flag(country)}</span>${esc(title)}`;
  $("#detailStats").innerHTML = stats;
  $("#detailActions").innerHTML = actions;
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

async function openMarketModal(loc) {
  state.modal = { kind: "market", loc, q: "", limit: PAGE, data: null };
  const m = marketOf(loc);
  setDetailHead(m.country, m.name, "Загрузка…", "");
  $("#detailBody").innerHTML = `<div class="empty">Загрузка…</div>`;
  openDetail();
  await loadMarketModal();
}

async function loadMarketModal() {
  const md = state.modal;
  if (md?.kind !== "market") return;
  const params = new URLSearchParams({ limit: md.limit, q: md.q });
  const data = await api(`/api/markets/${encodeURIComponent(md.loc)}?${params}`);
  if (state.modal !== md) return; // closed or switched meanwhile
  md.data = data;
  renderMarketModal();
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

function renderMarketModal() {
  const md = state.modal;
  const { market, scan, items, total, has_previous, trend_dates } = md.data;
  const shared = market.shares_with ? marketOf(market.shares_with) : null;
  const app = currentApp();
  const tracked = trackedTerms(md.loc);

  setDetailHead(market.country, market.name,
    scan ? `<b>${scan.unique_terms}</b> запросов · ${scan.prefixes} префиксов · ${fmtDate(scan.scanned_at)}` +
      (items[0] && !md.q ? ` &nbsp; Top: <b>${esc(items[0].term)}</b>` : "") : "ещё не сканировался",
    `<button class="act-prompt" ${scan ? "" : "disabled"}>Copy /strategy prompt</button>
     ${scan ? `<a href="/api/markets/${encodeURIComponent(md.loc)}/export.csv"><button>CSV</button></a>` : ""}
     <button class="act-scan">${scan ? "Пересканировать" : "Сканировать"}</button>`);

  const info = `
    <div class="meta-box">
      <div class="hdr">Параметры сбора
        <span class="pill">App Store ${market.country.toUpperCase()} · ${market.storefront}</span>
        <span class="pill">${esc(market.locale)}</span>
        ${scan ? `<span class="pill">${fmtDateTime(scan.scanned_at)}</span>` : ""}
      </div>
      <dl class="meta-grid">
        <dt>Алфавиты</dt><dd class="mono">${esc(seedText(market.seeds))} · слова‑мосты</dd>
        <dt>Сканов</dt><dd>${trend_dates.length ? `${trend_dates.length} (с ${fmtDate(trend_dates[0])})` : "—"}</dd>
        ${shared ? `<dt>Совпадает с</dt><dd>${esc(shared.name)}: та же витрина и тот же алфавит</dd>` : ""}
      </dl>
    </div>`;

  if (!scan) {
    $("#detailBody").innerHTML = info + `<div class="empty">Данных нет — нажмите «Сканировать».</div>`;
    return;
  }

  const rows = items.map((k) => `
    <tr class="row" data-term="${esc(k.term)}">
      <td>${esc(k.term)}</td>
      <td><span class="pos ${posClass(k.rank)}">#${k.rank}</span></td>
      <td>${deltaCell(k.rank, k.prev_rank, has_previous, k.is_new)}</td>
      <td>${sparkline(k.trend)}</td>
      <td title="относительная популярность (лидер рынка = 100)"><div class="bar"><span style="width:${Math.max(1.5, Math.log10(1 + k.score * 9.99) * 50)}%"></span></div></td>
      <td class="muted">${fmtScore(k.score)}</td>
      <td class="mono muted" title="кратчайший префикс и позиция в подсказках">${esc(k.best_prefix)} → ${k.best_pos}</td>
      ${app ? `<td><button class="iconbtn track ${tracked.has(k.term) ? "done" : ""}" title="${tracked.has(k.term) ? "уже отслеживается" : `отслеживать позицию «${esc(app.name)}» по этому запросу`}">${tracked.has(k.term) ? "✓" : "+"}</button></td>` : ""}
    </tr>`).join("");

  $("#detailBody").innerHTML = info + `
    <div class="toolbar">
      <input type="search" class="market-q" placeholder="Фильтр запросов" value="${esc(md.q)}">
      <span class="muted">${total} ${plural(total, "запрос", "запроса", "запросов")}</span>
      ${app ? `<span class="muted">«+» — отслеживать для «${esc(app.name)}»</span>` : ""}
    </div>
    <div class="table-wrap"><table class="kw">
      <thead><tr><th>Keyword</th><th>Position</th><th>Δ</th><th>Trend</th><th colspan="2">Popularity</th><th>Prefix → pos</th>${app ? "<th></th>" : ""}</tr></thead>
      <tbody>${rows || `<tr><td colspan="8" class="empty">Ничего не найдено</td></tr>`}</tbody>
    </table></div>
    ${items.length < total ? `<button class="more act-more">Показать ещё (${total - items.length})</button>` : ""}`;
  const input = $(".market-q");
  if (md.focus) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); md.focus = false; }
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
  if (state.modal?.kind === "market") loadMarketModal();
}

async function trackFromMarket(btn) {
  const app = currentApp();
  if (!app || btn.classList.contains("done")) return;
  const loc = state.modal.loc;
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
  renderAppTiles();
  document.querySelectorAll(".needs-app").forEach((el) => el.classList.toggle("disabled", !pick));
  $("#appContent").classList.toggle("hidden", !pick);
  if (pick) await loadApp(); else state.appData = null;
}

function renderAppTiles() {
  $("#appTilesBar").innerHTML = state.apps.map((a) => `
    <button class="tile app-tile ${a.id === state.appId ? "active" : ""}" data-app="${a.id}">
      <img src="${esc(a.icon)}" alt="">
      <span class="name">${esc(a.name)}</span>
      <span class="sub">${a.keyword_count} ${plural(a.keyword_count, "ключ", "ключа", "ключей")} · ${a.markets.length} гео</span>
    </button>`).join("") + `
    <button class="tile app-tile add" id="addAppTile"><span class="plus">+</span><span class="name">Добавить приложение</span></button>`;
}

async function loadApp() {
  storageSet("appId", state.appId);
  state.appData = await api(`/api/apps/${state.appId}/markets`);
  renderApp();
  if (state.modal?.kind === "app") renderAppModal();
}

function renderApp() {
  const { app, markets, total } = state.appData;
  $("#appSub").innerHTML = `<b>${esc(app.name)}</b> · ID ${app.id}${app.seller ? " · " + esc(app.seller) : ""} · ${total.keywords} ключей · ` +
    `ранжируется ${total.ranked} · в топ‑50 ${total.top50} · в топ‑10 ${total.top10}`;
  $("#appCsv").href = `/api/apps/${app.id}/export.csv`;
  $("#appTiles").innerHTML = markets.map((m) => `
    <button class="tile g-${m.grade}" data-loc="${m.locale}">
      <span class="name"><span class="flag">${flag(m.country)}</span>${esc(m.name)}</span>
      <span class="med">${m.median != null ? `#${m.median} <small>median</small>` : `<small>— median</small>`}</span>
      <span class="sub">ranked ${m.ranked}/${m.keywords}</span>
    </button>`).join("") || `<div class="muted">Выберите гео кнопкой «Гео» и добавьте ключи.</div>`;
}

function metaRow(label, value, limit, field, editing) {
  if (editing) {
    return `<dt>${label}</dt><dd><input type="text" name="${field}" value="${esc(value)}" maxlength="${limit * 2}"></dd>`;
  }
  const len = [...(value || "")].length;
  return `<dt>${label}</dt><dd class="mono">${value ? esc(value) : '<span class="faint">—</span>'}${value ? `<span class="count ${len > limit ? "over" : ""}">${len}/${limit}</span>` : ""}</dd>`;
}

function openAppModal(loc) {
  state.modal = { kind: "app", loc, editing: false };
  renderAppModal();
  openDetail();
}

function renderAppModal() {
  const md = state.modal;
  const m = state.appData.markets.find((x) => x.locale === md.loc);
  if (!m) { closeDetail(); return; }
  const meta = m.meta || {};
  const limits = state.appData.limits;
  const editing = md.editing;

  setDetailHead(m.country, m.name,
    `${m.ranked}/${m.keywords} indexed${m.best ? ` &nbsp; Best: <b>${esc(m.best.term)} #${m.best.rank}</b>` : ""}`,
    `<button class="act-prompt" ${m.keywords ? "" : "disabled"}>Copy /strategy prompt</button>
     <button class="act-addkw">+ Ключи</button>`);

  const rows = m.items.map((k) => `
    <tr class="row" data-id="${k.id}" data-term="${esc(k.term)}">
      <td>${esc(k.term)}</td>
      <td>${k.checked_at ? (k.rank ? `<span class="pos ${posClass(k.rank)}">#${k.rank}</span>` : `<span class="pos p3">&gt;200</span>`) : '<span class="faint">?</span>'}</td>
      <td>${deltaCell(k.rank, k.prev_rank, !!k.prev_checked_at, false)}</td>
      <td>${sparkline(k.trend, 201)}</td>
      <td class="muted">${k.checked_at ? fmtDate(k.checked_at) : "—"}</td>
      <td><button class="iconbtn del" title="Удалить ключ">✕</button></td>
    </tr>`).join("");

  $("#detailBody").innerHTML = `
    <form class="meta-box meta-form">
      <div class="hdr">Current metadata
        ${meta.version ? `<span class="pill">v${esc(meta.version)}</span>` : ""}
        ${meta.updated_at ? `<span class="pill">${fmtDate(meta.updated_at)}</span>` : ""}
        <span class="pill">${meta.source === "asc" ? "ASC: " : ""}${esc(m.locale)}</span>
        <span class="pill">App Store ${m.country.toUpperCase()}</span>
        <span class="spacer"></span>
        ${editing ? `<button type="submit" class="primary">Сохранить</button><button type="button" class="act-cancel">Отмена</button>`
          : `<button type="button" class="act-edit">Редактировать</button>`}
      </div>
      <dl class="meta-grid">
        ${metaRow("Title", meta.title || meta.store_title || "", limits.title, "title", editing)}
        ${metaRow("Subtitle", meta.subtitle || meta.store_subtitle || "", limits.subtitle, "subtitle", editing)}
        ${metaRow("Keywords", meta.keywords, limits.keywords, "keywords", editing)}
        ${!editing && meta.iap_names?.length ? `<dt>IAP names</dt><dd><div class="chips">${meta.iap_names.map((n) => `<span class="chip mono">${esc(n)}</span>`).join("")}</div></dd>` : ""}
        ${!editing && meta.source !== "asc" && !meta.keywords ? `<dt></dt><dd class="faint">Поле Keywords скрыто в App Store — его даёт импорт из App Store Connect, либо впишите вручную.</dd>` : ""}
      </dl>
    </form>
    ${m.items.length ? `<div class="table-wrap"><table class="kw">
      <thead><tr><th>Keyword</th><th>Position</th><th>Δ</th><th>Trend</th><th>Last</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>` : `<div class="empty">Ключей для этого гео пока нет — нажмите «+ Ключи».</div>`}`;
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
Subtitle: ${meta.subtitle || meta.store_subtitle || "—"}
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
    <label>Какие гео рассматривать</label>
    ${marketPicker([])}`,
  async (fd) => {
    const app = await api("/api/apps", { method: "POST", body: { app: fd.get("app"), locales: fd.getAll("loc") } });
    await loadApps(app.id);
    toast(`Добавлено: ${app.name}. Теперь добавьте ключи — кнопка «+ Ключевые слова».`);
  }, "Добавить");
  updatePickCount($("#dlg"));
}

function appGeoDialog() {
  const app = currentApp();
  openDialog(`Гео для «${app.name}»`, `
    <p class="muted">Плитки показываются для выбранных гео. Ключи в снятых гео не удаляются.</p>
    ${marketPicker(app.markets)}`,
  async (fd) => {
    await api(`/api/apps/${app.id}/markets`, { method: "PUT", body: { locales: fd.getAll("loc") } });
    await loadApps();
  }, "Сохранить");
  updatePickCount($("#dlg"));
}

const SOURCES = {
  market: "Топ рынка — все запросы гео из вкладки «Рынок»",
  listing: "Из страницы приложения в App Store (без авторизации)",
  asc: "Из App Store Connect (нужен API‑ключ владельца)",
  manual: "Вручную",
};

function estimate(n) {
  return `${n} ${plural(n, "ключ", "ключа", "ключей")} ≈ ${Math.ceil(n * 3.3 / 60)} мин проверки`;
}

function addKeywordsDialog(source = "market", preset) {
  const app = currentApp();
  preset = preset || app.markets;
  const fields = {
    market: `
      <label>Сколько запросов взять с каждого рынка</label>
      <select name="top">${[50, 100, 200, 500].map((n) => `<option ${n === 100 ? "selected" : ""}>${n}</option>`).join("")}</select>
      <label>Фильтр (необязательно) — только запросы, содержащие текст</label>
      <input type="text" name="q" placeholder="например: dream">
      <label>Гео</label>${marketPicker(preset)}
      <p class="muted">Берётся последний скан рынка в порядке популярности. Рынок должен быть уже просканирован.</p>`,
    listing: `
      <p class="muted">Для каждого гео читается публичная страница приложения: Title, Subtitle и описание. Добавляются фразы из названия и запросы из топа рынка, в которых есть слова приложения. Поле Keywords в App Store скрыто — его даёт только App Store Connect.</p>
      <label>Максимум запросов из топа на гео</label>
      <select name="per_market">${[20, 50, 100, 200].map((n) => `<option ${n === 50 ? "selected" : ""}>${n}</option>`).join("")}</select>
      <label>Гео</label>${marketPicker(preset)}`,
    asc: `<div id="ascBox" class="muted">Загрузка…</div>`,
    manual: `
      <label>Ключевые слова (через запятую или с новой строки)</label>
      <textarea name="terms" placeholder="dream journal, interprétation des rêves"></textarea>
      <label>Гео</label>${marketPicker(preset)}`,
  };
  const actions = {
    market: async (fd) => {
      const r = await api(`/api/apps/${state.appId}/keywords/from-market`, { method: "POST",
        body: { locales: fd.getAll("loc"), top: Number(fd.get("top")), q: fd.get("q") || "" } });
      return `Добавлено ${r.added}` + (r.markets_without_data.length ? `. Нет данных рынка: ${r.markets_without_data.join(", ")}` : "");
    },
    listing: async (fd) => {
      const r = await api(`/api/apps/${state.appId}/keywords/from-listing`, { method: "POST",
        body: { locales: fd.getAll("loc"), per_market: Number(fd.get("per_market")) } });
      const missing = r.markets.filter((m) => m.error).map((m) => m.market);
      return `Добавлено ${r.added}` + (missing.length ? `. Приложения нет в: ${missing.join(", ")}` : "");
    },
    asc: async (fd) => {
      if (fd.get("key_id")) {
        await api("/api/asc-accounts", { method: "POST", body: {
          name: fd.get("name"), key_id: fd.get("key_id"), issuer_id: fd.get("issuer_id"), private_key: fd.get("private_key") } });
      }
      const r = await api(`/api/apps/${state.appId}/import-asc`, { method: "POST", body: { replace: fd.has("replace") } });
      return `Версия ${r.version}: ${r.locales} локалей, добавлено ${r.added}` +
        (r.skipped_locales.length ? `. Нет в списке рынков: ${r.skipped_locales.join(", ")}` : "");
    },
    manual: async (fd) => {
      const r = await api(`/api/apps/${state.appId}/keywords`, { method: "POST",
        body: { locales: fd.getAll("loc"), terms: fd.get("terms") } });
      return `Добавлено ${r.added}`;
    },
  };
  openDialog("Добавить ключевые слова", `
    <label>Источник</label>
    <select name="source" id="kwSource">${Object.entries(SOURCES).map(([k, v]) =>
      `<option value="${k}" ${k === source ? "selected" : ""}>${v}</option>`).join("")}</select>
    <div id="kwFields">${fields[source]}</div>`,
  async (fd) => {
    const msg = await actions[fd.get("source")](fd);
    await loadApps();
    toast(`${msg}. Всего ${estimate(state.appData?.total.keywords || 0)} — нажмите «Проверить позиции».`);
  }, "Добавить");
  updatePickCount($("#dlg"));
  $("#kwSource").onchange = (e) => addKeywordsDialog(e.target.value, preset);
  if (source === "asc") renderAscBox();
}

async function renderAscBox() {
  const accounts = await api("/api/asc-accounts");
  const box = $("#ascBox");
  if (!box) return;
  const list = accounts.map((a) => `<div>🔑 ${esc(a.name)} <span class="faint mono">${esc(a.key_id)}</span>
    <button type="button" class="iconbtn del" data-acc="${a.id}" title="Отключить ключ">✕</button></div>`).join("");
  box.classList.remove("muted");
  box.innerHTML = `
    <p class="muted">Подтягивает Title, Subtitle, скрытое поле Keywords и названия IAP каждой локализации текущей версии. Ключи из поля Keywords добавляются в отслеживание на своих гео. Нужен ключ аккаунта разработчика, которому принадлежит приложение.</p>
    ${list ? `<label>Подключённые ключи</label>${list}` : ""}
    <details ${accounts.length ? "" : "open"}><summary>${accounts.length ? "Подключить ещё ключ" : "Подключить ключ App Store Connect"}</summary>
      <p class="muted">App Store Connect → Users and Access → Integrations → Team Keys → «+», роль App Manager. Issuer ID — над списком ключей.</p>
      <label>Название (например, имя команды)</label><input type="text" name="name">
      <label>Key ID</label><input type="text" name="key_id" placeholder="ABC123XYZ9">
      <label>Issuer ID</label><input type="text" name="issuer_id" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx">
      <label>Файл .p8</label><input type="file" id="p8file" accept=".p8">
      <textarea name="private_key" placeholder="-----BEGIN PRIVATE KEY-----…" style="min-height:70px"></textarea>
    </details>
    <label class="check" style="font-weight:400"><input type="checkbox" name="replace"> Заменить все текущие ключи приложения</label>`;
  $("#p8file").onchange = async (e) => {
    const f = e.target.files[0];
    if (f) $("textarea[name=private_key]", box).value = await f.text();
  };
  box.onclick = async (e) => {
    const del = e.target.closest("[data-acc]");
    if (!del || !confirm("Отключить этот ключ App Store Connect?")) return;
    await api(`/api/asc-accounts/${del.dataset.acc}`, { method: "DELETE" });
    renderAscBox();
  };
}

// =====================================================================
// Status polling (market scans + app checks)
// =====================================================================

async function poll() {
  const [scan, run] = await Promise.all([
    api("/api/scans/latest").catch(() => null), api("/api/app-runs/latest").catch(() => null)]);
  const scanning = scan?.status === "running";
  const checking = run?.status === "running";
  $("#scanAllBtn").textContent = scanning ? "Остановить скан" : "Сканировать все рынки";
  $("#scanAllBtn").classList.toggle("danger", scanning);
  $("#checkBtn").textContent = checking ? "Остановить проверку" : "Проверить позиции";
  $("#checkBtn").classList.toggle("danger", checking);
  const parts = [];
  if (scanning) parts.push(`Скан рынков: ${scan.current || ""} ${scan.total ? Math.min(99, Math.round(scan.done / scan.total * 100)) : 0}%`);
  if (checking) parts.push(`Позиции: ${run.done}/${run.total} (~${Math.ceil((run.total - run.done) * 3.3 / 60)} мин)`);
  if (!parts.length && scan?.finished_at) {
    parts.push(scan.status === "stopped" ? `Скан остановлен ${fmtDateTime(scan.finished_at)}`
      : scan.status === "failed" ? `Скан прерван: ${scan.error}` : `Рынки обновлены ${fmtDateTime(scan.finished_at)}`);
  }
  $("#status").textContent = parts.join(" · ");

  if ((scanning && scan.current !== poll.current) || (poll.scanning && !scanning)) refreshMarket();
  if (state.appId && ((checking && run.done !== poll.done && run.done % 10 === 0) || (poll.checking && !checking))) loadApp();
  poll.current = scan?.current;
  poll.done = run?.done;
  poll.scanning = scanning;
  poll.checking = checking;
  clearTimeout(poll.timer);
  poll.timer = setTimeout(poll, scanning || checking ? 4000 : 30000);
}

function pollSoon() {
  clearTimeout(poll.timer);
  poll.timer = setTimeout(poll, 800);
}

// =====================================================================
// Events
// =====================================================================

function setView(view) {
  state.view = view;
  storageSet("view", view);
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${view}`));
}
document.querySelectorAll(".tab").forEach((t) => { t.onclick = () => setView(t.dataset.view); });

// market view
$("#marketTiles").onclick = (e) => {
  const tile = e.target.closest(".tile");
  if (tile) openMarketModal(tile.dataset.loc);
};
$("#searchResults").onclick = (e) => {
  const chip = e.target.closest(".chip");
  if (chip) openMarketModal(chip.dataset.loc);
};
$("#globalSearch").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(runGlobalSearch, 250); };
$("#scanAllBtn").onclick = async () => {
  try {
    if (poll.scanning) {
      await api("/api/scans/stop", { method: "POST" });
      toast("Скан останавливается — уже готовые рынки сохранены");
    } else {
      await api("/api/scans", { method: "POST", body: {} });
      toast("Скан всех рынков запущен — это займёт несколько часов");
    }
    pollSoon();
  } catch (e) { toast(e.message); }
};

// apps view
$("#appTilesBar").onclick = (e) => {
  const tile = e.target.closest(".app-tile");
  if (!tile) return;
  if (tile.id === "addAppTile") { addAppDialog(); return; }
  state.appId = Number(tile.dataset.app);
  renderAppTiles();
  loadApp();
};
$("#geoBtn").onclick = appGeoDialog;
$("#addKwBtn").onclick = () => addKeywordsDialog();
$("#checkBtn").onclick = async () => {
  try {
    if (poll.checking) {
      await api("/api/app-runs/stop", { method: "POST" });
      toast("Проверка останавливается — уже полученные позиции сохранены");
    } else {
      await api("/api/app-runs", { method: "POST", body: { app_id: state.appId } });
      toast(`Проверка запущена: ${estimate(state.appData?.total.keywords || 0)}`);
    }
    pollSoon();
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
  if (tile) openAppModal(tile.dataset.loc);
};

// detail modal (market or app geo)
$("#detail").addEventListener("click", async (e) => {
  const md = state.modal;
  if (!md) return;
  try {
    if (md.kind === "market") {
      if (e.target.closest(".act-prompt")) { await copyText(await marketPrompt(md.loc), "Промпт"); return; }
      if (e.target.closest(".act-scan")) {
        await api("/api/scans", { method: "POST", body: { locales: [md.loc] } });
        toast("Скан рынка запущен — остановить можно кнопкой «Остановить скан»");
        pollSoon();
        return;
      }
      if (e.target.closest(".act-more")) { md.limit += PAGE; loadMarketModal(); return; }
      if (e.target.closest(".track")) { await trackFromMarket(e.target.closest(".track")); return; }
      const row = e.target.closest("tr.row");
      if (row) toggleTopApps(row, marketOf(md.loc).country, 8);
      return;
    }
    // app geo
    if (e.target.closest(".act-prompt")) { copyText(appPrompt(md.loc), "Промпт"); return; }
    if (e.target.closest(".act-addkw")) { addKeywordsDialog("market", [md.loc]); return; }
    if (e.target.closest(".act-edit")) { md.editing = true; renderAppModal(); return; }
    if (e.target.closest(".act-cancel")) { md.editing = false; renderAppModal(); return; }
    if (e.target.closest(".del")) {
      await api(`/api/app-keywords/${e.target.closest("tr").dataset.id}`, { method: "DELETE" });
      await loadApps();
      return;
    }
    const row = e.target.closest("tr.row");
    if (row) {
      const next = row.nextElementSibling;
      if (next?.classList.contains("apps")) { next.remove(); return; }
      const kw = state.appData.markets.find((m) => m.locale === md.loc).items.find((k) => String(k.id) === row.dataset.id);
      const tr = document.createElement("tr");
      tr.className = "apps";
      tr.innerHTML = `<td colspan="6">${kw.top_apps.length ? `<div class="muted" style="margin-bottom:6px">Топ выдачи на ${fmtDate(kw.checked_at)}:</div>${appsList(kw.top_apps)}` : '<span class="muted">Ещё не проверялось</span>'}</td>`;
      row.after(tr);
    }
  } catch (err) { toast(err.message); }
});
$("#detail").addEventListener("input", (e) => {
  const md = state.modal;
  if (md?.kind !== "market" || !e.target.classList.contains("market-q")) return;
  md.q = e.target.value;
  md.limit = PAGE;
  md.focus = true;
  clearTimeout(md.timer);
  md.timer = setTimeout(loadMarketModal, 250);
});
$("#detail").addEventListener("submit", async (e) => {
  e.preventDefault();
  const md = state.modal;
  const fd = new FormData(e.target);
  try {
    await api(`/api/apps/${state.appId}/meta/${encodeURIComponent(md.loc)}`, { method: "PUT",
      body: { title: fd.get("title"), subtitle: fd.get("subtitle"), keywords: fd.get("keywords") } });
    md.editing = false;
    await loadApp();
  } catch (err) { toast(err.message); }
});

(async function init() {
  state.meta = await api("/api/meta");
  setView(storageGet("view") === "apps" ? "apps" : "market");
  state.overview = await api("/api/markets");
  renderMarketTiles();
  await loadApps();
  poll();
})().catch((e) => toast(e.message));

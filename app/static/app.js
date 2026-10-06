"use strict";

const $ = (s) => document.querySelector(s);
const state = { meta: null, apps: [], appId: null, keywords: [], summary: null,
  sort: { key: "rank", dir: 1 }, country: "", openHistory: null };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const flag = (cc) => cc.toUpperCase().replace(/./g, (c) => String.fromCodePoint(0x1f1a5 + c.charCodeAt(0)));
const countryName = (cc) => state.meta?.countries[cc] || cc.toUpperCase();
const fmtDate = (s) => s ? new Date(s).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—";

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText);
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), 4000);
}

// Positions gained since the previous check; leaving/entering top-200 counts as 201.
function delta(k) {
  if (!k.prev_checked_at) return 0;
  return (k.prev_rank || 201) - (k.rank || 201);
}

function rankBadge(rank, checked) {
  if (!checked) return `<span class="rank none">?</span>`;
  if (!rank) return `<span class="rank none">—</span>`;
  const cls = rank <= 10 ? "r1" : rank <= 50 ? "r2" : rank <= 100 ? "r3" : "r4";
  return `<span class="rank ${cls}">${rank}</span>`;
}

function deltaCell(k) {
  const d = delta(k);
  if (!d) return k.prev_checked_at ? `<span class="muted">·</span>` : "";
  if (!k.prev_rank) return `<span class="up">new</span>`;
  if (!k.rank) return `<span class="down">out</span>`;
  return d > 0 ? `<span class="up">▲${d}</span>` : `<span class="down">▼${-d}</span>`;
}

// ---------- loading ----------

async function loadApps(selectId) {
  state.apps = await api("/api/apps");
  const sel = $("#appSelect");
  sel.innerHTML = state.apps.map((a) => `<option value="${a.id}">${esc(a.name)} (${a.keyword_count})</option>`).join("");
  const saved = Number(localStorageGet("appId"));
  const pick = selectId || (state.apps.some((a) => a.id === state.appId) ? state.appId
    : state.apps.some((a) => a.id === saved) ? saved : state.apps[0]?.id);
  state.appId = pick || null;
  sel.value = pick || "";
  sel.classList.toggle("hidden", !state.apps.length);
  $("#empty").classList.toggle("hidden", !!state.apps.length);
  $("#dashboard").classList.toggle("hidden", !state.apps.length);
  if (pick) await loadApp();
}

async function loadApp() {
  localStorageSet("appId", state.appId);
  const app = state.apps.find((a) => a.id === state.appId);
  $("#appIcon").src = app.icon || "";
  $("#appName").textContent = app.name;
  $("#exportBtn").href = `/api/apps/${app.id}/export.csv`;
  const [kws, summary] = await Promise.all([
    api(`/api/apps/${app.id}/keywords`), api(`/api/apps/${app.id}/summary`)]);
  state.keywords = kws;
  state.summary = summary;
  $("#appMeta").textContent = `ID ${app.id}${app.seller ? " · " + app.seller : ""} · последняя проверка: ${fmtDate(summary.last_checked)}`;
  renderCards();
  renderCountries();
  renderCountryFilter();
  renderKeywords();
}

function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch {} }

// ---------- rendering ----------

function renderCards() {
  const t = state.summary.total;
  const cards = [
    ["Ключей", t.keywords],
    ["Стран", state.summary.countries.length],
    ["В топ‑200", t.ranked],
    ["В топ‑50", t.top50],
    ["В топ‑10", t.top10],
    ["Выросли / упали", `<span class="up">${t.improved}</span> / <span class="down">${t.declined}</span>`],
  ];
  $("#cards").innerHTML = cards.map(([l, v]) => `<div class="card"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
}

function renderCountries() {
  const rows = state.summary.countries;
  $("#countryTable tbody").innerHTML = rows.map((c) => {
    const w = (n) => (c.keywords ? (n / c.keywords) * 100 : 0).toFixed(1);
    return `<tr data-cc="${c.country}" class="${state.country === c.country ? "active" : ""}">
      <td><span class="flag">${flag(c.country)}</span>${esc(c.name)}</td>
      <td class="num">${c.keywords}</td>
      <td class="num">${c.top10 || ""}</td>
      <td class="num">${c.top50 || ""}</td>
      <td class="num">${c.ranked || ""}</td>
      <td class="num">${c.best ? rankBadge(c.best, true) : ""}</td>
      <td><div class="cov" title="топ‑10 / топ‑50 / топ‑200 от всех ключей">
        <span style="width:${w(c.top10)}%;background:var(--r1)"></span>
        <span style="width:${w(c.top50 - c.top10)}%;background:var(--r2)"></span>
        <span style="width:${w(c.ranked - c.top50)}%;background:var(--r4)"></span>
      </div></td></tr>`;
  }).join("") || `<tr><td colspan="7" class="muted">Пока нет ключевых слов</td></tr>`;
}

function renderCountryFilter() {
  const ccs = [...new Set(state.keywords.map((k) => k.country))].sort();
  if (state.country && !ccs.includes(state.country)) state.country = "";
  $("#countryFilter").innerHTML = `<option value="">Все страны</option>` +
    ccs.map((c) => `<option value="${c}">${flag(c)} ${esc(countryName(c))}</option>`).join("");
  $("#countryFilter").value = state.country;
}

function filteredKeywords() {
  const q = $("#search").value.trim().toLowerCase();
  const rf = $("#rankFilter").value;
  const list = state.keywords.filter((k) => {
    if (state.country && k.country !== state.country) return false;
    if (q && !k.term.includes(q)) return false;
    if (rf === "10") return k.rank && k.rank <= 10;
    if (rf === "50") return k.rank && k.rank <= 50;
    if (rf === "ranked") return !!k.rank;
    if (rf === "none") return k.checked_at && !k.rank;
    if (rf === "up") return delta(k) > 0;
    if (rf === "down") return delta(k) < 0;
    return true;
  });
  const { key, dir } = state.sort;
  const val = (k) => key === "rank" ? (k.rank || 9999) : key === "delta" ? -delta(k) : k[key];
  return list.sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * dir || a.term.localeCompare(b.term));
}

function renderKeywords() {
  const list = filteredKeywords();
  $("#kwEmpty").classList.toggle("hidden", list.length > 0);
  $("#kwTable tbody").innerHTML = list.map((k) => {
    const top = k.top_apps[0];
    const leader = top ? `<div class="leader ${top.id === state.appId ? "me" : ""}" title="${esc(k.top_apps.map((a, i) => `${i + 1}. ${a.name}`).join("\n"))}">
      <img src="${esc(top.icon)}" alt="" loading="lazy"><span>${esc(top.name)}</span></div>` : "";
    return `<tr data-id="${k.id}">
      <td title="${esc(countryName(k.country))}${k.locale ? " · " + esc(k.locale) : ""}"><span class="flag">${flag(k.country)}</span>${k.country.toUpperCase()}</td>
      <td class="hist" title="История позиций">${esc(k.term)}</td>
      <td class="num">${rankBadge(k.rank, k.checked_at)}</td>
      <td class="num">${deltaCell(k)}</td>
      <td>${leader}</td>
      <td class="num"><button class="icon-btn del" title="Удалить">✕</button></td>
    </tr>`;
  }).join("");
}

async function toggleHistory(tr) {
  const existing = tr.nextElementSibling;
  if (existing?.classList.contains("history")) { existing.remove(); return; }
  const id = tr.dataset.id;
  const hist = await api(`/api/keywords/${id}/history`);
  const row = document.createElement("tr");
  row.className = "history";
  row.innerHTML = `<td colspan="6">${hist.length ? sparkline(hist) : '<span class="muted">Ещё не проверялось</span>'}</td>`;
  tr.after(row);
}

function sparkline(hist) {
  const W = 520, H = 70, P = 6, MAX = 201;
  const xs = (i) => P + (hist.length === 1 ? (W - 2 * P) / 2 : (i / (hist.length - 1)) * (W - 2 * P));
  const ys = (r) => P + ((Math.min(r || MAX, MAX) - 1) / (MAX - 1)) * (H - 2 * P);
  const pts = hist.map((h, i) => `${xs(i).toFixed(1)},${ys(h.rank).toFixed(1)}`).join(" ");
  const dots = hist.map((h, i) => `<circle cx="${xs(i).toFixed(1)}" cy="${ys(h.rank).toFixed(1)}" r="3" fill="var(--accent)"><title>${fmtDate(h.checked_at)}: ${h.rank || "нет в топ‑200"}</title></circle>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px;height:${H}px" role="img" aria-label="История позиций">
    <line x1="${P}" x2="${W - P}" y1="${ys(10)}" y2="${ys(10)}" stroke="var(--border)" stroke-dasharray="3 3"/>
    <line x1="${P}" x2="${W - P}" y1="${ys(50)}" y2="${ys(50)}" stroke="var(--border)" stroke-dasharray="3 3"/>
    <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2"/>${dots}</svg>
    <div class="muted">верх — позиция 1, пунктир — топ‑10 и топ‑50, низ — вне топ‑200</div>`;
}

// ---------- dialogs ----------

function openDialog(title, bodyHtml, onOk, okLabel = "OK") {
  const dlg = $("#dlg");
  $("#dlgTitle").textContent = title;
  $("#dlgBody").innerHTML = bodyHtml;
  $("#dlgError").textContent = "";
  $("#dlgOk").textContent = okLabel;
  $("#dlgOk").disabled = false;
  dlg.onclose = null;
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

function addAppDialog() {
  openDialog("Добавить приложение", `
    <label>App Store ID или ссылка</label>
    <input type="text" name="app" placeholder="https://apps.apple.com/app/id1234567890" required>
    <label>Витрина для поиска приложения</label>
    <input type="text" name="country" value="us" maxlength="2">`,
  async (fd) => {
    const app = await api("/api/apps", { method: "POST", body: { app: fd.get("app"), country: fd.get("country") || "us" } });
    await loadApps(app.id);
    toast(`Добавлено: ${app.name}`);
  }, "Добавить");
}

function addKeywordsDialog() {
  const ccs = Object.entries(state.meta.countries).sort((a, b) => a[1].localeCompare(b[1]));
  openDialog("Добавить ключевые слова", `
    <label>Ключевые слова (через запятую или с новой строки)</label>
    <textarea name="terms" required placeholder="dream dictionary, сонник, dream meaning"></textarea>
    <label>Страны</label>
    <div class="country-pick">${ccs.map(([cc, n]) =>
      `<label><input type="checkbox" name="cc" value="${cc}" ${cc === state.country ? "checked" : ""}>${flag(cc)} ${esc(n)}</label>`).join("")}</div>`,
  async (fd) => {
    const r = await api(`/api/apps/${state.appId}/keywords`, { method: "POST",
      body: { countries: fd.getAll("cc"), terms: fd.get("terms") } });
    await loadApps();
    toast(`Добавлено ключей: ${r.added}. Запустите проверку позиций.`);
  }, "Добавить");
}

function importDialog() {
  if (!state.meta.asc_configured) {
    openDialog("Импорт из App Store Connect", `<p>Ключ API App Store Connect не настроен.
      Задайте <code>ASC_KEY_ID</code>, <code>ASC_ISSUER_ID</code> и положите .p8‑файл в <code>secrets/</code> (см. README).</p>`, async () => {});
    return;
  }
  openDialog("Импорт из App Store Connect", `
    <p class="muted">Берёт поле Keywords из каждой локализации текущей версии приложения и сопоставляет локаль со страной (fr‑FR → FR, es‑MX → MX…).</p>
    <label class="check"><input type="checkbox" name="secondary"> Также дополнительные витрины локали (de‑DE → AT, CH; es‑MX → AR, CO…)</label>
    <label class="check"><input type="checkbox" name="replace"> Удалить ранее импортированные из ASC ключи</label>`,
  async (fd) => {
    const r = await api(`/api/apps/${state.appId}/import-asc`, { method: "POST",
      body: { include_secondary: fd.has("secondary"), replace: fd.has("replace") } });
    await loadApps();
    toast(`Версия ${r.version}: ${r.locales} локалей, добавлено ${r.added} ключей` +
      (r.skipped_locales.length ? `. Пропущены: ${r.skipped_locales.join(", ")}` : ""));
  }, "Импортировать");
}

// ---------- runs ----------

async function pollRun() {
  const run = await api("/api/runs/latest").catch(() => null);
  const running = run?.status === "running";
  $("#runBtn").disabled = running;
  if (running) {
    const pct = run.total ? Math.round((run.done / run.total) * 100) : 0;
    const left = Math.ceil(((run.total - run.done) * 3.3) / 60);
    $("#runStatus").textContent = `Проверка: ${run.done}/${run.total} (${pct}%) · ~${left} мин`;
  } else {
    $("#runStatus").textContent = run?.status === "failed" ? `Последняя проверка с ошибкой: ${run.error}` : "";
  }
  if (pollRun.wasRunning && !running && state.appId) await loadApp();
  if (running && state.appId && run.done !== pollRun.lastDone) {
    pollRun.lastDone = run.done;
    if (run.done % 20 === 0) loadApp(); // refresh table now and then while running
  }
  pollRun.wasRunning = running;
  setTimeout(pollRun, running ? 3000 : 15000);
}

async function startRun() {
  try {
    await api("/api/runs", { method: "POST", body: { app_id: state.appId } });
    pollRun.wasRunning = true;
    toast("Проверка запущена: ~3 сек на ключ из‑за лимитов Apple");
    $("#runBtn").disabled = true;
  } catch (e) { toast(e.message); }
}

// ---------- events ----------

$("#appSelect").onchange = (e) => { state.appId = Number(e.target.value); state.country = ""; loadApp(); };
$("#addAppBtn").onclick = addAppDialog;
$("#emptyAddBtn").onclick = addAppDialog;
$("#addKwBtn").onclick = addKeywordsDialog;
$("#importBtn").onclick = importDialog;
$("#runBtn").onclick = startRun;
$("#deleteAppBtn").onclick = async () => {
  const app = state.apps.find((a) => a.id === state.appId);
  if (!confirm(`Удалить «${app.name}» вместе со всеми ключами и историей?`)) return;
  await api(`/api/apps/${app.id}`, { method: "DELETE" });
  state.appId = null;
  await loadApps();
};
$("#search").oninput = renderKeywords;
$("#rankFilter").onchange = renderKeywords;
$("#countryFilter").onchange = (e) => { state.country = e.target.value; renderCountries(); renderKeywords(); };
$("#countryTable tbody").onclick = (e) => {
  const tr = e.target.closest("tr[data-cc]");
  if (!tr) return;
  state.country = state.country === tr.dataset.cc ? "" : tr.dataset.cc;
  $("#countryFilter").value = state.country;
  renderCountries();
  renderKeywords();
};
$("#kwTable thead").onclick = (e) => {
  const key = e.target.dataset.sort;
  if (!key) return;
  state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : 1 };
  renderKeywords();
};
$("#kwTable tbody").onclick = async (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  if (e.target.closest(".del")) {
    await api(`/api/keywords/${tr.dataset.id}`, { method: "DELETE" });
    await loadApp();
    return;
  }
  if (e.target.closest(".hist")) toggleHistory(tr);
};

(async function init() {
  state.meta = await api("/api/meta");
  await loadApps();
  pollRun();
})().catch((e) => toast(e.message));

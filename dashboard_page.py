# dashboard_page.py
"""Strona dashboardu (jeden plik HTML, bez zewnętrznych bibliotek - działa w sieci bez internetu)."""

PAGE = r"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Combo Test PSU - wyniki</title>
<style>
:root{
  color-scheme: light;
  --bg:#f2f2f0; --card:#fcfcfb; --border:#e4e3df; --grid:#ecebe7;
  --text:#0b0b0b; --text2:#52514e; --muted:#8a8984;
  --pass:#2a78d6; --fail:#e34948;
  --good-ink:#006300; --good-bg:#e7f4e7; --bad-ink:#b42323; --bad-bg:#fbe9e9;
  --warn-ink:#8a5300; --warn-bg:#fdf1dc; --idle-bg:#ecebe7;
  --brand:#2b4c8c; --brand-ink:#ffffff; --sel:#dbe7f8;
}
@media (prefers-color-scheme: dark){
  :root:where(:not([data-theme="light"])){
    color-scheme: dark;
    --bg:#111110; --card:#1a1a19; --border:#2e2e2b; --grid:#262624;
    --text:#f0efec; --text2:#c3c2b7; --muted:#8f8e86;
    --pass:#3987e5; --fail:#e66767;
    --good-ink:#6fd36f; --good-bg:#13301a; --bad-ink:#f29a9a; --bad-bg:#3a1717;
    --warn-ink:#f3c26b; --warn-bg:#3a2a0e; --idle-bg:#262624;
    --brand:#1d3566; --brand-ink:#f0efec; --sel:#203a63;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --bg:#111110; --card:#1a1a19; --border:#2e2e2b; --grid:#262624;
  --text:#f0efec; --text2:#c3c2b7; --muted:#8f8e86;
  --pass:#3987e5; --fail:#e66767;
  --good-ink:#6fd36f; --good-bg:#13301a; --bad-ink:#f29a9a; --bad-bg:#3a1717;
  --warn-ink:#f3c26b; --warn-bg:#3a2a0e; --idle-bg:#262624;
  --brand:#1d3566; --brand-ink:#f0efec; --sel:#203a63;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 "Segoe UI",Arial,sans-serif}
header{background:var(--brand);color:var(--brand-ink);padding:12px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
header h1{font-size:20px;margin:0;font-weight:600}
.live{font-size:12px;font-weight:700;padding:2px 8px;border-radius:10px;background:#e34948;color:#fff;display:none}
.live.on{display:inline-block}
header .upd{margin-left:auto;font-size:13px;opacity:.85}
main{max-width:1500px;margin:0 auto;padding:14px 16px 30px}
.filters{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.seg{display:flex;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:var(--card)}
.seg button{border:0;background:transparent;color:var(--text2);padding:7px 12px;font:inherit;cursor:pointer;border-right:1px solid var(--border)}
.seg button:last-child{border-right:0}
.seg button[aria-pressed="true"]{background:var(--sel);color:var(--text);font-weight:600}
input[type=date],.btn{font:inherit;padding:6px 10px;border:1px solid var(--border);border-radius:8px;background:var(--card);color:var(--text)}
.btn{cursor:pointer}
.sub{color:var(--text2);margin:4px 0 12px;font-size:15px}
.sub b{color:var(--text)}
.err{color:var(--bad-ink);font-weight:600;margin:6px 0}
.grid{display:grid;gap:12px}
.kpis{grid-template-columns:repeat(3,minmax(0,1fr))}
.minis{grid-template-columns:repeat(4,minmax(0,1fr));margin-top:12px}
.two{grid-template-columns:minmax(0,1fr) minmax(0,1fr);margin-top:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;min-width:0}
.card h2{font-size:13px;letter-spacing:.04em;text-transform:uppercase;color:var(--text2);margin:0 0 6px;font-weight:600}
.big{font-size:44px;font-weight:700;line-height:1.05}
.big small{font-size:14px;font-weight:500;color:var(--text2);margin-left:6px}
.counts{display:flex;gap:18px;margin-top:6px;font-size:15px;color:var(--text2)}
.counts b{color:var(--text);font-size:18px}
.yield{margin-top:8px;font-size:15px;color:var(--text2)}
.yield b{color:var(--text);font-size:20px}
.share{display:flex;gap:2px;height:10px;margin-top:10px}
.share span{display:block;height:100%;border-radius:2px}
.share .p{background:var(--pass)} .share .f{background:var(--fail)} .share .e{background:var(--grid);flex:1}
.mini .v{font-size:26px;font-weight:700}
.mini .d{color:var(--text2);font-size:12px}
.chart-card{margin-top:12px}
.legend{display:flex;gap:16px;color:var(--text2);font-size:13px;margin:2px 0 6px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:-1px}
#chart{width:100%;height:260px;position:relative}
#chart svg{display:block;width:100%;height:100%}
.tip{position:fixed;pointer-events:none;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:8px 10px;font-size:13px;box-shadow:0 4px 14px rgba(0,0,0,.15);display:none;z-index:10;min-width:150px}
.tip .t{color:var(--text2);margin-bottom:4px}
.tip .r{display:flex;align-items:center;gap:8px}
.tip .r i{display:inline-block;width:12px;height:3px;border-radius:2px}
.tip .r b{min-width:28px;text-align:right}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--text2);font-weight:600;padding:6px 8px;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:6px 8px;border-bottom:1px solid var(--grid);white-space:nowrap}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr.sum td{font-weight:700;border-top:1px solid var(--border)}
.scroll{overflow-x:auto}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-weight:600;font-size:12.5px}
.pill.ok{background:var(--good-bg);color:var(--good-ink)}
.pill.bad{background:var(--bad-bg);color:var(--bad-ink)}
.pill.warn{background:var(--warn-bg);color:var(--warn-ink)}
.pill.idle{background:var(--idle-bg);color:var(--text2)}
.hbar{display:flex;align-items:center;gap:8px}
.hbar .lab{flex:0 0 46%;overflow:hidden;text-overflow:ellipsis}
.hbar .track{flex:1;height:12px}
.hbar .fill{height:100%;background:var(--fail);border-radius:0 4px 4px 0;min-width:2px}
.hbar .val{width:32px;text-align:right;font-weight:600}
.hbars{display:grid;gap:6px}
.muted{color:var(--muted)}
.loading main{opacity:.6;transition:opacity .2s}
@media (max-width:900px){.kpis,.two{grid-template-columns:1fr}.minis{grid-template-columns:repeat(2,minmax(0,1fr))}.big{font-size:36px}}
</style>
</head>
<body>
<header>
  <h1>Combo Test PSU - wyniki testów</h1>
  <span class="live" id="live">LIVE</span>
  <span class="upd" id="upd">-</span>
</header>
<main>
  <div class="filters">
    <div class="seg" id="seg">
      <button data-s="current">Bieżąca zmiana</button>
      <button data-s="all">Cały dzień</button>
      <button data-s="1">Zmiana I</button>
      <button data-s="2">Zmiana II</button>
      <button data-s="3">Zmiana III</button>
    </div>
    <input type="date" id="day" aria-label="Dzień produkcyjny">
    <button class="btn" id="today">Dziś</button>
  </div>
  <div class="sub" id="sub">-</div>
  <div class="err" id="err" hidden></div>

  <section class="grid kpis" id="kpis"></section>
  <section class="grid minis" id="minis"></section>

  <section class="card chart-card">
    <h2>Testy COMBO na godzinę</h2>
    <div class="legend"><span><i style="background:var(--pass)"></i>PASS</span><span><i style="background:var(--fail)"></i>FAIL</span></div>
    <div id="chart"></div>
  </section>

  <section class="grid two">
    <div class="card"><h2>Stanowiska</h2><div class="scroll" id="stations"></div></div>
    <div class="card"><h2>Przyczyny FAIL</h2><div id="fails"></div></div>
  </section>
  <section class="grid two">
    <div class="card"><h2>Zmiany - cały dzień</h2><div class="scroll" id="shifts"></div></div>
    <div class="card"><h2>Profile (PN)</h2><div class="scroll" id="profiles"></div></div>
  </section>
  <section class="card" style="margin-top:12px"><h2>Ostatnie testy</h2><div class="scroll" id="last"></div></section>
</main>
<div class="tip" id="tip"></div>
<script>
"use strict";
const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const state = {shift: params.get("shift") || "current", day: params.get("day") || "", data: null};
const REFRESH_MS = 30000;
const NAMES = {combo: "COMBO (całość)", load: "Test obciążenia", hipot: "Hi-Pot"};

function el(tag, attrs, ...kids){
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)){
    if (k === "class") e.className = v; else if (k === "style") e.style.cssText = v; else e.setAttribute(k, v);
  }
  for (const k of kids){ if (k == null) continue; e.append(k instanceof Node ? k : document.createTextNode(String(k))); }
  return e;
}
const pct = (a, b) => b ? (100 * a / b).toFixed(1) + "%" : "-";
function pill(res){
  const m = {PASS: ["ok", "✓ PASS"], FAIL: ["bad", "✗ FAIL"], INVALID: ["warn", "! NIEWAŻNY"],
             ABORTED: ["warn", "■ PRZERWANY"], SKIP: ["idle", "pominięty"], OFF: ["idle", "wył."]};
  const [c, t] = m[res] || ["idle", res || "-"];
  return el("span", {class: "pill " + c}, t);
}

// ---------------- filtry ----------------
function syncUrl(){
  const p = new URLSearchParams();
  p.set("shift", state.shift);
  if (state.shift !== "current" && state.day) p.set("day", state.day);
  history.replaceState(null, "", "?" + p.toString());
}
function paintSeg(){
  for (const b of $("seg").querySelectorAll("button")) b.setAttribute("aria-pressed", b.dataset.s === state.shift);
  $("day").disabled = state.shift === "current";
}
$("seg").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  state.shift = b.dataset.s; paintSeg(); syncUrl(); load();
});
$("day").addEventListener("change", () => { state.day = $("day").value; if (state.shift === "current") state.shift = "all"; paintSeg(); syncUrl(); load(); });
$("today").addEventListener("click", () => { state.day = ""; state.shift = "current"; paintSeg(); syncUrl(); load(); });

// ---------------- dane ----------------
let timer = null;
async function load(){
  clearTimeout(timer);
  document.body.classList.add("loading");
  try{
    const q = new URLSearchParams({shift: state.shift});
    if (state.shift !== "current" && state.day) q.set("day", state.day);
    const r = await fetch("/api/report?" + q.toString(), {cache: "no-store"});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    state.data = d; $("err").hidden = true; render(d);
  }catch(e){
    $("err").hidden = false; $("err").textContent = "Brak połączenia z serwerem dashboardu: " + e.message + " (ponowienie za 30 s)";
  }finally{
    document.body.classList.remove("loading");
    timer = setTimeout(load, REFRESH_MS);
  }
}

// ---------------- render ----------------
function render(d){
  $("live").classList.toggle("on", d.is_live);
  $("upd").textContent = "Aktualizacja: " + d.now.slice(11) + "  ·  odświeżanie co 30 s";
  if (!state.day || state.shift === "current") $("day").value = d.day;
  $("sub").replaceChildren("Dzień ", el("b", null, d.day), "  ·  ", el("b", null, d.shift_label),
    "  ·  teraz: zmiana " + d.current.name + " (" + d.current.hours + ")");

  const k = $("kpis"); k.replaceChildren();
  for (const key of ["combo", "load", "hipot"]){
    const s = d.total[key];
    const share = el("div", {class: "share", role: "img", "aria-label": `PASS ${s.pass}, FAIL ${s.fail}`});
    if (s.tests){
      if (s.pass) share.append(el("span", {class: "p", style: `flex:${s.pass}`}));
      if (s.fail) share.append(el("span", {class: "f", style: `flex:${s.fail}`}));
    } else share.append(el("span", {class: "e"}));
    k.append(el("div", {class: "card"},
      el("h2", null, NAMES[key]),
      el("div", {class: "big"}, s.tests, el("small", null, "testów")),
      el("div", {class: "counts"},
        el("span", null, "✓ PASS ", el("b", null, s.pass)),
        el("span", null, "✗ FAIL ", el("b", null, s.fail))),
      el("div", {class: "yield"}, "Yield ", el("b", null, pct(s.pass, s.tests))),
      share));
  }
  const t = d.total, m = $("minis"); m.replaceChildren();
  for (const [lab, val, desc] of [
      ["FPY", pct(t.first_pass, t.first_tests), "PASS za pierwszym podejściem (per SN)"],
      ["Retesty", t.retest, "powtórne testy tego samego SN"],
      ["Nieważne", t.invalid, "brak komunikacji / błąd stanowiska"],
      ["Przerwane", t.aborted, "STOP lub otwarta klapa"]]){
    m.append(el("div", {class: "card mini"}, el("h2", null, lab), el("div", {class: "v"}, val), el("div", {class: "d"}, desc)));
  }

  drawChart(d);
  renderStations(d);
  renderFails(d);
  renderShifts(d);
  renderProfiles(d);
  renderLast(d);
}

function table(head, rows, sumRow){
  const thead = el("tr");
  for (const [h, n] of head) thead.append(el("th", n ? {class: "n"} : null, h));
  const tb = el("tbody");
  for (const r of rows){
    const tr = el("tr", r.cls ? {class: r.cls} : null);
    r.cells.forEach((c, i) => tr.append(el("td", head[i][1] ? {class: "n"} : null, c)));
    tb.append(tr);
  }
  return el("table", null, el("thead", null, thead), tb);
}
const STAT_HEAD = [["Testy", 1], ["PASS", 1], ["FAIL", 1], ["Yield", 1], ["Obc. FAIL", 1], ["Hi-Pot FAIL", 1], ["Retest", 1]];
const statCells = s => [s.combo.tests, s.combo.pass, s.combo.fail, pct(s.combo.pass, s.combo.tests), s.load.fail, s.hipot.fail, s.retest];

function renderStations(d){
  const box = $("stations");
  if (!d.stations.length){ box.replaceChildren(el("div", {class: "muted"}, "Brak testów w wybranym okresie")); return; }
  const rows = d.stations.map(s => {
    let st;
    if (!d.is_live || s.ago_min == null) st = el("span", {class: "muted"}, s.last || "-");
    else if (s.ago_min > d.stale_min) st = el("span", {class: "pill warn"}, `brak testów ${s.ago_min} min`);
    else st = el("span", {class: "pill ok"}, `${s.last}`);
    return {cells: [s.name, st, ...statCells(s)]};
  });
  box.replaceChildren(table([["Stanowisko"], ["Ostatni test"], ...STAT_HEAD], rows));
}
function renderShifts(d){
  const rows = d.shifts.map(s => ({cls: d.current.num === s.num && d.day === d.current.day ? "" : "",
    cells: [`Zmiana ${s.name} (${s.hours})`, ...statCells(s)]}));
  rows.push({cls: "sum", cells: ["Cały dzień", ...statCells(d.day_total)]});
  $("shifts").replaceChildren(table([["Zmiana"], ...STAT_HEAD], rows));
}
function renderProfiles(d){
  const box = $("profiles");
  if (!d.profiles.length){ box.replaceChildren(el("div", {class: "muted"}, "Brak testów")); return; }
  box.replaceChildren(table([["PN"], ...STAT_HEAD], d.profiles.map(p => ({cells: [p.name, ...statCells(p)]}))));
}
function renderFails(d){
  const box = $("fails");
  if (!d.fail_codes.length){ box.replaceChildren(el("div", {class: "muted"}, "Brak FAIL w wybranym okresie")); return; }
  const max = Math.max(...d.fail_codes.map(f => f.count));
  const wrap = el("div", {class: "hbars"});
  for (const f of d.fail_codes){
    wrap.append(el("div", {class: "hbar", title: `${f.code}: ${f.count}`},
      el("span", {class: "lab"}, f.code),
      el("span", {class: "track"}, el("div", {class: "fill", style: `width:${100 * f.count / max}%`})),
      el("span", {class: "val"}, f.count)));
  }
  box.replaceChildren(wrap);
}
function renderLast(d){
  const box = $("last");
  if (!d.last.length){ box.replaceChildren(el("div", {class: "muted"}, "Brak testów")); return; }
  box.replaceChildren(table([["Czas"], ["Zm."], ["Stanowisko"], ["PN"], ["SN"], ["Obciążenie"], ["Hi-Pot"], ["Wynik"]],
    d.last.map(r => ({cells: [r.ts.slice(11), ["", "I", "II", "III"][r.shift] || "?", r.station, r.pn,
      r.sn + (r.retest ? "  (retest)" : ""), pill(r.load), pill(r.hipot), pill(r.combo)]}))));
}

// ---------------- wykres godzinowy ----------------
function drawChart(d){
  const box = $("chart");
  const W = Math.max(320, box.clientWidth), H = box.clientHeight || 260;
  let hours = d.hours;
  if (d.shift_num){ const start = [0, 6, 14, 22][d.shift_num]; const set = new Set([...Array(8)].map((_, i) => (start + i) % 24)); hours = hours.filter(h => set.has(h.hour)); }
  const L = 36, R = 8, T = 10, B = 26, iw = W - L - R, ih = H - T - B;
  const maxV = Math.max(1, ...hours.map(h => h.pass + h.fail));
  const step = niceStep(maxV), top = Math.ceil(maxV / step) * step;
  const y = v => T + ih - ih * v / top;
  const bw = iw / hours.length, barW = Math.max(4, Math.min(36, bw * 0.62));
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Liczba testów COMBO na godzinę, PASS i FAIL");
  const mk = (tag, a) => { const e = document.createElementNS(NS, tag); for (const k in a) e.setAttribute(k, a[k]); return e; };
  for (let v = 0; v <= top; v += step){
    svg.append(mk("line", {x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: "var(--grid)", "stroke-width": 1}));
    const tx = mk("text", {x: L - 6, y: y(v) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--muted)"}); tx.textContent = v; svg.append(tx);
  }
  const every = bw < 26 ? 2 : 1;
  hours.forEach((h, i) => {
    const cx = L + bw * i + bw / 2, x = cx - barW / 2;
    const g = mk("g", {tabindex: 0});
    const segs = [];
    let base = 0;
    for (const [v, col] of [[h.pass, "var(--pass)"], [h.fail, "var(--fail)"]]){
      if (!v) continue;
      segs.push([base, base + v, col]); base += v;
    }
    segs.forEach(([a, b, col], j) => {
      const y1 = y(b), y0 = y(a), gap = j > 0 ? 2 : 0, hgt = Math.max(1, y0 - y1 - gap);
      const isTop = j === segs.length - 1;
      g.append(mk("path", {d: barPath(x, y1, barW, hgt, isTop ? 4 : 0), fill: col}));
    });
    g.append(mk("rect", {x: L + bw * i, y: T, width: bw, height: ih, fill: "transparent"}));
    const show = ev => tip(ev, h);
    g.addEventListener("pointermove", show); g.addEventListener("focus", show);
    g.addEventListener("pointerleave", hideTip); g.addEventListener("blur", hideTip);
    svg.append(g);
    if (i % every === 0){
      const tx = mk("text", {x: cx, y: H - 8, "text-anchor": "middle", "font-size": 11, fill: "var(--muted)"});
      tx.textContent = String(h.hour).padStart(2, "0"); svg.append(tx);
    }
  });
  svg.append(mk("line", {x1: L, x2: W - R, y1: y(0), y2: y(0), stroke: "var(--border)", "stroke-width": 1}));
  box.replaceChildren(svg);
}
function barPath(x, y, w, h, r){
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
function niceStep(m){ const raw = m / 4, p = Math.pow(10, Math.floor(Math.log10(raw || 1))); for (const k of [1, 2, 5, 10]) if (raw <= k * p) return Math.max(1, k * p); return 10 * p; }
function tip(ev, h){
  const t = $("tip"); const hh = String(h.hour).padStart(2, "0"), nx = String((h.hour + 1) % 24).padStart(2, "0");
  const row = (col, v, lab) => el("div", {class: "r"}, el("i", {style: `background:${col}`}), el("b", null, v), lab);
  t.replaceChildren(el("div", {class: "t"}, `Godz. ${hh}:00-${nx}:00`),
    row("var(--fail)", h.fail, "FAIL"), row("var(--pass)", h.pass, "PASS"), el("div", {class: "t", style: "margin:4px 0 0"}, `Razem: ${h.pass + h.fail}`));
  t.style.display = "block";
  const r = ev.clientX !== undefined ? {x: ev.clientX, y: ev.clientY} : (() => { const b = ev.target.getBoundingClientRect(); return {x: b.x + b.width / 2, y: b.y}; })();
  const w = t.offsetWidth;
  t.style.left = Math.min(window.innerWidth - w - 8, r.x + 14) + "px";
  t.style.top = Math.max(8, r.y - 20) + "px";
}
function hideTip(){ $("tip").style.display = "none"; }
let rz; window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => state.data && drawChart(state.data), 150); });

paintSeg(); load();
</script>
</body>
</html>
"""

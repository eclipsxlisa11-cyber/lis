const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const hm = t => t ? new Date(t * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"}) : "-";
const dm = t => t ? new Date(t * 1000).toLocaleDateString([], {day: "numeric", month: "short"}) + " " + hm(t).slice(0, 5) : "-";
const mins = s => s == null ? "-" : `${Math.floor(s / 3600) ? Math.floor(s / 3600) + " h " : ""}${Math.round(s % 3600 / 60)} min`;
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const STATUS = {
  PRESENT: ["On site", "t-info"], COMPLETE: ["Complete", "t-ok"], MISSING_OUT: ["No tap out", "t-warn"],
  EARLY_LEAVE: ["Left early", "t-warn"], ABSENT: ["Absent", "t-bad"], NOT_YET: ["Not arrived", "t-na"]};
const EXC = {
  DUPLICATE: ["Double tap", "t-warn"], MISSING_OUT: ["No tap out", "t-warn"], EARLY_LEAVE: ["Left early", "t-warn"],
  UNKNOWN: ["Unknown card", "t-bad"], NO_SESSION: ["No class open", "t-na"]};
const FEED = {IN: ["Tap in", "t-ok"], OUT: ["Tap out", "t-info"], DUPLICATE: ["Double tap", "t-warn"],
  UNKNOWN: ["Unknown", "t-bad"], NO_SESSION: ["No class", "t-na"]};

let sessionId = null, data = null, charts = {}, cardsLoaded = false;

async function api(path, body) {
  const r = await fetch(path, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {});
  return r.json();
}

function toast(msg, kind) {
  const t = $("toast"); t.textContent = msg; t.className = "toast " + kind; t.hidden = false;
  clearTimeout(toast.h); toast.h = setTimeout(() => t.hidden = true, 6000);
}

function renderKpis(d) {
  const k = d.kpi, rate = k.enrolled ? Math.round(100 * k.attended / k.enrolled) : 0;
  const items = [
    ["", k.enrolled, "Enrolled"], ["", `${k.attended} <small>(${rate}%)</small>`, "Tapped in"],
    ["", k.present_now, "On site now"], ["a", k.late, "Late arrivals"],
    ["a", k.flags, "Exceptions"], ["g", k.green, "Green zone"], ["a", k.amber, "Amber zone"], ["r", k.red, "Red zone"]];
  $("kpis").innerHTML = items.map(([c, v, l]) => `<div class="kpi ${c}"><b>${v}</b><span>${l}</span></div>`).join("");
}

function renderRegister(d) {
  const q = $("search").value.trim().toLowerCase();
  const rows = d.rows.filter(r => !q || r.name.toLowerCase().includes(q) || r.student_id.includes(q));
  $("register").tBodies[0].innerHTML = rows.map(r => {
    const [label, cls] = STATUS[r.status];
    return `<tr><td>${esc(r.name)}${r.late ? '<span class="late">LATE</span>' : ""}${r.doubles ? `<span class="late" title="Double taps blocked">x${r.doubles + 1} TAP</span>` : ""}</td>
      <td>${esc(r.student_id)}</td><td>${hm(r.in_ts)}</td><td>${hm(r.out_ts)}</td><td>${mins(r.dwell)}</td>
      <td><span class="tag ${cls}">${label}</span></td></tr>`;
  }).join("") || `<tr><td colspan="6" class="empty">No students match.</td></tr>`;
}

function renderLists(d) {
  $("exc-count").textContent = d.exceptions.length ? `${d.exceptions.length} to review` : "";
  $("exceptions").innerHTML = d.exceptions.map(e => {
    const [label, cls] = EXC[e.type];
    return `<li><span class="tag ${cls}">${label}</span><div class="meta"><b>${esc(e.student)}</b> ${e.student_id !== "-" ? "&middot; " + esc(e.student_id) : ""}
      <small>${esc(e.detail)} &middot; ${dm(e.ts)}</small></div></li>`;
  }).join("") || `<li class="empty">No exceptions for this session.</li>`;
  $("feed").innerHTML = d.feed.map(f => {
    const [label, cls] = FEED[f.event];
    return `<li><span class="tag ${cls}">${label}</span><div class="meta"><b>${esc(f.student)}</b></div><small class="muted">${hm(f.ts)}</small></li>`;
  }).join("") || `<li class="empty">No taps yet.</li>`;
  $("warnings").innerHTML = d.warnings.map(w =>
    `<li><span class="tag t-bad">${Math.round(w.attendance_pct)}%</span><div class="meta"><b>${esc(w.name)}</b>
     <small>${esc(w.delivery)} &middot; ${dm(w.ts)}</small></div></li>`).join("") || `<li class="empty">No warnings sent yet.</li>`;
}

function zoneBox(cls, title, zs, d) {
  const warnable = zs.filter(z => z.can_warn);
  return `<div class="zbox ${cls}"><h3><span>${title}</span><span>${zs.length}</span></h3><ul>` +
    (zs.map(z => `<li><div><b>${esc(z.name)}</b><small>${esc(z.student_id)} &middot; ${z.attended}/${z.held} classes${z.missing_out ? `, ${z.missing_out} no tap out` : ""}</small></div>
      <div><span class="pct">${z.pct}%</span>${cls === "r" ? (z.can_warn ? ` <button class="btn sm" data-warn="${esc(z.student_id)}">Warn</button>` :
        ` <small>warned ${dm(z.last_warned)}</small>`) : ""}</div></li>`).join("") || `<li class="muted">None</li>`) +
    `</ul>` + (cls === "r" && warnable.length ? `<div class="foot"><button class="btn" data-warn="${warnable.map(z => esc(z.student_id)).join(",")}">Warn all (${warnable.length})</button></div>` : "") + `</div>`;
}

function renderZones(d) {
  const by = z => d.zones.filter(x => x.zone === z);
  $("zlists").innerHTML = zoneBox("r", "Red zone: at risk", by("RED"), d) + zoneBox("a", "Amber zone: watch", by("AMBER"), d) + zoneBox("g", "Green zone: on track", by("GREEN"), d);
  $("policy").textContent = `Green ≥ ${d.policy.green_min}% · Red < ${d.policy.red_below}% · incomplete taps earn half credit`;
}

function mk(id, cfg) {
  if (charts[id]) { charts[id].data = cfg.data; charts[id].options = cfg.options; charts[id].update("none"); return; }
  charts[id] = new Chart($(id), cfg);
}

function renderCharts(d) {
  const G = css("--green"), A = css("--amber"), R = css("--red"), ink = css("--muted"), line = css("--line");
  const col = z => ({GREEN: G, AMBER: A, RED: R, NONE: ink}[z]);
  Chart.defaults.color = ink; Chart.defaults.font.family = "system-ui, sans-serif";
  const n = z => d.zones.filter(x => x.zone === z).length;
  mk("donut", {type: "doughnut", data: {labels: ["Green", "Amber", "Red"], datasets: [{data: [n("GREEN"), n("AMBER"), n("RED")], backgroundColor: [G, A, R], borderWidth: 0}]},
    options: {maintainAspectRatio: false, cutout: "62%", plugins: {legend: {position: "bottom"}, title: {display: true, text: "Students by zone"}}}});
  const zs = d.zones.filter(z => z.pct != null);
  mk("bars", {type: "bar", data: {labels: zs.map(z => z.name.split(" ")[0]), datasets: [{data: zs.map(z => z.pct), backgroundColor: zs.map(z => col(z.zone)), borderRadius: 3}]},
    options: {maintainAspectRatio: false, scales: {y: {min: 0, max: 100, grid: {color: line}, title: {display: true, text: "Attendance %"}}, x: {grid: {display: false}, ticks: {maxRotation: 60, autoSkip: false, font: {size: 10}}}},
      plugins: {legend: {display: false}, title: {display: true, text: "Attendance by student (lowest first)"}}}});
  mk("trend", {type: "line", data: {labels: d.turnout.map(t => t.name.replace(" Lecture", "")), datasets: [{data: d.turnout.map(t => t.pct), borderColor: css("--brand2"), backgroundColor: css("--brand2"), tension: .25, pointRadius: 3}]},
    options: {maintainAspectRatio: false, scales: {y: {min: 0, max: 100, grid: {color: line}}, x: {grid: {display: false}}},
      plugins: {legend: {display: false}, title: {display: true, text: "Class turnout %"}}}});
}

function renderHeader(d) {
  const sel = $("session");
  if (sel.options.length !== d.sessions.length) {
    sel.innerHTML = d.sessions.slice().reverse().map(s => `<option value="${s.id}">${esc(s.name)} (${dm(s.start).split(" ").slice(0, 2).join(" ")})</option>`).join("");
  }
  sel.value = d.selected.id; sessionId = d.selected.id;
  const live = $("live"); live.textContent = d.selected.live ? "● LIVE" : "Closed"; live.className = "pill" + (d.selected.live ? " live" : "");
  $("csv").href = "/api/export.csv?session=" + d.selected.id;
  $("clock").textContent = new Date(d.now * 1000).toLocaleString([], {weekday: "short", hour: "2-digit", minute: "2-digit"});
  if (!cardsLoaded) {
    $("sim-card").innerHTML = d.rows.map(r => `<option value="${esc(r.uid)}">${esc(r.name)} (${esc(r.student_id)})</option>`).join("");
    cardsLoaded = true;
  }
}

async function refresh() {
  try {
    data = await api("/api/dashboard" + (sessionId ? "?session=" + sessionId : ""));
    if (data.empty) return;
    renderHeader(data); renderKpis(data); renderRegister(data); renderLists(data); renderZones(data); renderCharts(data);
  } catch (e) { $("live").textContent = "Offline"; $("live").className = "pill"; }
}

async function tap(uid) {
  const r = await api("/api/tap", {uid});
  const kind = r.result === "IN" || r.result === "OUT" ? "ok" : r.result === "DUPLICATE" ? "warn" : "bad";
  toast(r.message + (r.warning ? ". " + r.warning : ""), r.warning && kind === "ok" ? "warn" : kind);
  refresh(); return r;
}

$("session").onchange = e => { sessionId = +e.target.value; refresh(); };
$("search").oninput = () => data && renderRegister(data);
$("sim-tap").onclick = () => tap($("sim-card").value);
$("sim-double").onclick = async () => { await tap($("sim-card").value); await tap($("sim-card").value); };
$("sim-unknown").onclick = () => tap("0000" + Math.floor(Math.random() * 1e6));
$("sim-reset").onclick = async () => { if (confirm("Replace all data with fresh demo data?")) { await api("/api/reset-demo", {}); cardsLoaded = false; sessionId = null; refresh(); } };
document.addEventListener("click", async e => {
  const ids = e.target.dataset && e.target.dataset.warn;
  if (!ids) return;
  e.target.disabled = true;
  const r = await api("/api/warn", {student_ids: ids.split(",")});
  toast(`${r.sent.length} warning${r.sent.length === 1 ? "" : "s"} recorded (${r.sent[0] ? r.sent[0].delivery : "none sent"})`, "ok");
  refresh();
});
refresh(); setInterval(refresh, 3000);

/* Kavach CTI - Operations lab 14 (monitoring dashboard, webhook alerts + drills, backups, restore verification, point-in-time
   recovery, benchmark + RPO/RTO, Lab 14 applied to the Lab 13/10 billing ledger). Loaded before labs.js; labs.js calls
   window.KavachLab14(helpers). Every number comes from /api/lab14/* = backend/app/lab14.py on the running deployment. */
window.KavachLab14 = (h) => {
  "use strict";
  const { $, $$, esc, num, api, toast, copyCmd, onLeave, intro, explain, act, dot, ago } = h;
  const pct = (x) => (x == null ? "-" : `${(100 * x).toFixed(1)}%`);
  const s2 = (x) => (x == null ? "-" : `${Number(x).toFixed(x < 10 ? 2 : 1)} s`);
  const mb = (b) => (b == null ? "-" : `${(b / 1048576).toFixed(b < 1048576 ? 2 : 1)} MB`);
  const yes = (v, t = "pass", f = "FAIL") => `${dot(!!v)}${v ? t : f}`;
  const role = (s) => `<span class="role r-${esc(s || "?")}">${esc(s || "down")}</span>`;
  const fileBtn = (name, label) => `<a class="btn small" href="/api/lab14/file/${name}" target="_blank" rel="noopener">${esc(label)}</a>`;
  const notRun = (what = "") => `<div class="panel empty"><strong>No results yet</strong>${what} Press the button above, start Lab 14 in the
    <a href="#/labs">Lab control centre</a>, or run <span class="mono">docker compose exec lab14 lab14 all</span>.</div>`;
  const noProd = (r) => `<div class="banner err" style="margin:18px 0"><div><strong>Production replica set not available.</strong> ${esc(r || "")}
    <div style="margin-top:6px">Lab 14 watches and backs up the Lab 13 production set. Start Labs 7 to 14 with:</div>${copyCmd("docker compose up -d lab14")}</div></div>`;
  const logBox = (lines) => (lines && lines.length ? `<details class="l14-log"><summary>Output (${lines.length} lines)</summary><pre class="term" style="max-height:300px">${esc(lines.join("\n"))}</pre></details>` : "");

  // ------------------------------------------------------------------ charts (SVG, theme colours)
  function lineChart(series, { W = 640, H = 180, yMax = null, threshold = null, thLabel = "", fmt = (v) => v, xFmt = (t) => t, below = false } = {}) {
    const pts = series.flatMap((s) => s.points);
    if (!pts.length) return `<p class="muted" style="margin:0">No data yet.</p>`;
    const L = 46, B = 22, T = 10, R = 8, ph = H - B - T;
    const x0 = Math.min(...pts.map((p) => p[0])), x1 = Math.max(...pts.map((p) => p[0]), x0 + 1);
    const top = yMax ?? (Math.max(threshold || 0, ...pts.map((p) => p[1] || 0)) * 1.15 || 1);
    const X = (t) => L + ((W - L - R) * (t - x0)) / (x1 - x0), Y = (v) => T + ph * (1 - Math.min(v, top) / top);
    let g = "";
    [0, 0.5, 1].forEach((f) => { g += `<line x1="${L}" x2="${W - R}" y1="${Y(top * f)}" y2="${Y(top * f)}" class="grid"/><text x="${L - 6}" y="${Y(top * f) + 4}" text-anchor="end" class="ax">${fmt(top * f)}</text>`; });
    if (threshold != null) {
      const y = Y(threshold);
      g += below ? `<rect x="${L}" y="${y}" width="${W - L - R}" height="${T + ph - y}" style="fill:var(--mal);opacity:.07"/>` : `<rect x="${L}" y="${T}" width="${W - L - R}" height="${y - T}" style="fill:var(--mal);opacity:.07"/>`;
      g += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" style="stroke:var(--mal);stroke-dasharray:5 4"/><text x="${W - R}" y="${y - 4}" text-anchor="end" class="ax" style="fill:var(--mal)">${esc(thLabel)}</text>`;
    }
    series.forEach((s) => {
      const p = s.points.filter((q) => q[1] != null);
      if (!p.length) return;
      g += `<polyline points="${p.map(([t, v]) => `${X(t).toFixed(1)},${Y(v).toFixed(1)}`).join(" ")}" fill="none" style="stroke:${s.color};stroke-width:2"/>`;
      p.forEach(([t, v]) => { g += `<circle cx="${X(t).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="2.4" style="fill:${s.color}"><title>${esc(s.name)}: ${fmt(v)} at ${xFmt(t)}</title></circle>`; });
    });
    g += `<text x="${L}" y="${H - 5}" class="ax">${esc(xFmt(x0))}</text><text x="${W - R}" y="${H - 5}" text-anchor="end" class="ax">${esc(xFmt(x1))}</text>`;
    const lg = series.length > 1 ? `<div class="lg">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : "";
    return `<svg viewBox="0 0 ${W} ${H}" class="lchart" role="img">${g}</svg>${lg}`;
  }
  const COLORS = ["var(--accent)", "var(--low)", "var(--ok)", "var(--high)", "var(--med)"];
  const tclock = (t) => new Date(t).toLocaleTimeString("en-IN", { hour12: false, hour: "2-digit", minute: "2-digit" });

  function hbars(items, { unit = "", fmt = (v) => v } = {}) {
    const W = 560, L = 170, R = 70, rowH = 30, mx = Math.max(...items.map((i) => i.value), 1e-9);
    const rows = items.map((it, i) => { const bw = ((W - L - R) * it.value) / mx, y = i * rowH + 4;
      return `<text x="${L - 8}" y="${y + 16}" text-anchor="end" class="ax">${esc(it.label)}</text><rect x="${L}" y="${y + 4}" width="${Math.max(bw, 2).toFixed(1)}" height="${rowH - 12}" rx="3" style="fill:${it.color || "var(--accent)"}"/><text x="${L + bw + 6}" y="${y + 16}" class="ax val">${fmt(it.value)}${unit}</text>`; }).join("");
    return `<svg viewBox="0 0 ${W} ${items.length * rowH + 8}" class="lchart" role="img">${rows}</svg>`;
  }

  // ================================================================== page
  const TABS = [["dash", "Dashboard (14.1)"], ["alerts", "Alerts + webhook (activity)"], ["backups", "Backups + schedule (14.2)"],
    ["verify", "Restore verification"], ["pitr", "Point-in-time restore"], ["bench", "Benchmark, RPO + RTO"],
    ["dr", "Billing recovery (14 on 13)"], ["users", "Least privilege"], ["scripts", "Scripts + commands"]];

  async function pageLab14(page, route) {
    const tab = route.params.get("tab") || "dash";
    const [st, saved] = await Promise.all([api("/api/lab14/status").catch((e) => ({ available: false, reason: e.message })),
      api("/api/lab14/results").catch(() => ({}))]);
    const d = st.daemon || {};
    page.innerHTML = `${intro("Operations lab 14",
      `Lab 14 runs the database the way an operations team does: a <b>monitoring dashboard</b> of every member of the Lab 13 production set,
       <b>threshold alerts</b> sent to a Slack / Discord webhook, <b>hourly backups</b> with a 7-day retention policy, <b>verified restores</b>,
       <b>point-in-time recovery</b> from the oplog, and measured <b>RPO / RTO</b>. Following the mentor's rule it is applied to
       <b>Lab 13 and Lab 10</b>: the classifier's billing ledger is attacked and recovered to the second before the attack.`,
      `<div class="row-gap"><a class="btn small" href="/api/lab14/report" target="_blank" rel="noopener">Download PDF report</a></div>`)}
      ${st.available ? `<section class="panel" style="margin-top:18px"><div class="stat-line" style="border-top:none">
        ${((st.rs || {}).members || []).map((m) => `<div><b>${role(m.state)}</b><span>${esc(m.name)}</span></div>`).join("")}
        <div><b>${dot(d.alive)}${d.alive ? "running" : "stopped"}</b><span>ops daemon ${d.samples != null ? `(${num(d.samples)} samples)` : ""}</span></div>
        <div><b>${(st.alerts_firing || []).length ? `<span class="dot err"></span>${(st.alerts_firing || []).length} firing` : `${dot(true)}none`}</b><span>alerts</span></div></div></section>`
        : noProd(st.reason)}
      <nav class="tabs" role="tablist">${TABS.map(([k, t]) => `<button role="tab" data-tab="${k}" class="${tab === k ? "active" : ""}" aria-selected="${tab === k}">${t}</button>`).join("")}</nav>
      <div id="tabbody"></div>`;
    $$("[data-tab]").forEach((b) => b.addEventListener("click", () => { location.hash = `#/lab14?tab=${b.dataset.tab}`; }));
    ({ dash: dashTab, alerts: alertsTab, backups: backupsTab, verify: verifyTab, pitr: pitrTab, bench: benchTab, dr: drTab,
       users: usersTab, scripts: scriptsTab }[tab] || dashTab)($("#tabbody"), st, saved || {});
  }

  // ------------------------------------------------------------------ 14.1 dashboard
  function kpi(label, value, sub, bad) {
    return `<div class="l14-kpi ${bad ? "bad" : ""}"><span>${label}</span><b>${value}</b><em>${sub}</em></div>`;
  }

  function dashTab(box, st) {
    const th = st.thresholds || { cache_min: 0.8, lag_max: 10 };
    box.innerHTML = `<div class="l14-kpis" id="kpis">${kpi("Connections", "-", "all members")}${kpi("Operations / s", "-", "client + replicated")}
        ${kpi("Lowest cache hit ratio", "-", `alert below ${pct(th.cache_min)}`)}${kpi("Highest replication lag", "-", `alert above ${th.lag_max} s`)}</div>
      <div id="alertbar"></div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Every member, live</h2><span class="muted" id="liveat">refreshes every 5 s</span></div>
        <div style="overflow-x:auto" id="livetbl"><div class="panel-pad"><span class="spin"></span></div></div></section>
      <div class="grid cols-2" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr));margin-top:18px">
        <section class="panel"><div class="panel-head"><h2>Operations per second</h2><span class="muted">ops daemon, every 30 s</span></div><div class="panel-pad" id="ch-ops"></div></section>
        <section class="panel"><div class="panel-head"><h2>Cache hit ratio (per interval)</h2><span class="muted">threshold ${pct(th.cache_min)}</span></div><div class="panel-pad" id="ch-hit"></div></section>
        <section class="panel"><div class="panel-head"><h2>Replication lag</h2><span class="muted">threshold ${th.lag_max} s</span></div><div class="panel-pad" id="ch-lag"></div></section>
        <section class="panel"><div class="panel-head"><h2>Connections</h2><span class="muted">current, per member</span></div><div class="panel-pad" id="ch-con"></div></section></div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>results/metrics.log</h2><span class="muted">the table the script writes every 30 s</span></div>
        <pre class="term" id="mlog" style="max-height:340px;white-space:pre;overflow:auto"></pre></section>
      ${explain("How the numbers are computed", `<ul class="plain">
        <li><b>Connections, opcounters, cache</b>: <span class="mono">serverStatus</span> of <i>each</i> member over a direct connection, as the least-privilege user <span class="mono">monitor_app</span> (clusterMonitor). The PRIMARY alone hides a cold or lagging secondary.</li>
        <li><b>Cache hit ratio</b> = 1 &minus; <span class="mono">pages read into cache</span> &divide; <span class="mono">pages requested from the cache</span>, over the last interval. MongoDB has no "bytes read from cache" counter (the solution text's formula) - checked on 7.0.34.</li>
        <li><b>Replication lag</b> = optimeDate of the PRIMARY &minus; optimeDate of the member (<span class="mono">replSetGetStatus</span>).</li>
        <li>A SECONDARY counts the writes it applies in <span class="mono">opcountersRepl</span>, not <span class="mono">opcounters</span>: the dashboard shows both.</li></ul>`)}`;
    let alive = true, t1, t2;
    onLeave(() => { alive = false; clearTimeout(t1); clearTimeout(t2); });
    async function live() {
      try {
        const s = await api("/api/lab14/live");
        if (!alive) return;
        const rows = s.rows || [];
        const ops = rows.reduce((a, r) => a + (r.ops_s || 0) + (r.repl_ops_s || 0), 0);
        const hits = rows.map((r) => r.hit_ratio).filter((x) => x != null);
        const minHit = hits.length ? Math.min(...hits) : null, maxLag = Math.max(0, ...rows.map((r) => r.lag_s || 0));
        $("#kpis").innerHTML = kpi("Connections", num(s.connections_total), `${rows.length} members`) +
          kpi("Operations / s", s.interval_s ? ops.toFixed(1) : "-", s.interval_s ? `over ${s.interval_s} s` : "next refresh") +
          kpi("Lowest cache hit ratio", pct(minHit), `alert below ${pct(th.cache_min)}`, minHit != null && minHit < th.cache_min) +
          kpi("Highest replication lag", `${maxLag.toFixed(1)} s`, `alert above ${th.lag_max} s`, maxLag > th.lag_max);
        $("#alertbar").innerHTML = (s.alerts || []).length ? `<div class="banner err" style="margin-top:14px"><div><strong>WARNING</strong>${s.alerts.map(esc).join("<br>")}</div></div>` : "";
        $("#livetbl").innerHTML = `<table class="table"><thead><tr><th>Member</th><th>State</th><th class="num">Conns</th><th class="num">ins/s</th><th class="num">qry/s</th><th class="num">upd/s</th><th class="num">del/s</th><th class="num">cmd/s</th><th class="num">repl/s</th><th>Cache used</th><th class="num">Hit ratio</th><th class="num">Since start</th><th class="num">Lag</th></tr></thead><tbody>
          ${rows.map((r) => { const o = r.ops || {}; const badH = r.hit_ratio != null && r.hit_ratio < th.cache_min, badL = (r.lag_s || 0) > th.lag_max;
            return `<tr><td class="mono small">${esc(r.member)}</td><td>${role(r.state)}</td><td class="num">${num(r.connections)}</td>
            ${["insert", "query", "update", "delete", "command"].map((k) => `<td class="num">${o[k] ?? "-"}</td>`).join("")}<td class="num">${r.repl_ops_s ?? "-"}</td>
            <td><div class="l14-meter"><i style="width:${r.cache_used_pct || 0}%"></i></div><span class="small muted">${r.cache_mb ?? "-"} MB (${r.cache_used_pct ?? "-"}%)</span></td>
            <td class="num ${badH ? "l14-bad" : ""}">${pct(r.hit_ratio)}</td><td class="num">${pct(r.hit_ratio_total)}</td><td class="num ${badL ? "l14-bad" : ""}">${r.lag_s == null ? "-" : r.lag_s.toFixed(1) + " s"}</td></tr>`; }).join("")}</tbody></table>`;
        $("#liveat").textContent = `${new Date().toLocaleTimeString("en-IN", { hour12: false })} - refreshes every 5 s`;
      } catch (e) { if (alive) $("#livetbl").innerHTML = `<div class="panel-pad muted">${esc(e.message)}</div>`; }
      if (alive) t1 = setTimeout(live, 5000);
    }
    async function hist() {
      try {
        const [hs, lg] = await Promise.all([api("/api/lab14/history?minutes=60"), fetch("/api/lab14/metrics-log?lines=60").then((r) => r.text())]);
        if (!alive) return;
        const smp = (hs.samples || []).filter((x) => x.source === "daemon");
        const members = [...new Set(smp.flatMap((x) => x.rows.map((r) => r.member)))];
        const per = (f) => members.map((m, i) => ({ name: m, color: COLORS[i % COLORS.length], points: smp.map((x) => { const r = x.rows.find((y) => y.member === m); return [new Date(x.at).getTime(), r ? f(r) : null]; }) }));
        $("#ch-ops").innerHTML = lineChart(per((r) => (r.ops_s == null ? null : r.ops_s + (r.repl_ops_s || 0))), { xFmt: tclock, fmt: (v) => v.toFixed(0) });
        $("#ch-hit").innerHTML = lineChart(per((r) => (r.hit_ratio == null ? null : 100 * r.hit_ratio)), { yMax: 100, threshold: 100 * th.cache_min, thLabel: "80 %", below: true, xFmt: tclock, fmt: (v) => v.toFixed(0) + "%" });
        $("#ch-lag").innerHTML = lineChart(per((r) => r.lag_s), { threshold: th.lag_max, thLabel: `${th.lag_max} s`, xFmt: tclock, fmt: (v) => v.toFixed(0) + "s" });
        $("#ch-con").innerHTML = lineChart(per((r) => r.connections), { xFmt: tclock, fmt: (v) => v.toFixed(0) });
        const m = $("#mlog"); m.textContent = lg || "metrics.log is empty - the ops daemon writes it every 30 s once Lab 14 is started."; m.scrollTop = m.scrollHeight;
      } catch (e) { /* ignore */ }
      if (alive) t2 = setTimeout(hist, 30000);
    }
    live(); hist();
  }

  // ------------------------------------------------------------------ activity: alerts + webhook + drills
  function msgCard(m) {
    const p = m.payload || {};
    const when = new Date(m.at).toLocaleTimeString("en-IN", { hour12: false });
    if (m.channel === "discord") {
      const e = (p.embeds || [])[0] || {};
      const col = "#" + Number(e.color || 0).toString(16).padStart(6, "0");
      return `<div class="l14-msg discord"><div class="who"><b>${esc(p.username || "webhook")}</b><span class="tag">BOT</span><em>${when}</em></div>
        <div>${esc(p.content || "").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")}</div>
        <div class="embed" style="border-left-color:${col}"><b>${esc(e.title || "")}</b><div class="fields">${(e.fields || []).map((f) => `<div><span>${esc(f.name)}</span>${esc(f.value)}</div>`).join("")}</div></div></div>`;
    }
    const hdr = ((p.blocks || []).find((b) => b.type === "header") || {}).text;
    const fields = ((p.blocks || []).find((b) => b.type === "section") || {}).fields || [];
    const icon = /RESOLVED/.test(p.text || "") ? "&#9989;" : /STILL/.test(p.text || "") ? "&#9888;&#65039;" : "&#128680;";
    return `<div class="l14-msg slack"><div class="who"><b>Kavach monitor</b><span class="tag">APP</span><em>${when}</em></div>
      <div class="hdr">${icon} ${esc(hdr ? hdr.text : p.text || "")}</div>
      <div class="fields">${fields.map((f) => { const [k, ...v] = (f.text || "").split("\n"); return `<div><span>${esc(k.replace(/\*/g, ""))}</span>${esc(v.join(" "))}</div>`; }).join("")}</div></div>`;
  }

  function lagChart(r) {
    if (!r || !r.series) return "";
    return lineChart([{ name: `${r.target} (locked)`, color: "var(--mal)", points: r.series.map((s) => [s.t, s.lag[r.target]]) },
      ...Object.keys(r.series[0].lag).filter((k) => k !== r.target).map((k, i) => ({ name: k, color: COLORS[i + 1], points: r.series.map((s) => [s.t, s.lag[k]]) }))],
    { threshold: r.threshold_s, thLabel: `${r.threshold_s} s threshold`, fmt: (v) => v.toFixed(0) + " s", xFmt: (t) => `${t.toFixed(0)} s` });
  }

  function lagCard(r) {
    if (!r) return `<p class="muted" style="margin:0">Not run yet.</p>`;
    if (r.error) return `<p class="muted">${esc(r.error)}</p>`;
    return `${lagChart(r)}<div class="stat-line"><div><b>${r.max_lag_s.toFixed(0)} s</b><span>max lag on ${esc(r.target)}</span></div>
      <div><b>${num(r.writes_ok)}</b><span>writes acknowledged (w: majority)</span></div><div><b>${num(r.writes_err)}</b><span>write errors</span></div>
      <div><b>${yes(r.fired, "fired")}</b><span>WARNING + webhook</span></div><div><b>${yes(r.resolved, "resolved")}</b><span>after fsyncUnlock</span></div></div>
      <p class="muted small" style="margin:8px 0 0">Sync sources: ${Object.entries(r.sync_sources || {}).map(([k, v]) => `<span class="mono">${esc(k)} &larr; ${esc(v)}</span>`).join(", ")}. The locked member is one nobody replicates from: locking a sync source froze BOTH secondaries in testing and w: majority writes stopped.</p>`;
  }

  function cacheCard(r) {
    if (!r) return `<p class="muted" style="margin:0">Not run yet.</p>`;
    if (r.error) return `<p class="muted">${esc(r.error)}</p>`;
    return hbars((r.scans || []).map((s, i) => ({ label: i ? "warm read" : "cold read (after restart)", value: 100 * (s.hit_ratio || 0), color: (s.hit_ratio || 0) < r.threshold ? "var(--mal)" : "var(--ok)" })), { unit: "%", fmt: (v) => v.toFixed(1) }) +
      `<table class="table" style="margin-top:8px"><thead><tr><th>Read of ${num((r.scans[0] || {}).docs)} docs from ${esc(r.target)}</th><th class="num">Time</th><th class="num">Pages requested</th><th class="num">Read into cache</th><th class="num">Hit ratio</th></tr></thead><tbody>
      ${(r.scans || []).map((s) => `<tr><td>${esc(s.label)}</td><td class="num">${s2(s.seconds)}</td><td class="num">${num(s.pages_requested)}</td><td class="num">${num(s.pages_read_into)}</td><td class="num">${pct(s.hit_ratio)}</td></tr>`).join("")}</tbody></table>
      <p class="muted small" style="margin:8px 0 0">${esc(r.target)} was restarted (${r.restart_s} s), so its WiredTiger cache was empty. WARNING fired: ${r.fired ? "yes" : "no"}, resolved by the warm read: ${r.resolved ? "yes" : "no"}.</p>`;
  }

  async function alertsTab(box, st, saved) {
    const th = st.thresholds || {};
    box.innerHTML = `<section class="panel panel-pad"><div class="row-gap" style="justify-content:space-between;flex-wrap:wrap"><p style="margin:0;max-width:640px">
        <b>Activity:</b> when a threshold is breached the monitor posts to a webhook with <span class="mono">requests.post(webhook_url, json=alert_payload)</span> inside try/except.
        Below is the built-in channel <b>#mongodb-alerts</b> (Slack and Discord formats). ${(st.external_webhooks || []).length ? `Also sending to: <span class="mono">${st.external_webhooks.map(esc).join(", ")}</span>` : "Add a real Slack/Discord URL with <span class=\"mono\">LAB14_WEBHOOK_URL</span> in .env."}</p>
        <div class="row-gap"><button class="btn small" id="b-test">Send a test alert</button><button class="btn small primary" id="b-lag">Replication-lag drill (~45 s)</button><button class="btn small primary" id="b-cache">Cold-cache drill (~40 s)</button></div></div></section>
      <div class="grid cols-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);margin-top:18px">
        <section class="panel"><div class="panel-head"><h2># mongodb-alerts</h2><span class="muted" id="inb-n"></span></div><div class="panel-pad l14-chan" id="inbox"><span class="spin"></span></div></section>
        <div style="display:grid;gap:18px;align-content:start">
          <section class="panel"><div class="panel-head"><h2>Drill 1 - replication lag &gt; ${th.lag_max ?? 10} s</h2><span class="muted">fsyncLock on a secondary</span></div><div class="panel-pad" id="d-lag">${lagCard(saved.lag_drill)}</div></section>
          <section class="panel"><div class="panel-head"><h2>Drill 2 - cache hit ratio &lt; ${pct(th.cache_min ?? 0.8)}</h2><span class="muted">restart + cold read</span></div><div class="panel-pad" id="d-cache">${cacheCard(saved.cache_drill)}</div></section></div></div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Alert history</h2><span class="muted">FIRING once, RESOLVED once - no repeat every 30 s</span></div><div style="overflow-x:auto" id="ahist"></div></section>
      <div id="dlog"></div>
      ${explain("Why only changes are posted", `<p style="margin:0">The log gets a WARNING line every 30 s while a threshold is breached - that is what the lab asks. A chat channel gets <b>FIRING</b> once,
        a reminder every 10 minutes if it lasts, and <b>RESOLVED</b> when it is over. Posting every 30 s would bury the channel (alert fatigue). Webhook URLs contain the secret token, so they are redacted in every log.</p>`)}`;
    async function loadInbox() {
      const [ib, ah] = await Promise.all([api("/api/lab14/inbox"), api("/api/lab14/alerts")]);
      $("#inbox").innerHTML = ib.length ? ib.map(msgCard).join("") : `<p class="muted" style="margin:0">No message yet - send a test alert or run a drill.</p>`;
      $("#inb-n").textContent = `${ib.length} messages`;
      $("#ahist").innerHTML = ah.length ? `<table class="table"><thead><tr><th>Time</th><th>Status</th><th>Alert</th><th>Source</th><th>Webhooks</th></tr></thead><tbody>${ah.map((a) => `<tr><td class="small">${new Date(a.at).toLocaleString("en-IN", { hour12: false })}</td><td><span class="pill ${a.status === "RESOLVED" ? "st-ok" : "st-failed"}">${esc(a.status)}</span></td><td class="small">${esc(a.line)}</td><td class="mono small">${esc(a.source)}</td><td class="small">${(a.webhooks || []).map((w) => `${dot(w.ok)}${w.ok ? "HTTP " + w.http : "failed"}`).join(" ")}</td></tr>`).join("")}</tbody></table>` : `<div class="panel-pad muted">No alerts yet.</div>`;
    }
    loadInbox().catch((e) => { $("#inbox").innerHTML = esc(e.message); });
    $("#b-test").addEventListener("click", (e) => act(e.currentTarget, "Sending", async () => {
      const r = await api("/api/lab14/webhook-test", { method: "POST", body: {} });
      toast(r.sent.map((s) => `${s.ok ? "&#10003;" : "&#10007;"} ${esc(s.url.split("/").pop())}`).join(" &nbsp; ") + " (the unreachable one is on purpose)");
      await loadInbox();
    }));
    $("#b-lag").addEventListener("click", (e) => act(e.currentTarget, "Secondary locked, writing", async () => {
      const r = await api("/api/lab14/drill/lag", { method: "POST", body: {} }); $("#d-lag").innerHTML = lagCard(r); $("#dlog").innerHTML = logBox(r.log); await loadInbox();
      toast(`Lag reached ${r.max_lag_s.toFixed(0)} s - WARNING ${r.fired ? "fired" : "not fired"}, ${r.resolved ? "resolved" : "not resolved"}`);
    }));
    $("#b-cache").addEventListener("click", (e) => act(e.currentTarget, "Restarting a secondary", async () => {
      const r = await api("/api/lab14/drill/cache", { method: "POST", body: {} }); $("#d-cache").innerHTML = cacheCard(r); $("#dlog").innerHTML = logBox(r.log); await loadInbox();
      toast(`Cold read hit ratio ${pct((r.scans[0] || {}).hit_ratio)} - warm ${pct((r.scans[1] || {}).hit_ratio)}`);
    }));
  }

  // ------------------------------------------------------------------ 14.2 backups + schedule + retention
  function retentionView(r) {
    if (!r || !r.before) return notRun("");
    const W = 640, L = 20, R = 20, maxH = Math.max(200, ...r.before.map((b) => (Date.now() - new Date(b.created_at)) / 3.6e6));
    const X = (hh) => W - R - ((W - L - R) * hh) / maxH;
    const del = new Set(r.deleted.map((x) => x.name));
    let g = `<line x1="${L}" x2="${W - R}" y1="50" y2="50" class="grid"/><line x1="${X(168)}" x2="${X(168)}" y1="12" y2="80" style="stroke:var(--mal);stroke-dasharray:5 4"/><text x="${X(168) + 4}" y="20" class="ax" style="fill:var(--mal)">7 days</text><text x="${W - R}" y="96" text-anchor="end" class="ax">now</text><text x="${L}" y="96" class="ax">${Math.round(maxH / 24)} days ago</text>`;
    r.before.forEach((b) => { const hh = (new Date(r.limit) - new Date(b.created_at)) / 3.6e6 + 168; const x = X(Math.max(hh, 0));
      g += `<circle cx="${x.toFixed(1)}" cy="50" r="7" style="fill:${del.has(b.name) ? "var(--mal)" : "var(--ok)"}"><title>${esc(b.name)} (${hh.toFixed(0)} h old) ${del.has(b.name) ? "deleted" : "kept"}</title></circle>`; });
    return `<svg viewBox="0 0 ${W} 100" class="lchart" role="img">${g}</svg>
      <div class="lg"><span><i style="background:var(--mal)"></i>deleted by shutil.rmtree (${r.deleted.length})</span><span><i style="background:var(--ok)"></i>kept (${r.kept.length})</span></div>
      <table class="table" style="margin-top:8px"><thead><tr><th>Backup folder (sorted by creation time)</th><th class="num">Age</th><th>Result</th></tr></thead><tbody>
      ${[...r.deleted.map((x) => [x, false]), ...r.kept.map((x) => [x, true])].map(([x, k]) => `<tr><td class="mono small">${esc(x.name)}</td><td class="num">${x.age_h} h</td><td>${k ? `${dot(true)}kept` : `<span class="dot err"></span>deleted`}</td></tr>`).join("")}</tbody></table>`;
  }

  async function backupsTab(box, st, saved) {
    box.innerHTML = `<div class="grid cols-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1.3fr)">
      <section class="panel panel-pad" id="sched"><span class="spin"></span></section>
      <section class="panel"><div class="panel-head"><h2>Backup now</h2><span class="muted">mongodump --gzip into a timestamped folder</span></div><div class="panel-pad">
        <div class="row-gap" style="flex-wrap:wrap"><button class="btn small primary" data-bk="billing">Billing ledger (prod rs0)</button><button class="btn small" data-bk="kavach">Kavach platform (rs0)</button><button class="btn small" data-bk="prod-full">Whole prod set with --oplog</button></div>
        <div id="bkout" style="margin-top:12px"></div></div></section></div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Backup folders</h2><span class="muted" id="bkdir"></span></div><div style="overflow-x:auto" id="folders"></div></section>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>7-day retention policy</h2><button class="btn small" id="b-ret">Run on simulated history</button></div><div class="panel-pad" id="ret">${retentionView(saved.retention)}</div></section>
      ${explain("The solution, line by line", `<ul class="plain"><li><span class="mono">subprocess.run(['mongodump', '--uri', uri, '--out', output_dir])</span> - the return code is captured and written to <span class="mono">manifest.json</span> in every backup folder. The URI goes in a mode-600 <span class="mono">--config</span> file, so the password never shows in <span class="mono">ps</span>.</li>
        <li><span class="mono">schedule.every().hour.do(run_backup)</span> runs in the ops daemon of the lab14 container (with <span class="mono">schedule.every(30).seconds</span> for the monitor).</li>
        <li>Retention scans the folder, <b>sorts by creation time</b> (manifest, else mtime) and removes anything older than 7 days with <span class="mono">shutil.rmtree()</span>. Waiting 8 days is not possible in a lab, so placeholders dated in the past show it.</li></ul>`)}`;
    async function load() {
      const b = await api("/api/lab14/backups");
      const d = b.daemon || {};
      $("#sched").innerHTML = `<h2 style="margin-bottom:8px">Scheduler (ops daemon)</h2>
        <div class="kvr"><span>Daemon</span><b>${dot(d.alive)}${d.alive ? "running" : "not running"}</b></div>
        <div class="kvr"><span>Backup</span><b>every ${d.backup_every_min || 60} min</b></div>
        <div class="kvr"><span>Next backup</span><b>${d.next_backup && d.next_backup !== "None" ? esc(String(d.next_backup).slice(11, 19)) : "-"}</b></div>
        <div class="kvr"><span>Last backup</span><b>${d.last_backup ? `${dot(d.last_backup.ok)}${esc(d.last_backup.target)} ${ago(d.last_backup.at)}` : "-"}</b></div>
        <div class="kvr"><span>Targets</span><b>${esc((b.targets || []).join(", "))}</b></div>
        <div class="kvr"><span>Retention</span><b>${d.retention_days || 7} days</b></div>
        <div class="kvr"><span>Monitor samples</span><b>${num(d.samples || 0)} (every ${d.interval_s || 30} s)</b></div>`;
      $("#bkdir").textContent = b.backup_dir;
      const rows = Object.entries(b.folders || {}).flatMap(([t, l]) => l.map((x) => ({ t, ...x }))).filter((x) => !x.simulated).sort((a, c) => (a.created_at < c.created_at ? 1 : -1)).slice(0, 25);
      $("#folders").innerHTML = rows.length ? `<table class="table"><thead><tr><th>Target</th><th>Folder</th><th>Created</th><th class="num">Size</th><th class="num">mongodump</th><th>rc</th></tr></thead><tbody>${rows.map((x) => `<tr><td>${esc(x.t)}</td><td class="mono small">${esc(x.name)}</td><td class="small">${ago(x.created_at)}</td><td class="num">${mb(x.bytes)}</td><td class="num">${s2(x.seconds)}</td><td>${x.ok == null ? "-" : yes(x.ok, "0", "error")}</td></tr>`).join("")}</tbody></table>` : `<div class="panel-pad muted">No backups yet.</div>`;
    }
    load().catch((e) => { $("#sched").innerHTML = esc(e.message); });
    $$("[data-bk]").forEach((btn) => btn.addEventListener("click", (e) => act(e.currentTarget, "mongodump", async () => {
      const r = await api("/api/lab14/backup", { method: "POST", body: { target: btn.dataset.bk } });
      $("#bkout").innerHTML = `<div class="kvr"><span>${esc(r.name)}</span><b>${yes(r.ok, `rc 0, ${s2(r.seconds)}, ${mb(r.bytes)}`, "rc " + r.rc)}</b></div>${logBox(r.log)}`; await load();
    })));
    $("#b-ret").addEventListener("click", (e) => act(e.currentTarget, "Applying retention", async () => { $("#ret").innerHTML = retentionView(await api("/api/lab14/retention", { method: "POST" })); }));
  }

  // ------------------------------------------------------------------ restore verification
  function verifyView(v) {
    if (!v || v.error) return v && v.error ? `<p class="muted">${esc(v.error)}</p>` : notRun("");
    const step = (n, t, d, cls = "") => `<div class="l14-step ${cls}"><b>${n}</b><div><strong>${t}</strong><span>${d}</span></div></div>`;
    return `<div class="l14-steps">
      ${step(1, `Insert ${num(v.docs)} documents`, `<span class="mono">${esc(v.ns)}</span> with timestamps. sha256 <span class="mono">${esc(v.sha256_before.slice(0, 16))}</span>`)}
      ${step(2, "mongodump", `${esc(v.backup.name)}<br>rc ${v.backup.rc}, ${s2(v.backup.seconds)}`)}
      ${step(3, "Modify the data", `verified: false on ${num(v.modified)} docs, ${v.deleted_after_backup} deleted. Checker finds <b>${num(v.mismatches_before_restore)}</b> mismatches - it can fail.`, "warn")}
      ${step(4, "mongorestore --drop", `--nsInclude=${esc(v.ns)}<br>rc ${v.restore_rc}, ${s2(v.restore_seconds)}`)}
      ${step(5, v.pass ? "Original data recovered" : "MISMATCH - alert sent", `${num(v.mismatches_after_restore)} mismatches, sha256 <span class="mono">${esc(v.sha256_after.slice(0, 16))}</span>`, v.pass ? "ok" : "err")}</div>`;
  }
  function verifyTab(box, st, saved) {
    box.innerHTML = `<section class="panel"><div class="panel-head"><h2>Backup, change, restore, prove it</h2><button class="btn small primary" id="b-ver">Run restore verification</button></div>
      <div class="panel-pad" id="ver">${verifyView(saved.verify)}</div></section><div id="vlog"></div>
      ${explain("The assertion loop", `<p style="margin:0">Every original document is kept in memory before the backup. After <span class="mono">mongorestore --drop --nsInclude=lab14.backup_test</span> the
        loop asserts that each <span class="mono">_id</span> exists and that every field equals the original; the count and a sha256 of the whole sorted collection must match too.
        Any mismatch prints an <b>ALERT</b> to stdout and is posted to the webhook. Step 3 runs the same checker on the modified data first: a verifier that has never failed proves nothing.</p>`)}`;
    $("#b-ver").addEventListener("click", (e) => act(e.currentTarget, "Verifying", async () => { const r = await api("/api/lab14/verify", { method: "POST" }); $("#ver").innerHTML = verifyView(r); $("#vlog").innerHTML = logBox(r.log); }));
  }

  // ------------------------------------------------------------------ point-in-time restore
  function pitrView(p) {
    if (!p || p.error) return p && p.error ? `<p class="muted">${esc(p.error)}</p>` : notRun("");
    const tl = p.timeline || [];
    return `<div class="l14-tl">${tl.map((e) => { const cls = /INCIDENT/.test(e.what) ? "err" : /restore/.test(e.what) ? (e.match ? "ok" : "warn") : "";
        return `<div class="l14-tle ${cls}"><em>${esc(e.at.slice(11, 19))}</em><b>${esc(e.what)}</b><span>${num(e.count)} documents${e.ts ? ` &middot; oplog ts ${esc(e.ts)}` : ""}${e.oplog_ns ? ` &middot; logged in <span class="mono">${esc(e.oplog_ns)}</span>` : ""}</span></div>`; }).join("")}</div>
      <table class="table" style="margin-top:14px"><thead><tr><th>Replay as</th><th>Base restore</th><th>oplog replay</th><th class="num">Docs</th><th>sha256</th><th>Result</th></tr></thead><tbody>
      ${(p.attempts || []).map((a) => `<tr><td>${esc(a.user)}</td><td>rc ${a.base_rc}</td><td class="small">rc ${a.replay_rc} - ${esc((a.replay_output || []).slice(-2)[0] || "").slice(0, 120)}</td><td class="num">${num(a.count)}</td><td class="mono small">${esc(a.sha256)}</td><td>${yes(a.pass, "matches", "half restored")}</td></tr>`).join("")}</tbody></table>
      <p class="muted small" style="margin:8px 0 0">Expected: ${num(p.expected_count)} documents, sha256 <span class="mono">${esc(p.expected_sha256)}</span>. The deleteMany was found in the oplog ${p.incident_in_admin_cmd ? "inside batched <span class=\"mono\">applyOps</span> entries of <span class=\"mono\">admin.$cmd</span> - a search on the collection's namespace alone misses it" : "under the collection's namespace"}.</p>`;
  }
  function pitrTab(box, st, saved) {
    box.innerHTML = `<section class="panel"><div class="panel-head"><h2>Recover to the second before a mistake</h2><button class="btn small primary" id="b-pitr">Run point-in-time restore</button></div>
      <div class="panel-pad" id="pitr">${pitrView(saved.pitr)}</div></section><div id="plog"></div>
      ${explain("How it works", `<ol class="plain"><li>T0: <span class="mono">mongodump</span> of the collection, and the last oplog timestamp is recorded.</li>
        <li>Business continues: 200 orders added, 50 marked paid - these are <b>not</b> in the backup.</li><li>The incident: <span class="mono">deleteMany({})</span>.</li>
        <li>The oplog entries since T0 for this collection are dumped (<span class="mono">local.oplog.rs</span>, including <span class="mono">o.applyOps.ns</span>).</li>
        <li><span class="mono">mongorestore --drop</span> the base, then <span class="mono">mongorestore --oplogReplay --oplogLimit=&lt;incident ts&gt;</span> replays everything <i>before</i> the incident.</li></ol>
        <p style="margin:6px 0 0">Measured: as <span class="mono">backup_app</span> (roles backup + restore) the replay applies the inserts and is refused at the first update - the collection is left half restored and only the checksum shows it. Replaying needs a user that may run <span class="mono">applyOps</span>; here that is <span class="mono">admin</span>, used for this one step only.</p>`)}`;
    $("#b-pitr").addEventListener("click", (e) => act(e.currentTarget, "Restoring", async () => { const r = await api("/api/lab14/pitr", { method: "POST" }); $("#pitr").innerHTML = pitrView(r); $("#plog").innerHTML = logBox(r.log); }));
  }

  // ------------------------------------------------------------------ benchmark + RPO/RTO
  function benchView(b) {
    if (!b || !b.rows) return b && b.error ? `<p class="muted">${esc(b.error)}</p>` : notRun("");
    const r = b.rpo_rto || {};
    const g = b.rows.flatMap((x) => [{ label: `${num(x.docs)} dump`, value: x.dump_s, color: "var(--accent)" }, { label: `${num(x.docs)} dump --gzip`, value: x.gzip_s || 0, color: "var(--med)" }, { label: `${num(x.docs)} restore`, value: x.restore_s, color: "var(--low)" }]);
    return `<div class="l14-kpis">${kpi("RPO", `${Math.round(r.rpo_s / 60)} min`, `worst case ${Math.round(r.rpo_worst_s / 60)} min ${Math.round(r.rpo_worst_s % 60)} s`)}${kpi("RTO", `${r.rto_s} s`, `restore ${r.restore_s} s + app restart ${r.app_restart_s} s`)}
      ${kpi("Restore speed", `${num(b.rows[b.rows.length - 1].restore_docs_s)}/s`, `documents, ${num(r.for_docs)} docs`)}${kpi("Dump speed", `${b.rows[b.rows.length - 1].dump_mb_s} MB/s`, "uncompressed mongodump")}</div>
      <div style="overflow-x:auto;margin-top:14px"><table class="table"><thead><tr><th class="num">Documents</th><th class="num">Data</th><th class="num">mongodump</th><th class="num">Dump size</th><th class="num">--gzip</th><th class="num">gzip size</th><th class="num">mongorestore</th><th>Restored</th></tr></thead><tbody>
      ${b.rows.map((x) => `<tr><td class="num">${num(x.docs)}</td><td class="num">${x.data_mb} MB</td><td class="num">${s2(x.dump_s)}</td><td class="num">${x.dump_mb} MB</td><td class="num">${s2(x.gzip_s)}</td><td class="num">${x.gzip_mb ?? "-"} MB</td><td class="num">${s2(x.restore_s)}</td><td>${yes(x.verified, num(x.restored))}</td></tr>`).join("")}</tbody></table></div>
      <div style="margin-top:12px">${hbars(g, { unit: " s", fmt: (v) => v.toFixed(2) })}</div><p style="margin:10px 0 0">${esc(r.explain || "")}</p>`;
  }
  function benchTab(box, st, saved) {
    box.innerHTML = `<section class="panel"><div class="panel-head"><h2>Backup and restore time at 1,000 / 10,000 / 100,000 documents (~1 KB each)</h2><button class="btn small primary" id="b-bench">Run benchmark (~1 min)</button></div>
      <div class="panel-pad" id="bench">${benchView(saved.benchmark)}</div></section><div id="blog"></div>
      ${explain("Reading the numbers", `<ul class="plain"><li><b>mongodump scales roughly linearly</b> with the data; it copies BSON as stored, so it is fast. <b>mongorestore is slower</b>: every document is inserted again, replicated to all members with w: majority, and the indexes are rebuilt.</li>
        <li><b>--gzip</b> halves the size of random data for about 3&times; the dump time - worth it for 168 hourly copies kept 7 days.</li>
        <li><b>RPO</b> (how much data can be lost) = the backup interval: 1 hour. <b>RTO</b> (how long until service is back) = restore time + application restart (measured: a new process connects and answers its first query).</li>
        <li>The point-in-time restore cuts the RPO to seconds - at the price of keeping the oplog slices.</li></ul>`)}`;
    $("#b-bench").addEventListener("click", (e) => act(e.currentTarget, "Generating 111,000 documents", async () => { const r = await api("/api/lab14/benchmark", { method: "POST", body: { full: true } }); $("#bench").innerHTML = benchView(r); $("#blog").innerHTML = logBox(r.log); }));
  }

  // ------------------------------------------------------------------ 14 on 13 / 10
  function drView(d) {
    if (!d || !d.timeline) return d && d.error ? `<p class="muted">${esc(d.error)}</p>` : notRun("");
    return `<div class="l14-dr">${d.timeline.map((s) => { const i = s.invariants || {}; const bad = !i.money_conserved || !i.revenue_matches; const att = /ATTACK/.test(s.what);
      return `<div class="l14-drc ${att ? "err" : /after point/.test(s.what) ? "ok" : ""}"><em>${esc(s.at.slice(11, 19))}</em><b>${esc(s.what)}</b>
        <div class="kvr"><span>Accounts / payments</span><b>${num(s.accounts)} / ${num(s.payments)}</b></div><div class="kvr"><span>Wallets</span><b>Rs ${num(i.wallets)}</b></div>
        <div class="kvr"><span>Revenue</span><b>Rs ${num(i.revenue)}</b></div><div class="kvr"><span>money conserved</span><b>${yes(i.money_conserved, "yes", "NO")}</b></div>
        <div class="kvr"><span>revenue = subscriptions</span><b>${yes(i.revenue_matches, "yes", "NO")}</b></div>${s.sha256 ? `<div class="kvr"><span>ledger sha256</span><b class="mono small">${esc(s.sha256)}</b></div>` : ""}${bad ? "" : ""}</div>`; }).join("")}</div>
      <div class="stat-line" style="margin-top:14px"><div><b>${s2(d.recovery_s)}</b><span>recovery (oplog dump + restore + replay)</span></div><div><b>${yes(d.digest_match, "identical")}</b><span>ledger vs the second before the attack</span></div>
      <div><b>${num(d.customers_after_backup)}</b><span>customers who paid AFTER the backup - kept</span></div><div><b>${esc((d.attack_detected_by || []).join(", ") || "none")}</b><span>invariant that caught the attack</span></div></div>`;
  }
  function drTab(box, st, saved) {
    box.innerHTML = `<section class="panel"><div class="panel-head"><h2>Lab 14 applied to Lab 13 + Lab 10: the billing ledger under attack</h2><button class="btn small primary" id="b-dr">Run the recovery drill</button></div>
      <div class="panel-pad" id="dr">${drView(saved.billing_dr)}</div></section><div id="drlog"></div>
      ${explain("What this proves", `<p style="margin:0">The classifier's billing (Lab 10, on the Lab 13 production set) is backed up at T0. Then new customers sign up and pay - money that a plain restore of
        the T0 backup would <b>lose</b>. An attacker sets every wallet to Rs 0 and deletes every payment. Recovery = base restore + oplog replay to the second before the attack, including the
        multi-document transactions (replayed as <span class="mono">applyOps</span>). Note that <b>money_conserved stays true after the attack</b> (0 top-ups &minus; 0 plans = 0 in wallets): only
        <i>revenue = subscriptions</i> exposes it. One invariant is not enough - the check also compares a sha256 of the whole ledger.</p>`)}`;
    $("#b-dr").addEventListener("click", (e) => act(e.currentTarget, "Attacking + recovering", async () => { const r = await api("/api/lab14/billing-dr", { method: "POST" }); $("#dr").innerHTML = drView(r); $("#drlog").innerHTML = logBox(r.log); }));
  }

  // ------------------------------------------------------------------ users + scripts
  function usersView(u) {
    if (!u || !u.matrix) return notRun("");
    const ops = Object.keys(u.matrix[0].ops);
    return `<div style="overflow-x:auto"><table class="table"><thead><tr><th>User</th><th>Roles</th>${ops.map((o) => `<th>${esc(o)}</th>`).join("")}</tr></thead><tbody>
      ${u.matrix.map((r) => `<tr><td class="mono small">${esc(r.user)}</td><td class="small">${esc(r.roles.join(", "))}</td>${ops.map((o) => `<td>${r.ops[o] === "allowed" ? `${dot(true)}allowed` : `<span class="dot err"></span>${esc(r.ops[o])}`}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  function usersTab(box, st, saved) {
    box.innerHTML = `<section class="panel"><div class="panel-head"><h2>Measured, not assumed: what each Lab 14 user may do</h2><button class="btn small" id="b-us">Check again</button></div>
      <div id="us">${usersView(saved.users)}</div></section>
      ${explain("Findings", `<ul class="plain"><li><b>monitor_app</b> (clusterMonitor + read on local) sees every metric and cannot read a single business document: a leaked monitoring password does not leak data.</li>
        <li><b>backup_app</b> (backup + restore) can read everything (it must) and cannot update or delete - but the built-in <b>restore role can create users</b>. Treat backup credentials like admin ones.</li>
        <li>Replaying an oplog with updates needs <span class="mono">applyOps</span> beyond the restore role (measured), so point-in-time recovery uses admin for that one step.</li></ul>`)}`;
    $("#b-us").addEventListener("click", (e) => act(e.currentTarget, "Testing", async () => { $("#us").innerHTML = usersView(await api("/api/lab14/users", { method: "POST" })); }));
  }
  function scriptsTab(box) {
    box.innerHTML = `<section class="panel panel-pad"><h2 style="margin-bottom:10px">The scripts of the lab</h2><div class="row-gap" style="flex-wrap:wrap">
        ${fileBtn("monitor", "monitor_dashboard.py (14.1 + activity)")}${fileBtn("backup", "backup_verify.py (14.2)")}${fileBtn("daemon", "ops_daemon.py (schedule)")}${fileBtn("runner", "run_lab14.py")}${fileBtn("readme", "README")}</div></section>
      <section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:10px">Commands</h2>
        ${["docker compose up -d lab14", "docker compose exec lab14 lab14 all", "docker compose exec lab14 lab14 dashboard", "docker compose exec lab14 lab14 monitor", "docker compose exec lab14 lab14 lag", "docker compose exec lab14 lab14 cache", "docker compose exec lab14 lab14 verify", "docker compose exec lab14 lab14 pitr", "docker compose exec lab14 lab14 benchmark", "docker compose exec lab14 lab14 log 60", "docker compose exec lab14 lab14 backups"].map(copyCmd).join("")}
        <p class="muted" style="margin:10px 0 0">The scripts run against any MongoDB:</p>${copyCmd('python labs/lab14/monitor_dashboard.py --uri "mongodb://localhost:27017" --interval 30 --log metrics.log --webhook https://hooks.slack.com/services/...')}
        ${copyCmd('python labs/lab14/backup_verify.py --uri "mongodb://localhost:27017" --out ./backups all')}</section>`;
  }

  return { lab14: pageLab14 };
};

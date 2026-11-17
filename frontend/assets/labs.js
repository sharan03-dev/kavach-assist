/* Kavach CTI - lab pages (Labs 8, 9, 10 + the dependency chain; Lab 11 lives in lab11.js, Lab 12 in lab12.js, Lab 13 in lab13.js, Lab 14 in lab14.js). Loaded before app.js, which calls
   window.KavachLabs(helpers) and merges the returned routes into its router. */
window.KavachLabs = (h) => {
  "use strict";
  const { $, $$, esc, num, fmtTime, ago, api, toast, copyCmd, onLeave } = h;

  // ------------------------------------------------------------------ shared bits
  const STATUS = { idle: "Not started", pending: "Waiting", running: "Running", ok: "Active", failed: "Failed", skipped: "Skipped" };
  const pill = (s) => `<span class="pill st-${esc(s || "idle")}">${esc(STATUS[s] || s)}</span>`;
  const dot = (ok) => `<span class="dot ${ok ? "ok" : "err"}"></span>`;
  const secs = (n) => (n == null ? "-" : n < 60 ? `${Number(n).toFixed(1)} s` : `${Math.floor(n / 60)} min ${Math.round(n % 60)} s`);
  const intro = (title, lede, right = "") => `<div class="lab-intro"><div><h1>${title}</h1><p class="lede">${lede}</p></div>${right}</div>`;
  const explain = (title, body) => `<section class="panel panel-pad explain"><h2>${title}</h2>${body}</section>`;
  const noRS = (reason) => `<div class="banner err" style="margin:18px 0"><div><strong>Replica set not available</strong>${esc(reason || "")}
    <div style="margin-top:6px">Labs 8, 9 and 10 need the three-member replica set. Start everything with one command:</div>${copyCmd("docker compose up -d lab10")}</div></div>`;

  function bars(items, { unit = "", w = 560, rowH = 34, fmt = (v) => v } = {}) {
    const max = Math.max(...items.map((i) => i.value), 1e-9);
    const L = 150, R = 80;
    const H = items.length * rowH + 6;
    const rows = items.map((it, i) => {
      const bw = ((w - L - R) * it.value) / max;
      const y = i * rowH + 4;
      return `<text x="${L - 10}" y="${y + 17}" text-anchor="end" class="ax">${esc(it.label)}</text>
        <rect x="${L}" y="${y + 4}" width="${Math.max(bw, 2).toFixed(1)}" height="${rowH - 14}" rx="4" style="fill:${it.color || "var(--accent)"}"><title>${esc(it.label)}: ${fmt(it.value)}${unit}</title></rect>
        <text x="${L + bw + 8}" y="${y + 17}" class="ax val">${fmt(it.value)}${unit}</text>`;
    }).join("");
    return `<svg viewBox="0 0 ${w} ${H}" class="lchart" role="img">${rows}</svg>`;
  }

  function groupedBars(rows, aKey, bKey, { aName, bName, w = 620, hgt = 230 } = {}) {
    const max = Math.max(...rows.map((r) => Math.max(r[aKey], r[bKey])), 1);
    const L = 44, B = 36, T = 14, gw = (w - L - 10) / rows.length;
    const ph = hgt - B - T;
    let g = "";
    [0, 0.25, 0.5, 0.75, 1].forEach((f) => {
      const y = T + ph * (1 - f);
      g += `<line x1="${L}" x2="${w - 6}" y1="${y}" y2="${y}" class="grid"/><text x="${L - 6}" y="${y + 4}" text-anchor="end" class="ax">${Math.round(max * f)}</text>`;
    });
    rows.forEach((r, i) => {
      const x0 = L + i * gw + gw * 0.14, bw = gw * 0.34;
      [[aKey, "var(--accent)", 0], [bKey, "var(--low)", 1]].forEach(([k, c, j]) => {
        const hh = (ph * r[k]) / max;
        g += `<rect x="${(x0 + j * (bw + 4)).toFixed(1)}" y="${(T + ph - hh).toFixed(1)}" width="${bw.toFixed(1)}" height="${hh.toFixed(1)}" rx="3" style="fill:${c}"><title>${r.label}: ${r[k]}/s</title></rect>
          <text x="${(x0 + j * (bw + 4) + bw / 2).toFixed(1)}" y="${(T + ph - hh - 4).toFixed(1)}" text-anchor="middle" class="ax">${Math.round(r[k])}</text>`;
      });
      g += `<text x="${(L + i * gw + gw / 2).toFixed(1)}" y="${hgt - 14}" text-anchor="middle" class="ax">${esc(r.label)}</text>`;
    });
    return `<svg viewBox="0 0 ${w} ${hgt}" class="lchart" role="img">${g}</svg>
      <div class="lg"><span><i style="background:var(--accent)"></i>${esc(aName)}</span><span><i style="background:var(--low)"></i>${esc(bName)}</span></div>`;
  }

  function busy(btn, label) {
    const old = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spin"></span> ${label || "Working"}`;
    return () => { btn.disabled = false; btn.innerHTML = old; };
  }
  async function act(btn, label, fn) {
    const done = busy(btn, label);
    try { return await fn(); } catch (err) { toast(esc(err.message || err), 7000); } finally { done(); }
  }

  // ================================================================== Lab control centre
  async function pageLabs(page) {
    page.innerHTML = `${intro("Lab control centre",
      `Each lab needs the one below it. Press <b>Start</b> on a lab and the website activates every lab it depends on first, in order:
       starting <span class="mono">Lab 18</span> runs Lab 7 first, then 8 to 17 and finally 18.`)}
      <div class="chain" id="chain"></div>
      <div class="grid cols-2" style="margin-top:18px;grid-template-columns:minmax(0,1.7fr) minmax(0,1fr)">
        <section class="panel"><div class="panel-head"><h2>Live output</h2><span class="muted" id="logstate"></span></div>
          <pre class="term" id="term" tabindex="0" aria-label="Lab output"></pre></section>
        <div style="display:grid;gap:18px;align-content:start">
          <section class="panel panel-pad" id="upd"></section>
          <section class="panel panel-pad" id="rsmini"></section>
          <section class="panel panel-pad" id="shmini"></section>
          <section class="panel panel-pad" id="secmini"></section>
          <section class="panel panel-pad" id="prodmini"></section>
          <section class="panel panel-pad" id="opsmini"></section>
          <section class="panel panel-pad"><h2 style="margin-bottom:8px">See the data in MongoDB Compass</h2>
            <p class="muted" style="margin:0 0 8px;font-size:13.5px">Connect to the first member; browse <b>cti_platform</b> (Lab 7), <b>labdb</b> (Lab 8), <b>lab9</b>, <b>lab10_bank</b> and <b>lab10_shop</b> (Lab 10).</p>
            ${copyCmd("mongodb://localhost:27017/?directConnection=true")}
            <p class="muted" style="margin:10px 0 8px;font-size:13.5px">Lab 11 uses its own sharded cluster; connect to the <b>mongos</b> router to browse <b>lab11</b>, <b>lab11_shop</b> and <b>lab11_reshard</b>.</p>
            ${copyCmd("mongodb://localhost:27030")}
            <p class="muted" style="margin:10px 0 8px;font-size:13.5px">Lab 12 uses the hardened <b>mongo-secure</b> server: TLS with a client certificate and a password (see the Security lab 12 page, tab <i>Connect</i>).</p>
            ${copyCmd("docker compose exec lab12 lab12 shell analyst_app")}
            <p class="muted" style="margin:10px 0 8px;font-size:13.5px">Lab 13 has a production replica set with keyFile authentication (one member at a time from the host) and Prometheus on <b>localhost:9090</b>.</p>
            ${copyCmd("mongodb://admin:<lab13 password admin>@localhost:27051/?directConnection=true&authSource=admin")}</section>
        </div>
      </div>`;
    let timer, alive = true;
    onLeave(() => { alive = false; clearTimeout(timer); });
    const state = { full: {} };

    function node(d) {
      const needs = [];
      for (let n = d.requires; n; n = { 8: 7, 9: 8, 10: 9, 11: 10, 12: 11, 13: 12, 14: 13, 15: 14, 16: 15, 17: 16, 18: 17 }[n]) needs.unshift(n);
      const steps = (d.steps || []).map((s) => `<li class="s-${s.status}"><span>${esc(s.name)}</span><em>${s.seconds != null ? secs(s.seconds) : ""}</em></li>`).join("");
      return `<article class="chain-node st-${d.status || "idle"}">
        <header><span class="lab-no">${d.lab}</span><div><b>${esc(d.short)}</b>${pill(d.status)}</div></header>
        <p class="muted small">${d.requires ? `Needs Lab ${d.requires}${needs.length > 1 ? ` (and so ${needs.slice(0, -1).map((x) => "Lab " + x).join(", ")})` : ""}` : "Foundation: no prerequisite"}</p>
        <ul class="steps">${steps || `<li class="s-idle"><span>No run yet</span></li>`}</ul>
        <footer>
          <span class="muted small">${d.status === "ok" || d.status === "failed" ? `${d.mode || ""} mode, ${secs(d.seconds)}, ${ago(d.finished_at)}` : d.mode ? `${d.mode} mode` : ""}</span>
          <div class="row-gap"><label class="ck small"><input type="checkbox" data-full="${d.lab}" ${state.full[d.lab] ? "checked" : ""}> full</label>
          <button class="btn small primary" data-start="${d.lab}" title="${needs.length ? "Runs " + needs.map((x) => "Lab " + x).join(", ") + " first" : ""}">Start</button></div>
        </footer></article>`;
    }

    async function load() {
      let s;
      try { s = await api("/api/chain/status"); } catch (err) { $("#chain").innerHTML = `<div class="panel empty">${esc(err.message)}</div>`; return schedule(6000); }
      if (!alive) return;
      $("#chain").innerHTML = s.labs.map(node).join(`<div class="chain-arrow" aria-hidden="true">&rarr;</div>`);
      const term = $("#term");
      const atBottom = term.scrollHeight - term.scrollTop - term.clientHeight < 40;
      term.innerHTML = s.log.length ? s.log.map((l) => `<span class="l${l.lab}">${new Date(l.at).toLocaleTimeString("en-IN", { hour12: false })}  Lab ${l.lab}  </span>${esc(l.line)}`).join("\n")
        : `No output yet. Press Start on any lab.\nStarting Lab 18 runs Lab 7 to 17 and then 18.`;
      if (atBottom) term.scrollTop = term.scrollHeight;
      $("#logstate").textContent = s.running ? "running..." : s.log.length ? "finished" : "idle";
      const u = s.updater;
      $("#upd").innerHTML = `<h2 style="margin-bottom:8px">Auto-update (every ${Math.round(u.interval_s / 60)} minutes)</h2>
        <div class="kvr"><span>Updater</span><b>${dot(u.alive)}${u.alive ? "running" : "not running"}</b></div>
        <div class="kvr"><span>Last refresh</span><b>${u.last ? ago(u.last) : "never"}</b></div>
        <div class="kvr"><span>Refresh cycles so far</span><b>${num(u.runs)}</b></div>
        <p class="muted" style="margin:8px 0 0;font-size:13px">Each cycle writes a heartbeat document to <span class="mono">cti_platform.ingest_runs</span> - refresh Compass and a new one appears.</p>`;
      schedule(s.running ? 1500 : 6000);
    }
    const schedule = (ms) => { clearTimeout(timer); if (alive) timer = setTimeout(load, ms); };

    async function mini() {
      try {
        const r = await api("/api/rs/status");
        if (!alive) return;
        $("#rsmini").innerHTML = `<h2 style="margin-bottom:8px">Replica set</h2>` + (r.available
          ? r.members.map((m) => `<div class="kvr"><span>${esc(m.name)}</span><b>${dot(m.health === 1)}<span class="role r-${m.state}">${esc(m.state)}</span></b></div>`).join("") +
            `<p style="margin:8px 0 0"><a href="#/lab8">Open Lab 8 &rarr;</a></p>`
          : `<p class="muted" style="margin:0">${esc(r.reason)}</p>`);
      } catch (e) { /* ignore */ }
      try {
        const c = await api("/api/lab11/status");
        if (!alive) return;
        $("#shmini").innerHTML = `<h2 style="margin-bottom:8px">Sharded cluster (Lab 11)</h2>` + (c.available
          ? c.shards.map((s) => `<div class="kvr"><span>${esc(s.name)}</span><b>${s.members.map((m) => `${dot(m.health === 1)}<span class="role r-${m.state}">${esc(m.state === "PRIMARY" ? "P" : m.state === "SECONDARY" ? "S" : "?")}</span>`).join(" ")}</b></div>`).join("") +
            `<div class="kvr"><span>Router</span><b>${dot(true)}mongos ${esc(c.mongos_version)}</b></div><p style="margin:8px 0 0"><a href="#/lab11">Open Lab 11</a></p>`
          : `<p class="muted" style="margin:0">${esc(c.reason)}</p>`);
      } catch (e) { /* ignore */ }
      try {
        const c = await api("/api/lab12/status");
        if (!alive) return;
        $("#secmini").innerHTML = `<h2 style="margin-bottom:8px">Secure server (Lab 12)</h2>` + (c.available
          ? `<div class="kvr"><span>mongo-secure</span><b>${dot(true)}${esc(c.version)} (${esc(c.set)})</b></div>
             <div class="kvr"><span>TLS</span><b>${dot(c.tls_mode === "requireTLS")}${esc(c.tls_mode)}, client certs ${c.client_certs_required ? "required" : "optional"}</b></div>
             <div class="kvr"><span>Users</span><b>${num(c.users.length)}</b></div><p style="margin:8px 0 0"><a href="#/lab12">Open Lab 12</a></p>`
          : `<p class="muted" style="margin:0">${esc(c.reason)}</p>`);
      } catch (e) { /* ignore */ }
      try {
        const c = await api("/api/lab13/status");
        if (!alive) return;
        const ms = (c.rs || {}).members || [];
        $("#prodmini").innerHTML = `<h2 style="margin-bottom:8px">Production set + Kubernetes (Lab 13)</h2>` + (c.available
          ? ms.map((m) => `<div class="kvr"><span>${esc(m.name)}</span><b>${dot(m.health === 1)}<span class="role r-${m.state}">${esc(m.state)}</span></b></div>`).join("") +
            `<div class="kvr"><span>Kubernetes (k3s)</span><b>${dot((c.k8s || {}).ready)}${(c.k8s || {}).ready ? `${((c.k8s || {}).pods || []).filter((p) => p.ready).length} pods ready` : "not ready"}</b></div>
             <p style="margin:8px 0 0"><a href="#/lab13">Open Lab 13</a></p>`
          : `<p class="muted" style="margin:0">${esc(c.reason || "not started")}</p>`);
      } catch (e) { /* ignore */ }
      try {
        const c = await api("/api/lab14/status");
        if (!alive) return;
        const d = c.daemon || {};
        $("#opsmini").innerHTML = `<h2 style="margin-bottom:8px">Operations (Lab 14)</h2>` + (c.available
          ? `<div class="kvr"><span>Ops daemon</span><b>${dot(d.alive)}${d.alive ? `${num(d.samples || 0)} samples` : "not running"}</b></div>
             <div class="kvr"><span>Last backup</span><b>${d.last_backup ? `${dot(d.last_backup.ok)}${esc(d.last_backup.target)} ${ago(d.last_backup.at)}` : "-"}</b></div>
             <div class="kvr"><span>Alerts firing</span><b>${dot(!(c.alerts_firing || []).length)}${(c.alerts_firing || []).length}</b></div>
             <p style="margin:8px 0 0"><a href="#/lab14">Open Lab 14</a></p>`
          : `<p class="muted" style="margin:0">${esc(c.reason || "not started")}</p>`);
      } catch (e) { /* ignore */ }
      if (alive) setTimeout(mini, 5000);
    }

    page.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-start]");
      if (!b) return;
      const lab = Number(b.dataset.start);
      await act(b, "Starting", async () => {
        const r = await api("/api/chain/run", { method: "POST", body: { lab, full: !!state.full[lab] } });
        toast(`Starting ${r.order.map((n) => "Lab " + n).join(" &rarr; ")}`);
        load();
      });
    });
    page.addEventListener("change", (e) => { const c = e.target.closest("[data-full]"); if (c) state.full[c.dataset.full] = c.checked; });
    load();
    mini();
  }

  // ================================================================== Lab 8
  async function pageLab8(page) {
    page.innerHTML = `${intro("Replica set lab 8",
      `A replica set keeps the same data on three servers. One is the <b>primary</b> (takes all writes); the others are <b>secondaries</b> that copy it.
       If the primary stops, the secondaries vote and one becomes the new primary - this is a <b>failover</b>.`,
      `<div class="row-gap"><button class="btn small" id="verify">Verify replication (w:3)</button><button class="btn small primary" id="stepdown">Step down primary</button></div>`)}
      <div id="rsbody"></div>
      <div class="grid cols-2" style="margin-top:18px">
        <section class="panel"><div class="panel-head"><h2>Replication check</h2><span class="muted">Lab 8.1</span></div><div class="panel-pad" id="vout"><p class="muted" style="margin:0">Inserts one document with <span class="mono">w:3</span> (acknowledged only after all three members wrote it) and then reads it directly from each secondary.</p></div></section>
        <section class="panel"><div class="panel-head"><h2>Election events seen by this page</h2><span class="muted">Lab 8.2</span></div><div class="panel-pad" id="events"><p class="muted" style="margin:0">Press <b>Step down primary</b>. The page polls the set every 2 seconds and records who is primary.</p></div></section>
      </div>
      ${explain("What to explain to the mentor", `<ol class="plain">
        <li><b>Why three servers?</b> An election needs a majority of the voting members. With 3 voters the majority is 2, so one server can fail and the set still works.</li>
        <li><b>Why priority?</b> <span class="mono">mongo1</span> has priority 2, so after a failover it takes the primary role back as soon as it is healthy again.</li>
        <li><b>Why does the arbiter not help here?</b> An arbiter only votes, it stores no data. It is useful only when it turns an even number of voters into an odd one.</li>
        <li><b>What does the driver do during a failover?</b> It notices the old primary stopped answering, finds the new primary by itself and retries the write once (retryable writes).</li></ol>`)}`;
    let alive = true;
    const events = [];
    let lastPrimary = null, term = null;
    onLeave(() => { alive = false; });

    function draw(r) {
      if (!r.available) { $("#rsbody").innerHTML = noRS(r.reason); return; }
      $("#rsbody").innerHTML = `<section class="panel panel-pad" style="margin-top:18px">
        <div class="stat-line" style="border-top:none;margin:-18px -20px 14px">
          <div><b>${esc(r.set)}</b><span>replica set</span></div><div><b>${r.term}</b><span>election term (rises by 1 per election)</span></div>
          <div><b>${r.voters}</b><span>voting members</span></div><div><b>${r.majority}</b><span>votes needed to elect</span></div>
          <div><b>${r.voters - r.majority}</b><span>failure(s) tolerated</span></div></div>
        <div class="members">${r.members.map((m) => `<article class="member ${m.state === "PRIMARY" ? "is-primary" : ""} ${m.health !== 1 ? "is-down" : ""}">
          <header><span class="role r-${m.state}">${esc(m.state)}</span>${dot(m.health === 1)}</header>
          <h3>${esc(m.name)}</h3>
          <div class="kvr"><span>Priority</span><b>${m.priority ?? "-"}</b></div><div class="kvr"><span>Votes</span><b>${m.votes ?? "-"}</b></div>
          <div class="kvr"><span>Replication lag</span><b>${m.lag_s == null ? "-" : m.lag_s + " s"}</b></div>
          <div class="kvr"><span>Copies data from</span><b>${esc(m.sync_source || (m.state === "PRIMARY" ? "(is primary)" : "-"))}</b></div>
          <div class="kvr"><span>Up for</span><b>${m.uptime != null ? secs(m.uptime) : "-"}</b></div></article>`).join("")}</div>
        <p class="muted" style="margin:12px 0 0;font-size:13px">w:majority = ${r.write_majority_count} members. Journal flushed before a majority acknowledgement: ${r.journal_majority_default ? "yes (default)" : "no"}.</p></section>`;
      if (lastPrimary !== null && r.primary !== lastPrimary) {
        events.unshift(`${new Date().toLocaleTimeString("en-IN", { hour12: false })}  PRIMARY changed: ${lastPrimary || "none"} &rarr; ${r.primary || "none (election running)"}   (term ${term} &rarr; ${r.term})`);
        $("#events").innerHTML = `<ul class="plain mono">${events.slice(0, 8).map((x) => `<li>${x}</li>`).join("")}</ul>`;
      }
      lastPrimary = r.primary; term = r.term;
    }
    async function poll() {
      if (!alive) return;
      try { draw(await api("/api/rs/status")); } catch (err) { $("#rsbody").innerHTML = errBox(err); }
      setTimeout(poll, 2000);
    }
    const errBox = (e) => `<div class="panel empty" style="margin-top:18px">${esc(e.message)}</div>`;
    $("#verify").addEventListener("click", (e) => act(e.currentTarget, "Verifying", async () => {
      const r = await api("/api/lab8/verify", { method: "POST" });
      $("#vout").innerHTML = `<p style="margin:0 0 8px">Primary <b>${esc(r.primary)}</b> acknowledged the write after <b>${r.ack_ms} ms</b> (w:3).</p>
        <table class="table"><thead><tr><th>Secondary (read directly)</th><th>Document</th></tr></thead><tbody>${r.secondaries.map((s) => `<tr><td class="mono">${esc(s.member)}</td><td>${s.found ? `${dot(true)}found` : `${dot(false)}MISSING`}</td></tr>`).join("")}</tbody></table>`;
    }));
    $("#stepdown").addEventListener("click", (e) => act(e.currentTarget, "Electing", async () => {
      const r = await api("/api/rs/stepdown", { method: "POST", body: { seconds: 30 } });
      toast(r.new_primary ? `New primary ${esc(r.new_primary)} after ${r.election_ms} ms` : "No new primary yet - an election is still running");
    }));
    poll();
  }

  // ================================================================== Lab 9
  async function pageLab9(page) {
    const [saved, rs] = await Promise.all([api("/api/lab9/results").catch(() => ({})), api("/api/rs/status")]);
    const savedRows = saved.w ? Object.values(saved.w.results) : [];
    page.innerHTML = `${intro("Write concern lab 9",
      `A <b>write concern</b> says how many servers must confirm a write before MongoDB tells your program "done".
       <span class="mono">w:1</span> = only the primary, <span class="mono">w:majority</span> = most members, <span class="mono">w:all</span> = every member. More confirmations = safer but slower.`,
      `<button class="btn small primary" id="quick" ${rs.available ? "" : "disabled"}>Run live benchmark</button>`)}
      ${rs.available ? "" : noRS(rs.reason)}
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Live benchmark</h2><span class="muted">100 inserts per write concern, run right now</span></div>
        <div class="panel-pad" id="qout"><p class="muted" style="margin:0">Press <b>Run live benchmark</b> (takes a few seconds).</p></div></section>
      ${savedRows.length ? `<section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Saved result of the full Lab 9.1 run</h2><span class="muted">${saved.w.docs} inserts per concern, mean of ${saved.w.rounds} rounds</span></div>
        <div class="panel-pad">${bars(savedRows.map((r, i) => ({ label: r.label, value: r.avg_ms, color: ["var(--ok)", "var(--accent)", "var(--mal)"][i % 3] })), { unit: " ms", fmt: (v) => v.toFixed(2) })}
        <table class="table" style="margin-top:10px"><thead><tr><th>Write concern</th><th class="num">Avg ms</th><th class="num">P95 ms</th><th class="num">Writes/s</th></tr></thead><tbody>
        ${savedRows.map((r) => `<tr><td>${esc(r.label)}</td><td class="num">${r.avg_ms.toFixed(3)}</td><td class="num">${r.p95_ms.toFixed(3)}</td><td class="num">${num(Math.round(r.throughput_ops))}</td></tr>`).join("")}</tbody></table></div></section>` : ""}
      ${saved.journal ? `<section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Journaling activity: j:true vs j:false</h2></div><div class="panel-pad"><table class="table"><thead><tr><th>Configuration</th><th class="num">Avg ms</th><th class="num">P95 ms</th></tr></thead><tbody>
        ${Object.values(saved.journal.results).map((r) => `<tr><td>${esc(r.label)}</td><td class="num">${r.avg_ms.toFixed(3)}</td><td class="num">${r.p95_ms.toFixed(3)}</td></tr>`).join("")}</tbody></table></div></section>` : ""}
      ${saved.timeline ? `<section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Lab 9.2 network partition - timeline</h2></div><pre class="term" style="max-height:320px">${esc(saved.timeline)}</pre></section>` : ""}
      ${!savedRows.length ? explain("Run the complete Lab 9 (including the network partition)", `<p style="margin:0 0 8px">Press <b>Start</b> with <b>full</b> ticked on Lab 9 in the <a href="#/labs">control centre</a>, or run:</p>${copyCmd("docker compose exec lab9 lab9 all")}`) : ""}
      ${explain("Plain-language summary", `<ol class="plain"><li><b>Why is w:majority slower?</b> The primary must wait for a second server to receive and write the change - one extra network trip.</li>
        <li><b>Network partition (Lab 9.2):</b> if one secondary is cut off, writes still work (the other two are a majority). If both are cut off, the primary has no majority, steps down and the set becomes read-only. This is the CAP trade-off: MongoDB chooses consistency over availability.</li></ol>`)}`;
    $("#quick")?.addEventListener("click", (e) => act(e.currentTarget, "Measuring", async () => {
      const r = await api("/api/lab9/quick", { method: "POST", body: { docs: 100 } });
      const base = r.rows[0].avg_ms;
      $("#qout").innerHTML = bars(r.rows.map((x, i) => ({ label: x.label, value: x.avg_ms, color: ["var(--ok)", "var(--accent)", "var(--mal)"][i] })), { unit: " ms", fmt: (v) => v.toFixed(2) }) +
        `<table class="table" style="margin-top:10px"><thead><tr><th>Write concern</th><th class="num">Avg ms</th><th class="num">P50</th><th class="num">P95</th><th class="num">Writes/s</th><th class="num">vs w:1</th></tr></thead><tbody>${r.rows.map((x) => `<tr><td>${esc(x.label)}</td><td class="num">${x.avg_ms}</td><td class="num">${x.p50_ms}</td><td class="num">${x.p95_ms}</td><td class="num">${num(x.writes_per_s)}</td><td class="num">${(x.avg_ms / base).toFixed(2)}x</td></tr>`).join("")}</tbody></table>`;
    }));
  }

  // ================================================================== Lab 10
  async function pageLab10(page, route) {
    const tab = route.params.get("tab") || "bank";
    const rs = await api("/api/rs/status");
    page.innerHTML = `${intro("Transactions lab 10",
      `A <b>transaction</b> groups several writes into one unit: all of them happen or none do (<b>atomicity</b>), nobody sees a half-finished state (<b>isolation</b>),
       and once committed the data survives a crash (<b>durability</b>, thanks to the journal and <span class="mono">w:majority</span>).`,
      `<div class="row-gap"><a class="btn small" href="/api/lab10/report" target="_blank" rel="noopener">Download PDF report</a><button class="btn small" id="reset">Reset demo data</button></div>`)}
      ${rs.available ? "" : noRS(rs.reason)}
      <nav class="tabs" role="tablist">${[["bank", "Bank transfer (10.1)"], ["shop", "Inventory (10.2)"], ["bench", "Throughput and results"]].map(([k, t]) => `<button role="tab" data-tab="${k}" class="${tab === k ? "active" : ""}">${t}</button>`).join("")}</nav>
      <div id="tabbody"></div>`;
    $$("[data-tab]").forEach((b) => b.addEventListener("click", () => { location.hash = `#/lab10?tab=${b.dataset.tab}`; }));
    if (!rs.available) return;
    $("#reset").addEventListener("click", (e) => act(e.currentTarget, "Resetting", async () => { await api("/api/lab10/reset", { method: "POST" }); toast("Demo data reset: ACC-001 = 1000, ACC-002 = 500, stock = 5"); location.hash = `#/lab10?tab=${tab}&r=${Date.now() % 1000}`; }));
    const state = await api("/api/lab10/state");
    if (!state.bank.accounts.length) { await api("/api/lab10/reset", { method: "POST" }); return pageLab10(page, route); }
    ({ bank: bankTab, shop: shopTab, bench: benchTab }[tab] || bankTab)($("#tabbody"), state);
  }

  const outcomeTag = (o) => ({ committed: "ok", rolled_back: "sus", failed: "err" }[o] || "idle");

  function bankTab(box, state) {
    let bank = state.bank;
    const accounts = () => bank.accounts.map((a) => `<article class="acct"><span class="muted small">${esc(a.owner)} &middot; ${esc(a.account_id)}</span><b class="big" id="bal-${a.account_id}">${num(a.balance)}</b><span class="muted small">version ${a.version}</span></article>`).join("");
    box.innerHTML = `<div class="grid cols-2" style="align-items:start">
      <section class="panel panel-pad"><h2 style="margin-bottom:10px">Accounts</h2>
        <div class="accts" id="accts">${accounts()}</div>
        <p class="muted" style="margin:10px 0 0" id="total"></p>
        <h3 style="margin:18px 0 8px">Make a transfer</h3>
        <div class="form-row"><label>From<select id="src"><option>ACC-001</option><option>ACC-002</option></select></label>
          <label>To<select id="dst"><option>ACC-002</option><option>ACC-001</option></select></label>
          <label>Amount<input id="amt" type="number" min="1" value="100"></label></div>
        <div class="row-gap" style="margin-top:12px;flex-wrap:wrap">
          <button class="btn primary" id="go">Transfer</button>
          <button class="btn" id="big">Try 5000 (not enough money)</button>
          <button class="btn" id="crash">Crash after the debit</button></div>
        <div id="res" style="margin-top:14px"></div></section>
      <section class="panel"><div class="panel-head"><h2>Transaction history</h2><span class="muted">written inside the same transaction</span></div><div id="hist" class="panel-pad"></div></section></div>
      <div class="grid cols-2" style="margin-top:18px;align-items:start">
        <section class="panel"><div class="panel-head"><h2>Activity 10.1: retry with exponential back-off</h2></div><div class="panel-pad">
          <p class="muted" style="margin:0 0 10px">Transfer A debits ACC-001 and keeps its transaction open for 0.6 s. Transfer B starts 0.15 s later on the same account, so the server aborts it with a write conflict. B waits 0.1 s, 0.2 s, 0.4 s... and retries until A has committed.</p>
          <button class="btn small primary" id="conflict">Run the conflict demo</button><div id="cout" style="margin-top:12px"></div></div></section>
        <section class="panel"><div class="panel-head"><h2>Why the code checks <span class="mono">matched_count</span></h2></div><div class="panel-pad">
          <p class="muted" style="margin:0 0 10px">The classroom sample uses a filter <span class="mono">balance &gt;= 100</span>, but a filter that matches nothing does <b>not</b> raise an error, so the credit would still run. This button runs the sample logic as written.</p>
          <button class="btn small" id="bug">Run the sample logic with 5000</button><div id="bout" style="margin-top:12px"></div></div></section></div>`;
    const paint = () => {
      $("#accts").innerHTML = accounts();
      $("#total").innerHTML = `Total money in the bank: <b>${num(bank.total)}</b> (always 1,500 when transactions work).`;
      $("#hist").innerHTML = bank.history.length ? `<table class="table"><thead><tr><th>When</th><th>From</th><th>To</th><th class="num">Amount</th></tr></thead><tbody>${bank.history.map((t) => `<tr><td>${fmtTime(t.timestamp)}</td><td class="mono">${esc(t.from)}</td><td class="mono">${esc(t.to)}</td><td class="num">${num(t.amount)}</td></tr>`).join("")}</tbody></table>`
        : `<p class="muted" style="margin:0">No committed transfers yet. A rolled-back transfer leaves no row here.</p>`;
    };
    paint();
    $("#src").addEventListener("change", () => { $("#dst").value = $("#src").value === "ACC-001" ? "ACC-002" : "ACC-001"; });
    async function transfer(btn, amount, crash) {
      await act(btn, "In transaction", async () => {
        const before = Object.fromEntries(bank.accounts.map((a) => [a.account_id, a.balance]));
        const r = await api("/api/lab10/transfer", { method: "POST", body: { src: $("#src").value, dst: $("#dst").value, amount, crash } });
        bank = r.bank;
        const after = Object.fromEntries(bank.accounts.map((a) => [a.account_id, a.balance]));
        const same = JSON.stringify(before) === JSON.stringify(after);
        const x = r.result;
        $("#res").innerHTML = `<div class="result ${outcomeTag(x.outcome)}"><b>${x.outcome === "committed" ? "Committed" : x.outcome === "rolled_back" ? "Rolled back" : "Failed"}</b>
          ${x.reason ? `<span>${esc(x.reason)}</span>` : ""}<span class="muted small">${x.attempts} attempt(s), ${x.ms} ms</span>
          ${x.outcome === "rolled_back" ? `<span>Balances unchanged: <b>${same ? "yes" : "NO"}</b> &mdash; nothing was half-done.</span>` : `<span>Money moved: ${num(x.amount)} from ${esc(x.src)} to ${esc(x.dst)}.</span>`}</div>`;
        paint();
      });
    }
    $("#go").addEventListener("click", (e) => transfer(e.currentTarget, Number($("#amt").value || 0), false));
    $("#big").addEventListener("click", (e) => transfer(e.currentTarget, 5000, false));
    $("#crash").addEventListener("click", (e) => transfer(e.currentTarget, Number($("#amt").value || 100), true));
    $("#conflict").addEventListener("click", (e) => act(e.currentTarget, "Running", async () => {
      const d = await api("/api/lab10/conflict-demo", { method: "POST" });
      bank = await api("/api/lab10/state").then((s) => s.bank); paint();
      const row = (n, x, note) => `<tr><td>${n}<div class="muted small">${note}</div></td><td><span class="pill st-${x.ok ? "ok" : "failed"}">${esc(x.outcome)}</span></td><td class="num">${x.attempts}</td><td class="num">${x.conflicts}</td><td class="mono">${esc(JSON.stringify(x.delays))}</td><td class="num">${x.ms}</td></tr>`;
      $("#cout").innerHTML = `<table class="table"><thead><tr><th>Transfer</th><th>Outcome</th><th class="num">Attempts</th><th class="num">Conflicts</th><th>Waits (s)</th><th class="num">ms</th></tr></thead><tbody>${row("A: 100", d.A, "holds its transaction 0.6 s")}${row("B: 50", d.B, "starts 0.15 s later")}</tbody></table>
        <p style="margin:10px 0 0">Money before ${num(d.total_before)}, after ${num(d.total_after)}: <b>${d.money_conserved ? "conserved" : "LOST"}</b>. B was aborted ${d.B.conflicts} time(s) by the server and succeeded on its own.</p>`;
    }));
    $("#bug").addEventListener("click", (e) => act(e.currentTarget, "Running", async () => {
      const d = await api("/api/lab10/sample-bug", { method: "POST" });
      $("#bout").innerHTML = `<div class="result err"><b>Money created from nothing</b><span>The debit matched ${d.debit_matched} document(s), yet the credit ran: total ${num(d.total_before)} &rarr; ${num(d.total_after)} (+${num(d.total_after - d.total_before)}).</span>
        <span class="muted small">Fix used in this project: check <span class="mono">result.matched_count</span> and raise an error, which aborts the transaction. Demo data was reset.</span></div>`;
      bank = await api("/api/lab10/state").then((s) => s.bank); paint();
    }));
  }

  function shopTab(box, state) {
    let shop = state.shop;
    box.innerHTML = `<div class="grid cols-2" style="align-items:start">
      <section class="panel panel-pad"><h2 style="margin-bottom:10px">Product P-100 &middot; Mechanical keyboard</h2>
        <div class="accts" id="stock"></div>
        <div class="row-gap" style="margin-top:12px"><button class="btn primary" id="one">Reserve 1 unit</button></div>
        <div id="one-out" style="margin-top:12px"></div>
        <h3 style="margin:18px 0 8px">Orders</h3><div id="orders"></div></section>
      <section class="panel panel-pad"><h2 style="margin-bottom:6px">Concurrency test</h2>
        <p class="muted" style="margin:0 0 10px">Many threads press "reserve" at the same instant. The stock is reset first.</p>
        <div class="form-row"><label>Threads<select id="thr"><option>4</option><option selected>8</option><option>16</option></select></label>
          <label>Stock<input id="stk" type="number" min="1" max="50" value="5"></label>
          <label class="ck"><input type="checkbox" id="retry" checked> retry on conflict</label></div>
        <div class="row-gap" style="margin-top:12px"><button class="btn primary" id="conc">Run concurrency test</button></div><div id="cout" style="margin-top:14px"></div></section></div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Atomicity: crash after the stock decrement</h2><span class="muted">transaction vs findAndModify</span></div><div class="panel-pad">
        <p class="muted" style="margin:0 0 10px">The process "crashes" after the stock was reduced but before the order was saved.</p>
        <button class="btn small primary" id="atom">Run the crash test</button><div id="aout" style="margin-top:12px"></div></div></section>`;
    const paint = () => {
      const inv = shop.inventory || {};
      $("#stock").innerHTML = `<article class="acct"><span class="muted small">In stock</span><b class="big">${inv.stock ?? "-"}</b><span class="muted small">version ${inv.version ?? "-"}</span></article>
        <article class="acct"><span class="muted small">Orders</span><b class="big">${shop.order_count}</b><span class="muted small">${shop.units_ordered} unit(s)</span></article>`;
      $("#orders").innerHTML = shop.orders.length ? `<table class="table"><thead><tr><th>Order</th><th>Customer</th><th>Status</th></tr></thead><tbody>${shop.orders.slice(0, 6).map((o) => `<tr><td class="mono">${esc(o.order_id)}</td><td>${esc(o.customer_id)}</td><td>${esc(o.status)}</td></tr>`).join("")}</tbody></table>` : `<p class="muted" style="margin:0">No orders.</p>`;
    };
    paint();
    $("#one").addEventListener("click", (e) => act(e.currentTarget, "Reserving", async () => {
      const r = await api("/api/lab10/reserve", { method: "POST", body: {} });
      shop = r.shop; paint();
      const x = r.result;
      $("#one-out").innerHTML = `<div class="result ${x.status === "reserved" ? "ok" : "sus"}"><b>${x.status === "reserved" ? "Reserved" : x.status === "out_of_stock" ? "Out of stock" : esc(x.status)}</b><span class="muted small">${x.attempts || 1} attempt(s), ${x.ms} ms${x.reason ? " - " + esc(x.reason) : ""}</span></div>`;
    }));
    $("#conc").addEventListener("click", (e) => act(e.currentTarget, "Running", async () => {
      const r = await api("/api/lab10/concurrency", { method: "POST", body: { threads: Number($("#thr").value), stock: Number($("#stk").value), retry: $("#retry").checked } });
      shop = (await api("/api/lab10/state")).shop; paint();
      const s = r.summary;
      $("#cout").innerHTML = `<div class="tiles">${r.results.map((x) => `<span class="tile t-${x.status}" title="${esc(x.customer)}: ${esc(x.status)}, ${x.attempts || 1} attempt(s)">${esc(x.customer.replace("cust-", "#"))}<em>${x.status === "reserved" ? "OK" : x.status === "out_of_stock" ? "none left" : "conflict"}${x.conflicts ? ` &middot; ${x.conflicts} retry` : ""}</em></span>`).join("")}</div>
        <p style="margin:12px 0 4px"><b>${s.reserved}</b> reserved, <b>${s.out_of_stock}</b> told "out of stock", <b>${s.write_conflict}</b> failed with a write conflict, ${s.retries} retries, ${r.seconds} s.</p>
        <p style="margin:0">Final stock <b>${r.final_stock}</b>, orders <b>${r.orders}</b>. Oversold: <b>${r.oversold ? "YES" : "no"}</b>. Stock matches orders: <b>${r.consistent ? "yes" : "NO"}</b>.</p>
        ${!r.retry && s.write_conflict ? `<p class="muted" style="margin:8px 0 0;font-size:13px">Without retry, ${s.write_conflict} request(s) died although stock may have been left - tick "retry on conflict" and run again.</p>` : ""}
        ${r.retry && s.out_of_stock === r.threads - r.stock_start ? `<p class="muted" style="margin:8px 0 0;font-size:13px">With retry the first ${s.reserved} succeed and the rest correctly fail because the stock reached zero.</p>` : ""}`;
    }));
    $("#atom").addEventListener("click", (e) => act(e.currentTarget, "Running", async () => {
      const a = await api("/api/lab10/atomicity", { method: "POST" });
      shop = (await api("/api/lab10/state")).shop; paint();
      const col = (t, x, hint) => `<div class="result ${x.consistent ? "ok" : "err"}"><b>${t}</b><span>stock ${x.stock} (should be 5), orders ${x.orders} (should be 0)</span><span>${x.consistent ? "Consistent: everything rolled back." : "INCONSISTENT: a unit disappeared and no order exists."}</span><span class="muted small">${hint}</span></div>`;
      $("#aout").innerHTML = `<div class="grid cols-2">${col("Transaction", a.transactional, "Stock decrement and order insert are one unit.")}${col("findAndModify", a.non_transactional, "Atomic on one document only; the order is a second step.")}</div>`;
    }));
  }

  async function benchTab(box) {
    const saved = await api("/api/lab10/results").catch(() => ({}));
    box.innerHTML = `<section class="panel"><div class="panel-head"><h2>Throughput: transaction vs findAndModify</h2><span class="muted">successful reservations per second</span></div>
      <div class="panel-pad"><p class="muted" style="margin:0 0 10px">1, 4, 8 and 16 threads fight over one product. The transaction keeps stock and order consistent; findAndModify is faster but not atomic across two collections.</p>
      <button class="btn small primary" id="bench">Run benchmark (about 30-60 s)</button><div id="bout" style="margin-top:14px"></div></div></section>
      ${saved.throughput ? `<section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Result of the last script run</h2><span class="muted">${fmtTime(saved.ran_at)}</span></div><div class="panel-pad" id="sout"></div></section>` : ""}
      ${saved.lab_10_1 ? `<section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:8px">Checks from the last script run</h2><div id="checks"></div></section>` : ""}`;
    const show = (b, el) => {
      const rows = b.rows.map((r) => ({ label: `${r.threads} thread${r.threads > 1 ? "s" : ""}`, a: r.transactional.per_second, b: r.non_transactional.per_second }));
      el.innerHTML = groupedBars(rows, "a", "b", { aName: "multi-document transaction", bName: "findAndModify (no transaction)" }) +
        `<table class="table" style="margin-top:10px"><thead><tr><th>Threads</th><th class="num">Txn ok/s</th><th class="num">Txn retries</th><th class="num">Txn failed</th><th class="num">findAndModify ok/s</th></tr></thead><tbody>${b.rows.map((r) => `<tr><td>${r.threads}</td><td class="num">${r.transactional.per_second}</td><td class="num">${r.transactional.retries}</td><td class="num">${r.transactional.failed}</td><td class="num">${r.non_transactional.per_second}</td></tr>`).join("")}</tbody></table>
        <ul class="plain" style="margin-top:10px">${b.explanation.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
    };
    if (saved.throughput) show(saved.throughput, $("#sout"));
    if (saved.lab_10_1) {
      const a = saved.lab_10_1, b = saved.lab_10_2;
      const checks = [["Transfer committed", a.success.ok], ["Insufficient funds rolled back", a.insufficient.unchanged], ["Crash after debit rolled back", a.crash.unchanged],
        ["Conflict demo: both committed, money conserved", a.conflict.A.ok && a.conflict.B.ok && a.conflict.money_conserved]];
      if (b) checks.push(["Inventory: 5 reserved, 3 out of stock, never oversold", b.with_retry.summary.reserved === 5 && !b.with_retry.oversold], ["Atomicity: transaction consistent after crash", b.atomicity.transactional.consistent]);
      $("#checks").innerHTML = checks.map(([t, v]) => `<div class="kvr"><span>${esc(t)}</span><b>${dot(v)}${v ? "pass" : "FAIL"}</b></div>`).join("");
    }
    $("#bench").addEventListener("click", (e) => act(e.currentTarget, "Measuring", async () => {
      const b = await api("/api/lab10/throughput", { method: "POST", body: { ops: 60 } });
      show(b, $("#bout"));
    }));
  }

  const lab11 = window.KavachLab11 ? window.KavachLab11({ ...h, intro, explain, act, dot, pill }) : {};
  const lab12 = window.KavachLab12 ? window.KavachLab12({ ...h, intro, explain, act, dot, pill }) : {};
  const lab13 = window.KavachLab13 ? window.KavachLab13({ ...h, intro, explain, act, dot, pill }) : {};
  const lab14 = window.KavachLab14 ? window.KavachLab14({ ...h, intro, explain, act, dot, pill }) : {};
  const lab15 = window.KavachLab15 ? window.KavachLab15({ ...h, intro, explain, act, dot, pill }) : {};
  const lab16 = window.KavachLab16 ? window.KavachLab16({ ...h, intro, explain, act, dot, pill }) : {};
  const lab17 = window.KavachLab17 ? window.KavachLab17({ ...h, intro, explain, act, dot, pill }) : {};
  const lab18 = window.KavachLab18 ? window.KavachLab18({ ...h, intro, explain, act, dot, pill }) : {};
  async function pageLab10Billing(page, route) {          // Lab 10 applied to Lab 7: the classifier paywall
    await pageLab10(page, route);
    if (!window.KavachBilling) return;
    const host = document.createElement("div");
    page.appendChild(host);
    window.KavachBilling.lab10Panel(host, { ...h, explain, act });
  }
  return { labs: pageLabs, lab8: pageLab8, lab9: pageLab9, lab10: pageLab10Billing, ...lab11, ...lab12, ...lab13, ...lab14, ...lab15, ...lab16, ...lab17, ...lab18 };
};

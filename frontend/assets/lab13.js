/* Kavach CTI - Containers & cloud lab 13 (Docker Compose replica set, exporter sidecar + Prometheus, Kubernetes
   StatefulSet, Lab 13 applied to Lab 10, Atlas/Terraform). Loaded before labs.js; labs.js calls window.KavachLab13(helpers)
   and adds the returned route. Every number comes from /api/lab13/* = backend/app/lab13.py on the running deployment. */
window.KavachLab13 = (h) => {
  "use strict";
  const { $, $$, esc, num, api, toast, copyCmd, onLeave, intro, explain, act, dot } = h;
  const s1 = (ms) => (ms == null ? "-" : `${(ms / 1000).toFixed(1)} s`);
  const yes = (v, t = "pass", f = "FAIL") => `${dot(!!v)}${v ? t : f}`;
  const notRun = (what) => `<div class="panel empty"><strong>No results yet</strong>${what} Start Lab 13 in the <a href="#/labs">Lab control centre</a>,
    or run <span class="mono">docker compose exec lab13 lab13 all</span>.</div>`;
  const noProd = (reason) => `<div class="banner err" style="margin:18px 0"><div><strong>Production replica set not available.</strong> ${esc(reason || "")}
    <div style="margin-top:6px">Lab 13 runs its own keyFile replica set (prod-mongo-1/2/3), Prometheus and Kubernetes. Start them, together with Labs 7 to 12, with:</div>${copyCmd("docker compose up -d lab13")}</div></div>`;
  const role = (s) => `<span class="role r-${esc(s || "?")}">${esc(s || "down")}</span>`;
  const fileBtn = (name, label) => `<a class="btn small" href="/api/lab13/file/${name}" target="_blank" rel="noopener">${esc(label)}</a>`;

  // ------------------------------------------------------------------ timeline of a failover (writes per 0.5 s)
  function timeline(f) {
    const tl = f.timeline || [];
    if (!tl.length) return "";
    const W = 900, H = 150, L = 34, B = 26, T = 10, ph = H - B - T;
    const mx = Math.max(1, ...tl.map((b) => b.w_ok));
    const bw = (W - L - 8) / tl.length, span = tl[tl.length - 1].t + 500;
    const x = (t) => L + ((W - L - 8) * t) / span;
    let g = "";
    tl.forEach((b, i) => {
      const hh = (ph * b.w_ok) / mx;
      g += `<rect x="${(L + i * bw).toFixed(1)}" y="${(T + ph - hh).toFixed(1)}" width="${Math.max(bw - 1, 0.6).toFixed(1)}" height="${hh.toFixed(1)}" rx="1.5" style="fill:var(--accent)"><title>${(b.t / 1000).toFixed(1)} s: ${b.w_ok} writes, ${b.w_err} write errors, ${b.r_err} read errors, primary ${b.primary || "none"}</title></rect>`;
      if (b.w_err || b.r_err) g += `<rect x="${(L + i * bw).toFixed(1)}" y="${T + ph - 6}" width="${Math.max(bw - 1, 0.6).toFixed(1)}" height="6" style="fill:var(--mal)"/>`;
    });
    if (f.event_ms) g += `<line x1="${x(f.event_ms)}" x2="${x(f.event_ms)}" y1="${T}" y2="${T + ph}" style="stroke:var(--text);stroke-dasharray:4 3"/><text x="${x(f.event_ms) + 4}" y="${T + 10}" class="ax">${f.event === "crash" ? "kill -9" : "stepDown"}</text>`;
    if (f.election_ms != null) { const xe = x(f.event_ms + f.election_ms); g += `<line x1="${xe}" x2="${xe}" y1="${T}" y2="${T + ph}" style="stroke:var(--ok);stroke-dasharray:4 3"/><text x="${xe + 4}" y="${T + 24}" class="ax">new PRIMARY</text>`; }
    for (let s = 0; s * 1000 <= span; s += 5) g += `<text x="${x(s * 1000)}" y="${H - 8}" text-anchor="middle" class="ax">${s}s</text>`;
    return `<svg viewBox="0 0 ${W} ${H}" class="lchart" role="img" aria-label="Writes per half second during the failover">${g}</svg>
      <p class="muted small" style="margin:4px 0 0">Bars = writes acknowledged per 0.5 s, red = errors that reached the application. Hover a bar for details.</p>`;
  }

  function failoverCard(f, title) {
    if (!f) return `<section class="panel panel-pad"><h2>${title}</h2><p class="muted" style="margin:6px 0 0">Not run yet.</p></section>`;
    if (f.error) return `<section class="panel panel-pad"><h2>${title}</h2><p class="muted">${esc(f.error)}</p></section>`;
    const errs = Object.entries(f.write_errors || {}).map(([k, v]) => `${esc(k)} ${v}`).join(", ") || "none";
    return `<section class="panel"><div class="panel-head"><h2>${title}</h2><b>${yes(f.pass, "0 writes lost", "check")}</b></div>
      <div class="stat-line" style="border-top:none"><div><b>${s1(f.election_ms)}</b><span>to a new PRIMARY</span></div><div><b>${num(f.write_gap_ms)} ms</b><span>longest write pause</span></div>
        <div><b>${num(f.acknowledged)} / ${num(f.stored)}</b><span>acknowledged / stored</span></div><div><b>${num(f.lost_acknowledged_writes)}</b><span>acknowledged writes lost</span></div>
        ${f.event === "crash" ? `<div><b>${s1(f.container_running_again_ms)}</b><span>Docker restarted it</span></div><div><b>${s1(f.rejoined_secondary_ms)}</b><span>SECONDARY again</span></div>` : ""}</div>
      <div class="panel-pad" style="padding-top:0">${timeline(f)}
        <div class="kvr"><span>Primary</span><b class="mono small">${esc(f.old_primary)} &rarr; ${esc(f.new_primary)}</b></div>
        <div class="kvr"><span>Election reason (replSetGetStatus)</span><b class="mono small">${esc(f.last_election_reason || "-")}</b></div>
        <div class="kvr"><span>Errors seen by the application</span><b class="small">${errs}</b></div></div></section>`;
  }

  // ================================================================== page
  async function pageLab13(page, route) {
    const tab = route.params.get("tab") || "overview";
    const [st, saved] = await Promise.all([api("/api/lab13/status").catch((e) => ({ available: false, reason: e.message, containers: [] })),
      api("/api/lab13/results").catch(() => ({}))]);
    page.innerHTML = `${intro("Containers &amp; cloud lab 13",
      `Lab 13 deploys MongoDB the way it runs in production: a <b>3-member replica set with keyFile authentication in Docker Compose</b>, an
       <b>exporter sidecar + Prometheus</b> for monitoring, a <b>Kubernetes StatefulSet</b> with persistent volumes, and an <b>Atlas</b> free-tier
       cluster described in Terraform. Following the mentor's rule it is applied to <b>Lab 10</b>: the classifier's billing transactions keep
       running while the primary is stepped down, killed or its pod deleted.`,
      `<div class="row-gap"><a class="btn small" href="/api/lab13/report" target="_blank" rel="noopener">Download PDF report</a>
        <a class="btn small" href="http://localhost:9090" target="_blank" rel="noopener">Prometheus</a></div>`)}
      ${st.available ? statusLine(st) : noProd(st.reason)}
      <nav class="tabs" role="tablist">${[["overview", "Overview"], ["compose", "Compose replica set (13.1)"], ["monitor", "Monitoring (activity)"],
        ["k8s", "Kubernetes (13.2)"], ["billing", "Billing on prod (13 on 10)"], ["compare", "Compose vs Kubernetes"], ["cloud", "Atlas + Terraform"], ["connect", "Connect"]]
        .map(([k, t]) => `<button role="tab" data-tab="${k}" class="${tab === k ? "active" : ""}" aria-selected="${tab === k}">${t}</button>`).join("")}</nav>
      <div id="tabbody"></div>`;
    $$("[data-tab]").forEach((b) => b.addEventListener("click", () => { location.hash = `#/lab13?tab=${b.dataset.tab}`; }));
    ({ overview: overviewTab, compose: composeTab, monitor: monitorTab, k8s: k8sTab, billing: billingTab, compare: compareTab,
       cloud: cloudTab, connect: connectTab }[tab] || overviewTab)($("#tabbody"), st, saved || {});
  }

  function statusLine(st) {
    const rs = st.rs || {};
    const c = (n) => (st.containers || []).find((x) => x.name === n) || {};
    return `<section class="panel" style="margin-top:18px"><div class="stat-line" style="border-top:none">
      ${(rs.members || []).map((m) => `<div><b>${role(m.state)}</b><span>${esc(m.name)}</span></div>`).join("")}
      <div><b>${esc(String(rs.term ?? "-"))}</b><span>election term</span></div>
      <div><b>${dot(c("prometheus").status === "running")}${esc(c("prometheus").status || "-")}</b><span>Prometheus</span></div>
      <div><b>${dot((st.k8s || {}).ready)}${(st.k8s || {}).ready ? "ready" : "not ready"}</b><span>Kubernetes (k3s)</span></div></div></section>`;
  }

  // ------------------------------------------------------------------ overview
  function overviewTab(box, st, saved) {
    const checks = saved.checks || [];
    box.innerHTML = `<div class="grid cols-2" style="grid-template-columns:minmax(0,1.2fr) minmax(0,1fr)">
      <section class="panel"><div class="panel-head"><h2>Checks of the last run</h2><span class="muted">${saved.environment ? esc(String(saved.environment.at || "").slice(0, 19).replace("T", " ")) + " UTC" : ""}</span></div>
        <div class="panel-pad">${checks.length ? checks.map((c) => `<div class="kvr"><span>${esc(c.name)}</span><b>${yes(c.ok)}</b></div>`).join("") : notRun("")}</div></section>
      <section class="panel"><div class="panel-head"><h2>Containers of Lab 13</h2><span class="muted">docker inspect</span></div>
        <div style="overflow-x:auto"><table class="table"><thead><tr><th>Container</th><th>State</th><th class="num">Restarts</th></tr></thead><tbody>
        ${(st.containers || []).map((c) => `<tr><td class="mono small">${esc(c.name)}</td><td>${dot(c.status === "running")}${esc(c.status)}${c.health ? ` <span class="muted small">(${esc(c.health)})</span>` : ""}</td><td class="num">${num(c.restarts || 0)}</td></tr>`).join("")}
        </tbody></table></div></section></div>
      ${explain("What Lab 13 builds", `<ol class="plain">
        <li><b>13.1 Docker Compose:</b> <span class="mono">prod-mongo-1/2/3</span> run <span class="mono">mongod --replSet rs0 --keyFile /etc/mongodb/keyfile</span>. The keyfile is made once with
          <span class="mono">openssl rand -base64 756</span>, owner 999 (the mongodb user), mode 400. <span class="mono">rs.initiate()</span> uses the localhost exception; PyMongo then reads and writes while the primary steps down and while it is killed.</li>
        <li><b>Activity:</b> <span class="mono">prod-mongo-1-exporter</span> is a sidecar - it shares prod-mongo-1's network namespace - and Prometheus scrapes it every 5 s with 4 alert rules.</li>
        <li><b>13.2 Kubernetes:</b> a headless Service, a StatefulSet with one PersistentVolumeClaim per pod and an InitContainer that installs the keyfile from a Secret; pods are deleted to prove self-healing.</li>
        <li><b>13 on 10:</b> the classifier billing (3 free uses, then Rs 100/month) runs on the production set; customer journeys go through failovers and every rupee is checked.</li>
        <li><b>Cloud:</b> the reference Terraform for an Atlas M0 cluster is reviewed and corrected.</li></ol>`)}`;
  }

  // ------------------------------------------------------------------ 13.1
  function composeTab(box, st, saved) {
    const a = saved.lab_13_1 || {};
    const v = a.verify || {};
    const rg = a.rogue;
    box.innerHTML = `<section class="panel panel-pad"><div class="row-gap" style="justify-content:space-between;flex-wrap:wrap"><div>
        <p style="margin:0 0 6px"><b>Step 1-2:</b> the production Compose file and the keyfile (made in a volume, owner 999, mode 400).</p>
        <div class="row-gap">${fileBtn("compose", "docker-compose.production.yml")}${fileBtn("compose-reference", "reference file (mentor)")}</div></div>
        <div class="row-gap"><button class="btn small" id="v-run">Verify again</button><button class="btn small" id="sd-run">Step down the primary</button>
        <button class="btn small primary" id="cr-run">Crash the primary (kill -9)</button></div></div></section>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Step 3 - the three members</h2><span class="muted">docker inspect + rs.status()</span></div><div id="v-out">${v.containers ? members(v) : notRun("")}</div></section>
      ${rg && rg.ran ? `<section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:8px">A member with ANOTHER keyfile tries to join</h2>
        <div class="kvr"><span><span class="mono">${esc(rg.host)}</span> added with rs.add() (priority 0, votes 0)</span><b>${role(rg.state)} health ${esc(String(rg.health))}</b></div>
        <div class="kvr"><span>"Authentication failed" lines in the logs</span><b>${num(rg.auth_failures_logged || 0)}</b></div>
        ${rg.log_sample ? `<pre class="sec-json">${esc(rg.log_sample)}</pre>` : ""}<p class="muted small" style="margin:6px 0 0">The keyfile is the members' shared secret (internal authentication): a server without it cannot replicate, vote or read data. It was removed again afterwards.</p></section>` : ""}
      <div class="grid cols-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr);margin-top:18px" id="fo">
        ${failoverCard(a.failover_stepdown, "Step 4 - rs.stepDown() (planned)")}${failoverCard(a.failover_crash, "Step 4 - kill -9 of the primary (crash)")}</div>
      ${explain("What to explain to the mentor", `<ul class="plain">
        <li><b>--keyFile turns authorization on</b> (the reference's extra <span class="mono">--auth</span> is redundant) and authenticates the members to each other.</li>
        <li>The reference health check logs in as <span class="mono">admin / AdminPass123!</span>, a user that is never created - measured: ${v.reference_healthcheck ? `exit code ${v.reference_healthcheck.exit_code}, <i>${esc(v.reference_healthcheck.output)}</i>` : "see the report"}. Here the health check is a password-less <span class="mono">ping</span>.</li>
        <li>A planned step-down is hidden from the application by retryable writes; a crash costs about <b>electionTimeoutMillis (10 s)</b> before the secondaries call an election.</li>
        <li><b>init: true</b> makes tini PID 1, so a killed mongod ends the container and <b>restart: unless-stopped</b> brings it back; it rejoins as SECONDARY and catches up from the oplog.</li></ul>`)}`;
    const bind = () => {
      $("#v-run").addEventListener("click", (e) => act(e.currentTarget, "Inspecting", async () => { $("#v-out").innerHTML = members(await api("/api/lab13/verify", { method: "POST" })); }));
      [["#sd-run", "stepdown", 0, "Stepping down (25 s)"], ["#cr-run", "crash", 1, "Killing mongod (about 30 s)"]].forEach(([id, ev, i, label]) =>
        $(id).addEventListener("click", (e) => act(e.currentTarget, label, async () => {
          const f = await api("/api/lab13/failover", { method: "POST", body: { event: ev } });
          $("#fo").children[i].outerHTML = failoverCard(f, ev === "crash" ? "kill -9 of the primary (just now)" : "rs.stepDown() (just now)");
          toast(`New primary ${esc(f.new_primary)} after ${s1(f.election_ms)} - ${f.lost_acknowledged_writes} acknowledged writes lost`);
        })));
    };
    bind();
  }

  function members(v) {
    const rs = v.rs || {};
    return `<div style="overflow-x:auto"><table class="table"><thead><tr><th>Member</th><th>rs.status()</th><th>Health</th><th>Restart policy</th><th class="num">Restarts</th><th>Limits</th><th>Keyfile</th></tr></thead><tbody>
      ${(v.containers || []).map((c) => { const m = (rs.members || []).find((x) => x.name === c.host) || {};
        return `<tr><td class="mono small">${esc(c.container)}</td><td>${role(m.state)}</td><td>${dot(c.health === "healthy")}${esc(c.health || "-")}</td><td class="mono small">${esc(c.restart_policy)}</td>
          <td class="num">${num(c.restarts)}</td><td class="small">${num(c.memory_limit_mb)} MB, ${c.cpus} CPU</td>
          <td class="mono small">${esc(c.keyfile.mode)} ${esc(c.keyfile.owner)} &middot; ${esc((c.keyfile.sha256 || "").slice(0, 12))}</td></tr>`; }).join("")}
      </tbody></table></div><div class="panel-pad">${Object.entries(v.checks || {}).map(([k, x]) => `<div class="kvr"><span>${esc(k)}</span><b>${yes(x)}</b></div>`).join("")}</div>`;
  }

  // ------------------------------------------------------------------ monitoring
  async function monitorTab(box, st, saved) {
    const m = saved.activity || {};
    box.innerHTML = `<section class="panel panel-pad"><div class="row-gap" style="justify-content:space-between;flex-wrap:wrap"><p style="margin:0">
        <b>Activity:</b> a Prometheus exporter <b>sidecar</b> for prod-mongo-1 (<span class="mono">network_mode: service:prod-mongo-1</span>), scraped by Prometheus every 5 s.</p>
        <div class="row-gap">${fileBtn("prometheus", "prometheus.yml")}${fileBtn("alerts", "alerts.yml")}<button class="btn small primary" id="m-run">Generate load + measure</button></div></div></section>
      <div class="grid cols-2" style="grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);margin-top:18px">
        <section class="panel"><div class="panel-head"><h2>Operations per second (Prometheus, last 10 min)</h2><span class="muted" id="lm-at"></span></div><div class="panel-pad" id="lm-chart"><span class="spin"></span></div></section>
        <section class="panel"><div class="panel-head"><h2>Live from the sidecar</h2><span class="muted">refreshes every 10 s</span></div><div class="panel-pad" id="lm-now"><span class="spin"></span></div></section></div>
      <div id="m-out" style="margin-top:18px">${m.sidecar ? measured(m) : notRun("")}</div>
      ${explain("Why a sidecar", `<p style="margin:0">A sidecar is a helper container that shares the main container's network namespace: the exporter reaches mongod at
        <span class="mono">127.0.0.1:27017</span> and is reached at <span class="mono">prod-mongo-1:9216</span>, with no extra port on mongod and its own least-privilege
        user (<span class="mono">clusterMonitor</span> + read on local). Kubernetes runs sidecars the same way inside one pod - and restarts them together with the main
        container, which Docker does not (see the restart test below).</p>`)}`;
    $("#m-run").addEventListener("click", (e) => act(e.currentTarget, "Inserting 3,000 documents", async () => { $("#m-out").innerHTML = measured(await api("/api/lab13/monitoring", { method: "POST" })); }));
    let alive = true, timer;
    onLeave(() => { alive = false; clearTimeout(timer); });
    async function live() {
      try {
        const lm = await api("/api/lab13/metrics");
        if (!alive) return;
        const ex = lm.exporter || {};
        const al = lm.alerts || [];
        $("#lm-now").innerHTML = ex.ok ? `<div class="kvr"><span>mongodb_up</span><b>${dot(ex.mongodb_up === 1)}${ex.mongodb_up}</b></div>
          <div class="kvr"><span>metric families / samples</span><b>${num(ex.families)} / ${num(ex.lines)}</b></div>
          <div class="kvr"><span>connections (current)</span><b>${num(ex.connections_current)}</b></div>
          ${Object.entries(ex.members || {}).map(([k, v]) => `<div class="kvr"><span class="mono small">${esc(k)}</span><b>${role(v)}</b></div>`).join("")}
          <div class="kvr"><span>alerts</span><b>${al.length ? al.map((a) => `${esc(a.labels.alertname)} (${esc(a.state)})`).join(", ") : `${dot(true)}none firing`}</b></div>`
          : `<p class="muted" style="margin:0">Exporter not reachable: ${esc(ex.error || "")}</p>`;
        const sr = lm.ops_series || [];
        $("#lm-chart").innerHTML = sr.length ? spark(sr) : `<p class="muted" style="margin:0">No data from Prometheus yet.</p>`;
        $("#lm-at").textContent = new Date().toLocaleTimeString("en-IN", { hour12: false });
      } catch (err) { $("#lm-now").innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
      if (alive) timer = setTimeout(live, 10000);
    }
    live();
  }

  function spark(sr) {
    const W = 600, H = 160, L = 40, B = 22, T = 8;
    const mx = Math.max(1, ...sr.map((p) => p[1]));
    const t0 = sr[0][0], t1 = sr[sr.length - 1][0] || t0 + 1;
    const pts = sr.map(([t, v]) => `${(L + ((W - L - 6) * (t - t0)) / Math.max(t1 - t0, 1)).toFixed(1)},${(T + (H - B - T) * (1 - v / mx)).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 ${W} ${H}" class="lchart" role="img"><line x1="${L}" x2="${W - 6}" y1="${H - B}" y2="${H - B}" class="grid"/>
      <text x="${L - 6}" y="${T + 8}" text-anchor="end" class="ax">${mx.toFixed(0)}</text><text x="${L - 6}" y="${H - B}" text-anchor="end" class="ax">0</text>
      <polyline points="${pts}" fill="none" style="stroke:var(--accent);stroke-width:2"/><text x="${W - 6}" y="${H - 4}" text-anchor="end" class="ax">now</text>
      <text x="${L}" y="${H - 4}" class="ax">-10 min</text></svg><p class="muted small" style="margin:4px 0 0">rate(opcounters) + rate(opcountersRepl) of prod-mongo-1 (a secondary counts replicated inserts in opcountersRepl).</p>`;
  }

  function measured(m) {
    const sc = m.scrape || {}, rt = m.restart_test;
    return `<div class="grid cols-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
      <section class="panel panel-pad"><h2 style="margin-bottom:8px">Measured</h2>
        <div class="kvr"><span>sidecar NetworkMode</span><b class="mono small">${esc((m.sidecar || {}).network_mode || "").slice(0, 26)}&hellip;</b></div>
        <div class="kvr"><span>shares the network namespace of</span><b>${yes((m.sidecar || {}).shares_netns_with, esc((m.sidecar || {}).shares_netns_with || ""), "-")}</b></div>
        <div class="kvr"><span>exporter scrape</span><b>${num(sc.lines)} samples in ${num(sc.ms)} ms</b></div>
        <div class="kvr"><span>Prometheus targets</span><b class="small">${(m.targets || []).map((t) => `${dot(t.health === "up")}${esc(t.job)}`).join(" ")}</b></div>
        <div class="kvr"><span>insert counter before &rarr; after 3,000 inserts</span><b>${num(m.inserts_before)} &rarr; ${num(m.inserts_after)}</b></div>
        <div class="kvr"><span>insert rate (30 s window)</span><b>${m.insert_rate_per_s ?? "-"} / s</b></div>
        <div class="kvr"><span>alert rules loaded</span><b class="small">${(m.rules || []).map((r) => esc(r.name)).join(", ")}</b></div></section>
      <section class="panel panel-pad"><h2 style="margin-bottom:8px">Sidecar vs. a restart of its main container</h2>
        ${rt ? `<div class="kvr"><span>docker restart prod-mongo-1</span><b>back after ${rt.main_back_s} s</b></div>
          <div class="kvr"><span>sidecar scrape right after</span><b>${rt.sidecar_broken ? `${dot(false)}fails` : `${dot(true)}works`}</b></div>
          ${rt.sidecar_broken ? `<div class="kvr"><span>docker restart prod-mongo-1-exporter</span><b>${yes(rt.sidecar_ok_after_restart, `OK in ${rt.sidecar_fix_s} s`)}</b></div>` : ""}
          <div class="kvr"><span>Prometheus up{job="mongodb"} around it</span><b class="mono small">${(rt.prometheus_up || []).map((p) => p[1]).join("")}</b></div>
          <p class="muted small" style="margin:8px 0 0">In Docker the sidecar joins the network namespace of a specific container instance; Kubernetes keeps one namespace per pod and restarts the pod's containers together.</p>`
        : `<p class="muted" style="margin:0">Runs in the full lab (it restarts prod-mongo-1).</p>`}</section></div>`;
  }

  // ------------------------------------------------------------------ 13.2 Kubernetes
  async function k8sTab(box, st, saved) {
    const k = saved.lab_13_2 || {};
    box.innerHTML = `<section class="panel panel-pad"><div class="row-gap" style="justify-content:space-between;flex-wrap:wrap"><p style="margin:0">
        Kubernetes runs in the <span class="mono">k3s</span> container (one node). Namespace <span class="mono">lab13</span>: headless Service, StatefulSet <span class="mono">mongo</span>, client pod.</p>
        <div class="row-gap">${fileBtn("service", "mongo-headless-svc.yaml")}${fileBtn("statefulset", "mongo-statefulset.yaml")}${fileBtn("classroom", "classroom variant")}
        <button class="btn small primary" id="k-del">kubectl delete pod mongo-2</button></div></div></section>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Live: pods, volumes, replica set</h2><span class="muted" id="k-at"></span></div><div id="k-live"><div class="panel-pad"><span class="spin"></span></div></div></section>
      <div id="k-res" style="margin-top:18px">${k.resiliency ? resil(k.resiliency) : ""}</div>
      ${k.failover && !k.failover.error ? k8sFail(k.failover) : ""}
      ${k.classroom_variant && !k.classroom_variant.error ? classroom(k.classroom_variant) : ""}
      ${!k.deploy ? notRun("") : k.deploy.error ? `<div class="panel empty"><strong>The Kubernetes part did not complete</strong>${esc(k.deploy.error)}</div>` : ""}
      ${explain("How the StatefulSet answers the solution", `<ol class="plain">
        <li><b>Headless Service</b> (<span class="mono">clusterIP: None</span>, selector <span class="mono">app: mongo</span>) gives <span class="mono">mongo-N.mongo-headless-svc</span> - the names used in <span class="mono">rs.initiate()</span>.</li>
        <li><b>StatefulSet</b> with <span class="mono">replicas: 3</span>, <span class="mono">serviceName: mongo-headless-svc</span> and a <b>volumeClaimTemplate</b>: PVC data-mongo-N follows pod N for ever.</li>
        <li><b>InitContainer</b>: the keyfile is generated <i>once</i> into a Secret and the InitContainer installs it in an in-memory emptyDir (owner 999, mode 400). Generating it inside each pod, as the text suggests, gives every pod a different key - tested below.</li>
        <li><b>Readiness probe</b>: TCP 27017. The Service publishes not-ready addresses so members can find each other while starting.</li></ol>`)}`;
    $("#k-del").addEventListener("click", (e) => act(e.currentTarget, "Deleting mongo-2, waiting for it", async () => {
      $("#k-res").innerHTML = resil(await api("/api/lab13/k8s/delete-pod", { method: "POST", body: { pod: "mongo-2" } }));
    }));
    let alive = true, timer;
    onLeave(() => { alive = false; clearTimeout(timer); });
    async function live() {
      try {
        const s = await api("/api/lab13/k8s");
        if (!alive) return;
        const rsm = (s.rs || {}).members || [];
        $("#k-live").innerHTML = !s.ready ? `<p class="muted panel-pad" style="margin:0">${esc(s.reason || "Kubernetes not ready")}</p>` : `<div style="overflow-x:auto"><table class="table"><thead><tr><th>Pod</th><th>Phase</th><th>Ready</th><th class="num">Restarts</th><th>IP</th><th>Replica set</th><th>PVC</th></tr></thead><tbody>
          ${(s.pods || []).map((p) => { const m = rsm.find((x) => x.name.startsWith(p.pod + ".")); const v = (s.pvcs || []).find((x) => x.name === "data-" + p.pod);
            return `<tr><td class="mono">${esc(p.pod)}</td><td>${esc(p.phase)}</td><td>${dot(p.ready)}</td><td class="num">${num(p.restarts)}</td><td class="mono small">${esc(p.ip || "-")}</td><td>${m ? role(m.state) : "-"}</td><td class="mono small">${v ? esc(v.name + " " + v.status + " " + v.size) : "-"}</td></tr>`; }).join("")}
          </tbody></table></div>${(s.events || []).length ? `<details class="panel-pad"><summary class="muted">Recent events (${s.events.length})</summary><pre class="sec-json">${esc(s.events.map((x) => `${String(x.at || "").slice(11, 19)} ${x.object} ${x.reason}: ${x.message}`).join("\n"))}</pre></details>` : ""}`;
        $("#k-at").textContent = new Date().toLocaleTimeString("en-IN", { hour12: false });
      } catch (err) { $("#k-live").innerHTML = `<p class="muted panel-pad">${esc(err.message)}</p>`; }
      if (alive) timer = setTimeout(live, 6000);
    }
    live();
  }

  const resil = (r) => r.error ? `<div class="panel empty">${esc(r.error)}</div>` : `<section class="panel"><div class="panel-head"><h2>Test resiliency: kubectl delete pod ${esc(r.pod)}</h2><b>${yes(r.pass)}</b></div>
    <div class="stat-line" style="border-top:none"><div><b>${r.timeline_s.recreated ?? "-"} s</b><span>new pod created</span></div><div><b>${r.timeline_s.ready ?? "-"} s</b><span>Ready</span></div>
      <div><b>${r.timeline_s.secondary ?? "-"} s</b><span>SECONDARY again</span></div><div><b>${num(r.after.count_on_pod)}</b><span>documents on ${esc(r.pod)} after</span></div></div>
    <div class="panel-pad" style="padding-top:0"><table class="table"><thead><tr><th></th><th>Pod uid</th><th>PVC uid</th><th>Volume</th><th class="num">Documents</th></tr></thead><tbody>
      ${["before", "after"].map((k) => `<tr><td>${k}</td><td class="mono small">${esc(r[k].pod_uid)}</td><td class="mono small">${esc(r[k].pvc_uid)}</td><td class="mono small">${esc(r[k].volume || "")}</td><td class="num">${num(r[k].count_on_pod)}</td></tr>`).join("")}</tbody></table>
    <p class="muted small" style="margin:8px 0 0">A new pod (new uid) with the same name, the same PVC and its data: it rejoined by catching up from the oplog, no initial sync.</p></div></section>`;

  function k8sFail(f) {
    const c = f.client || {}, b = f.balances || {};
    const ps = f.per_second || [], mx = Math.max(1, ...ps);
    const bars = ps.map((v, i) => `<rect x="${i * 12}" y="${60 - (56 * v) / mx}" width="10" height="${(56 * v) / mx || 2}" style="fill:${v ? "var(--accent)" : "var(--mal)"}"><title>${i} s: ${v} transfers</title></rect>`).join("");
    return `<section class="panel" style="margin-top:18px"><div class="panel-head"><h2>13.2 on 10: the PRIMARY pod deleted during transactions</h2><b>${yes(f.pass, "A + B unchanged")}</b></div>
      <div class="stat-line" style="border-top:none"><div><b>${esc(f.deleted_primary)}</b><span>deleted (PRIMARY)</span></div><div><b>${f.new_primary_s ?? "-"} s</b><span>new PRIMARY ${esc(f.new_primary || "")}</span></div>
        <div><b>${num(c.committed)}</b><span>transfers committed</span></div><div><b>${num(c.max_gap_ms)} ms</b><span>longest pause</span></div>
        <div><b>${num(b.A)} + ${num(b.B)}</b><span>= ${num((b.A || 0) + (b.B || 0))} (was 2,000)</span></div><div><b>${num(b.ledger)}</b><span>ledger entries</span></div></div>
      <div class="panel-pad" style="padding-top:0"><svg viewBox="0 0 ${Math.max(ps.length * 12, 100)} 62" class="lchart" role="img">${bars}</svg>
      <p class="muted small" style="margin:4px 0 0">Transfers per second from the mongo-client pod (red = none). After an error the client looks up its ledger entry before retrying, so no transfer is applied twice.</p></div></section>`;
  }

  const classroom = (c) => `<section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:8px">The solution text taken literally: keyfile generated in every pod</h2>
    <div class="kvr"><span>keyfile sha256 per pod</span><b class="mono small">${esc(Object.entries(c.keyfile_sha256 || {}).map(([k, v]) => `${k} ${v}`).join("  "))}</b></div>
    <div class="kvr"><span>rs.initiate()</span><b>${dot(false)}${esc((c.initiate || {}).codeName || "")} (${esc(String((c.initiate || {}).code || ""))})</b></div>
    <pre class="sec-json">${esc((c.initiate || {}).message || "")}</pre><p class="muted small" style="margin:6px 0 0">Expected and measured: ${yes(c.pass, "fails as predicted", "unexpected")}. An emptyDir is private to one pod - the key must come from one Secret.</p></section>`;

  // ------------------------------------------------------------------ 13 on 10
  function billingTab(box, st, saved) {
    const b = saved.billing || {};
    const jt = (j, name) => !j ? "" : j.error ? `<div class="kvr"><span>${name}</span><b>${esc(j.error)}</b></div>` : `<tr><td>${name}</td><td class="num">${num(j.journeys_ok)} / ${num(j.users)}</td><td class="num">${num(j.app_retries)}</td><td class="num">${num(j.recovered_committed)}</td>
      <td>${yes(j.invariants.money_conserved, "conserved")}</td><td>${yes(j.invariants.revenue_matches, "matches")}</td><td>${yes(j.pass)}</td></tr>`;
    const ac = b.acid || {};
    box.innerHTML = `<section class="panel panel-pad"><div class="row-gap" style="justify-content:space-between;flex-wrap:wrap"><p style="margin:0;max-width:760px">
        The <b>Lab 10 classifier billing</b> (3 free uses, then Rs 100 per month from a wallet, every change one ACID transaction) on the production replica set, as the
        least-privilege user <span class="mono">billing_app</span>. Each journey: sign in, 3 free uses, a refused 4th, top-up Rs 200, subscribe Rs 100, 2 uses on the plan.</p>
        <div class="row-gap"><button class="btn small" id="b-sd">Journeys through a step-down</button><button class="btn small primary" id="b-cr">Journeys through a crash</button></div></div></section>
      ${b.copy && b.copy.collections ? `<section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Copied from the ${esc(b.copy.source || "")}</h2><b>${yes(b.copy.verified, "verified")}</b></div>
        <table class="table"><thead><tr><th>Collection</th><th class="num">Source</th><th class="num">Copied</th></tr></thead><tbody>${Object.entries(b.copy.collections).map(([k, v]) => `<tr><td class="mono">cti_billing.${esc(k)}</td><td class="num">${num(v.source)}</td><td class="num">${num(v.copied)}</td></tr>`).join("")}</tbody></table></section>` : notRun("")}
      ${ac.free_trial_race ? `<section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:8px">Lab 10 ACID checks on the production set</h2>
        <div class="kvr"><span>A+I: 10 simultaneous uses by a new user</span><b>${ac.free_trial_race.granted} granted, ${ac.free_trial_race.refused} refused ${yes(ac.free_trial_race.pass, "")}</b></div>
        <div class="kvr"><span>A: crash after the wallet debit</span><b>wallet ${ac.crash_after_debit.wallet_before} &rarr; ${ac.crash_after_debit.wallet_after} ${yes(ac.crash_after_debit.pass, "")}</b></div>
        <div class="kvr"><span>C: double click on Pay (same idempotency key)</span><b>${esc(ac.double_click.responses.join(", "))} ${yes(ac.double_click.pass, "")}</b></div>
        <div class="kvr"><span>D: majority + journal</span><b>${yes(ac.durability.pass, "yes")}</b></div></section>` : ""}
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Customer journeys THROUGH a failover</h2><span class="muted">expected per user: wallet Rs 100, 3 free + 2 plan uses, 1 refusal, 2 payments</span></div>
        <div style="overflow-x:auto"><table class="table"><thead><tr><th>Failover</th><th class="num">Journeys exactly right</th><th class="num">App retries</th><th class="num">Found committed</th><th>Money</th><th>Revenue</th><th>Result</th></tr></thead>
        <tbody id="b-rows">${jt(b.stepdown, "rs.stepDown()")}${jt(b.crash, "kill -9 of the primary")}</tbody></table></div></section>
      ${explain("Finding", `<p style="margin:0">Lab 10's <span class="mono">run_txn</span> retries 5 times with 0.1-1.6 s back-off (about 3 s); a crash needs about 11 s to elect a new primary, so the
        transaction gives up first. Recovery therefore needs an application-level retry - and it is only safe because every operation can be recognised afterwards:
        the payment's idempotency key and an operation id stored with each use. After an error the app looks for that record first; if it exists the operation had committed and is not repeated.</p>`)}`;
    [["#b-sd", "stepdown", "rs.stepDown() (just now)"], ["#b-cr", "crash", "kill -9 (just now)"]].forEach(([id, ev, name]) =>
      $(id).addEventListener("click", (e) => act(e.currentTarget, "16 customers paying through a failover", async () => {
        const j = await api("/api/lab13/billing", { method: "POST", body: { event: ev, users: 16 } });
        $("#b-rows").insertAdjacentHTML("afterbegin", jt(j, name));
        toast(`${j.journeys_ok}/${j.users} journeys exactly right, ${j.app_retries} retries`);
      })));
  }

  // ------------------------------------------------------------------ compare
  function compareTab(box, st, saved) {
    const c = saved.compare || [];
    box.innerHTML = c.length ? `<section class="panel"><div class="panel-head"><h2>Docker Compose vs Kubernetes</h2><span class="muted">numbers are from the last run</span></div>
      <div style="overflow-x:auto"><table class="table"><thead><tr><th>Aspect</th><th>Docker Compose (13.1)</th><th>Kubernetes StatefulSet (13.2)</th></tr></thead><tbody>
      ${c.map((r) => `<tr><td><b>${esc(r.aspect)}</b></td><td class="small">${esc(r.compose)}</td><td class="small">${esc(r.k8s)}</td></tr>`).join("")}</tbody></table></div></section>
      ${explain("In one sentence", `<p style="margin:0">Compose is simpler on one machine; Kubernetes adds self-healing across machines, stable identities and volume management in exchange for much more configuration.</p>`)}` : notRun("");
  }

  // ------------------------------------------------------------------ cloud
  function cloudTab(box, st, saved) {
    const c = saved.cloud || {};
    const v = (x) => !x ? "-" : x.ran ? `${yes(x.valid, "valid", "invalid")} <span class="muted small">${x.errors} errors, ${x.warnings} warnings, mongodbatlas ${esc(x.provider || "")}</span>` : `<span class="muted small">${esc(x.reason)}</span>`;
    box.innerHTML = `<section class="panel panel-pad"><div class="row-gap" style="justify-content:space-between;flex-wrap:wrap"><p style="margin:0">
        The mentor's <span class="mono">main.tf</span> for an Atlas free-tier cluster, reviewed and corrected.</p><div class="row-gap">${fileBtn("terraform-reference", "reference main.tf")}${fileBtn("terraform", "corrected main.tf")}</div></div></section>
      ${c.review ? `<section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Review of the reference file</h2></div><div style="overflow-x:auto"><table class="table"><thead><tr><th>Issue</th><th>Found</th><th>Why it matters</th><th>Corrected file</th></tr></thead><tbody>
        ${c.review.map((r) => `<tr><td><b>${esc(r.issue)}</b></td><td>${r.found ? "yes" : "no"}</td><td class="small">${esc(r.why)}</td><td class="small mono">${esc(r.fix)}</td></tr>`).join("")}</tbody></table></div></section>
      <section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:8px">terraform validate</h2>
        <div class="kvr"><span>reference main.tf</span><b>${v(c.validate_reference)}</b></div><div class="kvr"><span>corrected main.tf</span><b>${v(c.validate_corrected)}</b></div>
        <div class="kvr"><span>Live Atlas test</span><b class="small">${(c.atlas || {}).connected ? `connected in ${c.atlas.connect_ms} ms, insert w:majority ${esc(JSON.stringify(c.atlas.insert_ms_wmajority))} ms` : esc((c.atlas || {}).reason || (c.atlas || {}).error || "-")}</b></div>
        <p class="muted small" style="margin:8px 0 0">validate checks syntax and the provider schema; the M0 = TENANT rule and the broken URI output only fail at apply time, which is why they were found by reading.</p></section>` : notRun("")}
      ${explain("Use it", `${copyCmd("cd labs/lab13/terraform && cp terraform.tfvars.example terraform.tfvars && terraform init && terraform apply")}
        <p class="muted" style="margin:8px 0 0">Then put <span class="mono">terraform output -raw cluster_uri</span> into <span class="mono">.env</span> as <span class="mono">ATLAS_URI=...</span> and run <span class="mono">docker compose exec lab13 lab13 cloud</span>.</p>`)}`;
  }

  // ------------------------------------------------------------------ connect
  function connectTab(box) {
    box.innerHTML = `<section class="panel panel-pad"><h2 style="margin-bottom:8px">Production replica set (13.1)</h2>
      ${copyCmd("docker compose exec lab13 lab13 shell admin")}${copyCmd("docker compose exec lab13 lab13 password admin")}
      <p class="muted" style="margin:10px 0 8px">From your computer (Compass): one member at a time with directConnection - the member names prod-mongo-N only resolve inside Docker.</p>
      ${copyCmd("mongodb://admin:<password>@localhost:27051/?directConnection=true&authSource=admin")}
      <p class="muted" style="margin:10px 0 8px">Failover tests on the command line:</p>${copyCmd("docker compose exec lab13 lab13 stepdown")}${copyCmd("docker compose exec lab13 lab13 crash")}</section>
      <section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:8px">Kubernetes (13.2)</h2>
      ${copyCmd("docker compose exec lab13 lab13 pods")}${copyCmd("docker compose exec lab13 lab13 kubectl -n lab13 describe statefulset mongo")}
      ${copyCmd("docker compose exec lab13 lab13 k8s-shell mongo-0")}${copyCmd("docker compose exec lab13 lab13 delete-pod mongo-2")}
      <p class="muted" style="margin:10px 0 8px">kubectl on your computer: copy the kubeconfig and point it at localhost:6443.</p>
      ${copyCmd("docker compose cp k3s:/output/kubeconfig.yaml ./kubeconfig.yaml && kubectl --kubeconfig ./kubeconfig.yaml -n lab13 get pods,pvc,svc")}</section>
      <section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:8px">Monitoring</h2>
      ${copyCmd("http://localhost:9090/graph?g0.expr=mongodb_rs_members_state")}${copyCmd("curl -s http://localhost:9216/metrics | grep mongodb_up")}</section>`;
  }

  return { lab13: pageLab13 };
};

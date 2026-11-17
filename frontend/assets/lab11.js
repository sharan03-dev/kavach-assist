/* Kavach CTI - Sharding lab 11. Loaded before labs.js; labs.js calls window.KavachLab11(helpers) and adds the
   returned route. Everything shown here comes from the real sharded cluster through /api/lab11/*. */
window.KavachLab11 = (h) => {
  "use strict";
  const { $, $$, esc, num, fmtTime, api, toast, copyCmd, onLeave, intro, explain, act, dot } = h;
  const P = (x, d = 0) => `${(100 * x).toFixed(d)}%`;
  const reduced = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SHARD_COLOR = ["var(--accent)", "var(--low)"];
  const noCluster = (reason) => `<div class="banner err" style="margin:18px 0"><div><strong>Sharded cluster not available.</strong> ${esc(reason || "")}
    <div style="margin-top:6px">Lab 11 runs on its own cluster (mongos router, config server replica set, two shards). Start it, together with Labs 7 to 10, with:</div>${copyCmd("docker compose up -d lab11")}</div></div>`;
  const empty = (title, body) => `<div class="panel empty"><strong>${title}</strong>${body}</div>`;

  // ------------------------------------------------------------------ small SVG charts
  function lineChart(series, labels, { w = 640, hgt = 240, unit = "", xTitle = "" } = {}) {
    const all = series.flatMap((s) => s.values);
    const max = Math.max(...all, 1);
    const L = 54, B = 34, T = 12, R = 24, pw = w - L - R, ph = hgt - B - T;
    const x = (i) => L + (labels.length > 1 ? (pw * i) / (labels.length - 1) : pw / 2);
    const y = (v) => T + ph * (1 - v / max);
    let g = "";
    [0, 0.25, 0.5, 0.75, 1].forEach((f) => {
      g += `<line x1="${L}" x2="${w - R}" y1="${y(max * f)}" y2="${y(max * f)}" class="grid"/><text x="${L - 6}" y="${y(max * f) + 4}" text-anchor="end" class="ax">${num(Math.round(max * f))}${unit}</text>`;
    });
    labels.forEach((l, i) => { g += `<text x="${x(i)}" y="${hgt - 18}" text-anchor="middle" class="ax">${esc(l)}</text>`; });
    if (xTitle) g += `<text x="${L + pw / 2}" y="${hgt - 3}" text-anchor="middle" class="ax">${esc(xTitle)}</text>`;
    series.forEach((s) => {
      g += `<polyline fill="none" stroke="${s.color}" stroke-width="2.2" ${s.dash ? 'stroke-dasharray="5 4"' : ""} points="${s.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}"><title>${esc(s.name)}</title></polyline>`;
      s.values.forEach((v, i) => { g += `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="2.6" fill="${s.color}"><title>${esc(s.name)}: ${num(v)}</title></circle>`; });
    });
    return `<svg viewBox="0 0 ${w} ${hgt}" class="lchart" role="img" aria-label="line chart">${g}</svg>
      <div class="lg">${series.map((s) => `<span><i style="${s.dash ? `background:transparent;border:2px dashed ${s.color}` : `background:${s.color}`}"></i>${esc(s.name)}</span>`).join("")}</div>`;
  }

  function latencyChart(points, events, { w = 640, hgt = 220 } = {}) {
    if (!points || !points.length) return "";
    const tmax = Math.max(...points.map((p) => p[0]), 1), vmax = Math.max(...points.map((p) => p[1]), 1);
    const L = 50, B = 30, T = 14, R = 12, pw = w - L - R, ph = hgt - B - T;
    const x = (t) => L + (pw * t) / tmax, y = (v) => T + ph * (1 - v / vmax);
    let g = "";
    [0, 0.5, 1].forEach((f) => { g += `<line x1="${L}" x2="${w - R}" y1="${y(vmax * f)}" y2="${y(vmax * f)}" class="grid"/><text x="${L - 6}" y="${y(vmax * f) + 4}" text-anchor="end" class="ax">${Math.round(vmax * f)} ms</text>`; });
    (events || []).filter((e) => e.source === "coordinator" && ["cloning", "applying", "blocking-writes", "committing"].includes(e.state)).forEach((e, i) => {
      g += `<line x1="${x(e.t)}" x2="${x(e.t)}" y1="${T}" y2="${T + ph}" stroke="var(--accent)" stroke-dasharray="3 3"/><text x="${x(e.t) + 4}" y="${T + 10 + 12 * (i % 2)}" class="ax">${esc(e.state)}</text>`;
    });
    g += `<polyline fill="none" stroke="var(--low)" stroke-width="1.2" points="${points.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ")}"/>`;
    const worst = points.reduce((a, b) => (b[1] > a[1] ? b : a));
    g += `<circle cx="${x(worst[0])}" cy="${y(worst[1])}" r="4" fill="var(--mal)"><title>${worst[1]} ms</title></circle><text x="${x(worst[0]) - 6}" y="${y(worst[1]) + 4}" text-anchor="end" class="ax val">${worst[1]} ms</text>`;
    g += `<text x="${L + pw / 2}" y="${hgt - 8}" text-anchor="middle" class="ax">seconds since the writer started (one insert every 20 ms)</text>`;
    return `<svg viewBox="0 0 ${w} ${hgt}" class="lchart" role="img" aria-label="insert latency during resharding">${g}</svg>`;
  }

  const split = (by) => {
    const keys = Object.keys(by || {}), tot = keys.reduce((s, k) => s + by[k], 0) || 1;
    return `<div class="split" title="${keys.map((k) => `${k}: ${num(by[k])}`).join(", ")}">${keys.map((k, i) => `<i style="width:${(100 * by[k]) / tot}%;background:${SHARD_COLOR[i % 2]}"></i>`).join("")}</div>`;
  };
  const shardLegend = (names) => `<div class="lg" style="justify-content:flex-start">${names.map((n, i) => `<span><i style="background:${SHARD_COLOR[i % 2]}"></i>${esc(n)}</span>`).join("")}<span><i style="background:transparent;border-left:2px dashed var(--mal);border-radius:0"></i>50 %</span></div>`;

  // ------------------------------------------------------------------ cluster diagram (the mentor's picture, live)
  function topo(st) {
    const shards = st.available ? st.shards : [{ name: "shardA", members: [] }, { name: "shardB", members: [] }];
    const docs = {};
    (st.collections || []).forEach((c) => Object.entries(c.docs || {}).forEach(([k, v]) => { docs[k] = (docs[k] || 0) + v; }));
    const cfg = st.available ? st.config : { name: "cfgrs", members: [] };
    const member = (m, x, y) => `<g><rect x="${x}" y="${y}" width="300" height="30" rx="6" class="member ${m.state === "PRIMARY" ? "p" : ""}"/>
      <text x="${x + 12}" y="${y + 20}" class="mono">${esc(m.name)}</text><text x="${x + 288}" y="${y + 20}" text-anchor="end" class="role-t ${m.state === "PRIMARY" ? "p" : m.state === "SECONDARY" ? "s" : "x"}">${esc(m.state.toLowerCase())}</text></g>`;
    const shardBox = (s, i) => {
      const x = i === 0 ? 20 : 420;
      return `<g class="shard" data-shard="${esc(s.name)}"><rect x="${x}" y="196" width="320" height="${s.members.length ? 46 + 36 * s.members.length : 90}" rx="10" class="box"/>
        <text x="${x + 14}" y="219" class="b">${esc(s.name)}</text><text x="${x + 306}" y="219" text-anchor="end" class="sub">${docs[s.name] != null ? num(docs[s.name]) + " documents" : "replica set"}</text>
        ${s.members.map((m, j) => member(m, x + 10, 232 + 36 * j)).join("") || `<text x="${x + 14}" y="250" class="sub">not running</text>`}</g>`;
    };
    return `<svg viewBox="0 0 760 ${196 + 52 + 36 * Math.max(2, ...shards.map((s) => s.members.length))}" class="topo" role="img" aria-label="Sharded cluster topology">
      <path d="M380 46 V82" class="link"/><path d="M380 128 C380 160 180 160 180 196" class="link"/><path d="M380 128 C380 160 580 160 580 196" class="link"/>
      <path d="M470 105 H540" class="link cfg"/><text x="505" y="98" text-anchor="middle" class="sub">chunk map</text>
      <rect x="300" y="8" width="160" height="38" rx="8" class="box"/><text x="380" y="32" text-anchor="middle" class="b">Application</text>
      <rect x="290" y="82" width="180" height="46" rx="10" class="box router"/><text x="380" y="103" text-anchor="middle" class="b">mongos router</text><text x="380" y="119" text-anchor="middle" class="sub mono">localhost:27030</text>
      <rect x="540" y="78" width="200" height="56" rx="10" class="box cfg"/><text x="640" y="99" text-anchor="middle" class="b">Config servers</text>
      <text x="640" y="118" text-anchor="middle" class="sub mono">${esc(cfg.name)}: ${esc((cfg.members || []).map((m) => m.name).join(", ") || "-")}</text>
      ${shards.slice(0, 2).map(shardBox).join("")}</svg>`;
  }

  // mini router picture used by the hotspot demo: particles fly to the shard that really received the inserts
  function routerMini(id, names, counts) {
    const [a, b] = names;
    return `<svg viewBox="0 0 360 170" class="router-mini" id="${id}" role="img" aria-label="Where mongos routed the documents">
      <path d="M180 44 C180 80 80 80 80 108" class="link" id="${id}-pa"/><path d="M180 44 C180 80 280 80 280 108" class="link" id="${id}-pb"/>
      <rect x="125" y="10" width="110" height="34" rx="8" class="box router"/><text x="180" y="32" text-anchor="middle" class="b">mongos</text>
      <g class="bin" data-s="${esc(a)}"><rect x="20" y="108" width="120" height="54" rx="9" class="box"/><text x="80" y="128" text-anchor="middle" class="b">${esc(a)}</text><text x="80" y="149" text-anchor="middle" class="cnt">${num(counts[a] || 0)}</text></g>
      <g class="bin" data-s="${esc(b)}"><rect x="220" y="108" width="120" height="54" rx="9" class="box"/><text x="280" y="128" text-anchor="middle" class="b">${esc(b)}</text><text x="280" y="149" text-anchor="middle" class="cnt">${num(counts[b] || 0)}</text></g>
      <g class="fx"></g></svg>`;
  }
  function flyParticles(svgId, names, added) {
    const svg = document.getElementById(svgId);
    if (!svg) return;
    const total = Object.values(added).reduce((s, v) => s + v, 0) || 1;
    names.forEach((n, i) => {
      const bin = svg.querySelector(`.bin[data-s="${CSS.escape(n)}"]`);
      if (bin && added[n] > 0) { bin.classList.remove("hit"); void bin.getBBox(); bin.classList.add("hit"); setTimeout(() => bin.classList.remove("hit"), 1600); }
      if (reduced()) return;
      const k = Math.round((14 * (added[n] || 0)) / total);
      const path = svg.querySelector(`#${svgId}-p${i === 0 ? "a" : "b"}`).getAttribute("d");
      const fx = svg.querySelector(".fx");
      for (let j = 0; j < k; j++) {
        const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        c.setAttribute("r", "4"); c.setAttribute("class", "particle");
        c.style.fill = SHARD_COLOR[i];
        const m = document.createElementNS("http://www.w3.org/2000/svg", "animateMotion");
        m.setAttribute("dur", "0.8s"); m.setAttribute("begin", "indefinite"); m.setAttribute("fill", "freeze"); m.setAttribute("path", path);
        c.setAttribute("cx", "-20"); c.setAttribute("cy", "-20");
        c.appendChild(m); fx.appendChild(c);
        setTimeout(() => { try { m.beginElement(); } catch (err) { /* old browser */ } }, j * 60);
        setTimeout(() => c.remove(), 1600 + j * 60);
      }
    });
  }

  // ================================================================== page
  async function pageLab11(page, route) {
    const tab = route.params.get("tab") || "cluster";
    const [st, saved] = await Promise.all([api("/api/lab11/status").catch((e) => ({ available: false, reason: e.message })),
      api("/api/lab11/results").catch(() => ({}))]);
    page.innerHTML = `${intro("Sharding lab 11",
      `Sharding splits one collection over several replica sets, the <b>shards</b>, by ranges of a <b>shard key</b>.
       The <b>mongos</b> router sends each query only to the shards that hold the matching ranges, so a good key spreads the writes
       and keeps most queries on one shard. Everything on this page is measured on a real two-shard cluster.`,
      `<div class="row-gap"><a class="btn small" href="/api/lab11/report" target="_blank" rel="noopener">Download PDF report</a></div>`)}
      ${st.available ? "" : noCluster(st.reason)}
      <nav class="tabs" role="tablist">${[["cluster", "Cluster"], ["keys", "Shard keys (11.1)"], ["hotspot", "Hotspot detection (11.2)"], ["reshard", "Resharding (activity)"], ["billing", "Billing on shards (11 on 10)"]]
        .map(([k, t]) => `<button role="tab" data-tab="${k}" class="${tab === k ? "active" : ""}" aria-selected="${tab === k}">${t}</button>`).join("")}</nav>
      <div id="tabbody"></div>`;
    $$("[data-tab]").forEach((b) => b.addEventListener("click", () => { location.hash = `#/lab11?tab=${b.dataset.tab}`; }));
    const billingTab = (b) => (window.KavachBilling ? window.KavachBilling.lab11Panel(b, h) : (b.innerHTML = ""));
    ({ cluster: clusterTab, keys: keysTab, hotspot: hotTab, reshard: reshardTab, billing: billingTab }[tab] || clusterTab)($("#tabbody"), st, saved || {});
  }

  // ------------------------------------------------------------------ tab: cluster
  function clusterTab(box, st0) {
    let alive = true;
    onLeave(() => { alive = false; });
    box.innerHTML = `<section class="panel panel-pad" id="topo-wrap">${topo(st0)}</section>
      <div id="cl-stats"></div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Sharded collections</h2><span class="muted">chunks from config.chunks, documents from $collStats</span></div><div id="cl-colls"></div></section>
      ${explain("How a request travels", `<ol class="plain">
        <li>The application connects only to <span class="mono">mongos</span>. It never needs to know how many shards exist.</li>
        <li>mongos keeps a copy of the <b>chunk map</b> from the config servers: which range of shard-key values lives on which shard.</li>
        <li>If the filter contains the shard key, mongos sends the operation only to the shard(s) owning that range (<b>targeted</b>). Otherwise it asks every shard and merges the answers (<b>scatter-gather</b>).</li>
        <li>Each shard is a replica set, so a shard keeps working when one of its members fails (Lab 8), and writes are acknowledged as in Lab 9.</li></ol>
        <p style="margin:10px 0 6px">Look at it yourself with mongosh or Compass (connect to the router):</p>${copyCmd("mongosh mongodb://localhost:27030 --eval \"sh.status()\"")}`)}`;
    const paint = (st) => {
      if (!alive) return;
      $("#topo-wrap").innerHTML = topo(st);
      if (!st.available) { $("#cl-stats").innerHTML = ""; $("#cl-colls").innerHTML = `<p class="muted panel-pad" style="margin:0">${esc(st.reason || "")}</p>`; return; }
      $("#cl-stats").innerHTML = `<section class="panel" style="margin-top:18px"><div class="stat-line" style="border-top:none">
        <div><b>${esc(st.mongos_version)}</b><span>MongoDB version (mongos)</span></div><div><b>${st.shards.length}</b><span>shards, each a replica set</span></div>
        <div><b>${st.shards.reduce((s, x) => s + x.members.length, 0) + st.config.members.length}</b><span>mongod processes</span></div>
        <div><b>${esc(st.balancer.mode)}</b><span>balancer mode</span></div><div><b>${st.collections.length}</b><span>sharded collections</span></div></div></section>`;
      const names = st.shards.map((s) => s.name);
      $("#cl-colls").innerHTML = st.collections.length ? `<div style="overflow-x:auto"><table class="table"><thead><tr><th>Collection</th><th>Shard key</th>${names.map((n) => `<th class="num">chunks ${esc(n)}</th>`).join("")}${names.map((n) => `<th class="num">docs ${esc(n)}</th>`).join("")}<th style="min-width:160px">Split</th></tr></thead><tbody>
        ${st.collections.map((c) => `<tr><td class="mono">${esc(c.ns)}</td><td class="mono">${esc(c.key)}</td>${names.map((n) => `<td class="num">${c.chunks[n] || 0}</td>`).join("")}${names.map((n) => `<td class="num">${num(c.docs[n] || 0)}</td>`).join("")}<td>${split(Object.fromEntries(names.map((n) => [n, c.docs[n] || 0])))}</td></tr>`).join("")}</tbody></table></div>
        <div class="panel-pad" style="padding-top:0">${shardLegend(names)}</div>`
        : `<p class="muted panel-pad" style="margin:0">No sharded collections yet. Start Lab 11 in the <a href="#/labs">Lab control centre</a>.</p>`;
    };
    paint(st0);
    (async function poll() {
      await new Promise((r) => setTimeout(r, 5000));
      if (!alive) return;
      try { paint(await api("/api/lab11/status")); } catch (e) { /* keep last picture */ }
      poll();
    })();
  }

  // ------------------------------------------------------------------ tab: shard keys (11.1)
  function keysTab(box, st, saved) {
    let r = saved.lab_11_1;
    const W = { cardinality: 15, distribution: 25, hotspot: 25, targeting: 35 };
    const sc = (n) => `<span class="sc s${Math.max(1, Math.min(5, Math.round(n)))}">${n}</span>`;
    const ranked = () => {
      const wsum = Object.values(W).reduce((a, b) => a + b, 0) || 1;
      return r.candidates.map((c) => ({ ...c, live: Object.keys(W).reduce((s2, k) => s2 + (W[k] / wsum) * c.scores[k], 0) }))
        .sort((a, b) => b.live - a.live);
    };
    const drawMatrix = () => {
      const rows = ranked();
      const bestReq = rows.find((c) => c.required);
      $("#k-best").textContent = bestReq.label;
      $("#k-matrix").innerHTML = `<table class="table matrix"><thead><tr><th>#</th><th>Shard key</th><th>Cardinality</th><th>Distribution</th><th>Hot-spot risk</th><th>Query targeting</th><th class="num">Score</th><th>Verdict</th></tr></thead><tbody>
        ${rows.map((c, i) => `<tr class="${c.id === bestReq.id ? "best" : ""}"><td class="num">${i + 1}</td><td><b class="mono">${esc(c.label)}</b>${c.required ? "" : ` <span class="pill">extra</span>`}<div class="muted small">${esc(c.note)}</div></td>
          ${["cardinality", "distribution", "hotspot", "targeting"].map((k) => `<td><div class="cellf">${sc(c.scores[k])}<span>${esc((c.cells || {})[k] || "")}</span></div></td>`).join("")}
          <td class="num"><b>${c.live.toFixed(2)}</b></td><td><span class="vd ${esc(c.verdict.split(" ")[0])}">${esc(c.verdict)}</span><div class="muted small" style="margin-top:4px;max-width:260px">${esc(c.reason)}</div></td></tr>`).join("")}</tbody></table>`;
    };
    const draw = () => {
      if (!r) {
        box.innerHTML = empty("No shard-key evaluation yet", `Start Lab 11 in the <a href="#/labs">Lab control centre</a>, or run it here.
          <div style="margin-top:12px"><button class="btn primary" id="k-run" ${st.available ? "" : "disabled"}>Evaluate the shard keys (6,000 orders)</button></div>`);
        $("#k-run")?.addEventListener("click", run);
        return;
      }
      const shards = r.shards;
      box.innerHTML = `<section class="panel"><div class="panel-head"><h2>Decision matrix</h2><span class="muted">${num(r.n_orders)} orders, ${num(r.customers)} customers, one sharded collection per key</span></div>
        <div class="panel-pad" style="padding-bottom:6px"><div class="weights">${Object.keys(W).map((k) => `<label>${k[0].toUpperCase() + k.slice(1)} weight <b class="mono" id="w-${k}-v">${W[k]}</b>
          <input type="range" min="0" max="60" step="5" value="${W[k]}" data-w="${k}" aria-label="${k} weight"></label>`).join("")}</div>
          <p class="muted small" style="margin:10px 0 0">Move the weights to see how the ranking depends on what the workload needs. With these weights the best of the four required keys is <b style="color:var(--text)" id="k-best"></b>.</p></div>
        <div style="overflow-x:auto" id="k-matrix"></div></section>
      <div class="grid cols-2" style="margin-top:18px;align-items:start">
        <section class="panel"><div class="panel-head"><h2>Where the new orders went</h2><span class="muted">${num(r.live)} live orders, balancer off</span></div><div class="panel-pad">
          ${r.candidates.map((c) => `<div class="kbar"><span class="mono">${esc(c.label)}</span>${split(c.distribution.live_by_shard)}<b>${P(c.distribution.busiest_share)}</b></div>`).join("")}
          ${shardLegend(shards)}<p class="muted small" style="margin:8px 0 0">History orders were split at the median key and half moved to ${esc(shards[1])} first (the balancer's job). Ranged keys then send each new order to the shard that owns its range.</p></div></section>
        <section class="panel"><div class="panel-head"><h2>Query targeting</h2><span class="muted">shards contacted, from explain()</span></div><div style="overflow-x:auto"><table class="table"><thead><tr><th>Shard key</th>${r.workload.map((q) => `<th>${esc(q.label)} <span class="muted">${P(q.weight)}</span></th>`).join("")}</tr></thead><tbody>
          ${r.candidates.map((c) => `<tr><td class="mono">${esc(c.label)}</td>${r.workload.map((q) => { const x = c.targeting.queries[q.id]; return `<td><span class="hops ${x.targeted ? "hop1" : "hop2"}">${x.n_shards} shard${x.n_shards > 1 ? "s" : ""}</span><div class="muted small">${x.targeted ? "targeted" : "scatter-gather"}</div></td>`; }).join("")}</tr>`).join("")}
          </tbody></table></div><div class="panel-pad" style="padding-top:6px"><ul class="plain small">${r.workload.map((q) => `<li>${esc(q.label)}: <span class="mono">${esc(r.queries[q.id])}</span></li>`).join("")}</ul></div></section></div>
      ${explain("Recommendation", (() => { const b = r.candidates.find((c) => c.id === r.recommended); const x = r.candidates.find((c) => c.id === r.best_overall);
        return `<p style="margin:0 0 8px"><b>Shard on ${esc(b.label)}</b> <span class="mono">${esc(b.key)}</span>: ${esc(b.reason)}.</p>
        <p style="margin:0 0 8px">The price: an order lookup by order_id alone and the store-wide 7-day report become scatter-gather. The order page can pass the logged-in customer_id along (then it is targeted again) and reports can run on secondaries.</p>
        ${x && x.id !== b.id ? `<p style="margin:0"><b>Refinement:</b> <span class="mono">${esc(x.label)}</span> scores ${x.total}: ${esc(x.reason)}.</p>` : ""}`; })())}
      <div class="row-gap" style="margin-top:18px;flex-wrap:wrap"><button class="btn" id="k-run" ${st.available ? "" : "disabled"}>Run the evaluation again (6,000 orders, about 10 s)</button><span class="muted small">Shows the new numbers here; the saved report keeps the full 24,000-order run.</span></div>`;
      drawMatrix();
      $$("[data-w]").forEach((sl) => sl.addEventListener("input", () => { W[sl.dataset.w] = Number(sl.value); $(`#w-${sl.dataset.w}-v`).textContent = sl.value; drawMatrix(); }));
      $("#k-run")?.addEventListener("click", run);
    };
    function run(e) {
      act(e.currentTarget, "Loading orders into 6 sharded collections", async () => {
        r = await api("/api/lab11/eval", { method: "POST", body: { orders: 6000 } });
        toast(`Evaluation done: ${esc(r.candidates.find((c) => c.id === r.recommended).label)} recommended`);
        draw();
      });
    }
    draw();
  }

  // ------------------------------------------------------------------ tab: hotspot (11.2)
  function hotTab(box, st, saved) {
    let r = saved.lab_11_2;
    const COLLS = [["hotspot_data", "{ts: 1}", "monotonically increasing timestamp"], ["even_data", "{user_id: 'hashed'}", "hash of a new ObjectId"]];
    const names = st.available ? st.shards.map((s) => s.name) : ["shardA", "shardB"];
    box.innerHTML = `<div class="bins">${COLLS.map(([c, k, d]) => `<section class="panel"><div class="panel-head"><h2 class="mono">db.${c}</h2><span class="muted">shard key <span class="mono">${esc(k)}</span></span></div>
        <div class="panel-pad"><p class="muted small" style="margin:0 0 6px">${d}</p><div id="mini-${c}">${routerMini(`rm-${c}`, names, {})}</div>
        <div id="chunks-${c}" class="small"></div>
        <div class="row-gap" style="margin-top:12px;flex-wrap:wrap"><button class="btn small primary" data-ins="${c}" ${st.available ? "" : "disabled"}>Insert 1,000 more</button><span class="muted small" id="ins-${c}"></span></div></div></section>`).join("")}</div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Inserts routed per batch</h2><span class="muted" id="h-meta"></span></div><div class="panel-pad" id="h-chart"></div></section>
      <div class="grid cols-2" style="margin-top:18px;align-items:start">
        <section class="panel"><div class="panel-head"><h2>collStats per shard</h2><span class="muted">db.collection.stats()</span></div><div id="h-stats"></div></section>
        <section class="panel"><div class="panel-head"><h2>Range queries with explain()</h2></div><div class="panel-pad">
          <div class="row-gap" style="flex-wrap:wrap">${["H1", "H2", "E1", "E2", "E3"].map((p) => `<button class="btn small" data-ex="${p}" ${st.available ? "" : "disabled"}>${p}</button>`).join("")}</div>
          <div id="ex-out" style="margin-top:12px"></div></div></section></div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>getShardDistribution()</h2><div class="row-gap"><button class="btn small" data-gsd="hotspot_data">hotspot_data</button><button class="btn small" data-gsd="even_data">even_data</button></div></div><pre class="term" id="gsd" style="height:300px"></pre></section>
      ${explain("Brief report: why monotonically increasing keys cause hotspots", `<p style="margin:0 0 8px">With a ranged key every chunk is a range of values, and the last one ends at MaxKey. A timestamp or counter gives every new document a value larger than all others, so every insert lands in that last chunk, on one shard. More shards add no write capacity: the cluster inserts only as fast as that one shard. The balancer can move <i>older</i> ranges away, but the newest range always stays together. Hashing maps neighbouring values to unrelated points, so consecutive inserts spread out, even though ObjectIds also increase.</p>
        <p style="margin:0 0 4px"><b>A ranged key is still the right choice when:</b></p><ul class="plain"><li>range queries on the key dominate and must stay on one shard (time-window reports; compare H1 with E1);</li>
        <li>the values are not monotonic and spread over the range (postal codes, SKUs);</li><li>a compound key has a non-monotonic prefix, like <span class="mono">{device_id: 1, ts: 1}</span>;</li>
        <li>zones are needed: recent data on fast disks, old data on cheap ones, or one region's customers in that region;</li><li>the insert rate is low enough for one shard to absorb.</li></ul>`)}
      <div class="row-gap" style="margin-top:18px;flex-wrap:wrap"><button class="btn" id="h-run" ${st.available ? "" : "disabled"}>Run the 10,000-document experiment again (about 10 s)</button><span class="muted small">Drops and rebuilds db.hotspot_data and db.even_data.</span></div>`;

    const EXDESC = { H1: "hotspot_data: range on ts (the classroom query)", H2: "hotspot_data: range crossing the 2025-01-01 boundary", E1: "even_data: the same ts range (not its shard key)", E2: "even_data: range on the hashed user_id", E3: "even_data: equality on the hashed user_id" };
    const exCard = (e) => `<div class="result ${e.targeted ? "ok" : "err"}"><b>${esc(e.id || "")} ${esc(e.stage)}: ${e.n_shards} of ${e.total_shards} shards (${esc(e.kind)})</b>
      <span class="mono small">${esc(e.filter)}</span><span>${num(e.nReturned)} returned, ${num(e.docsExamined)} documents examined, about ${e.avg_ms} ms</span>
      <table class="table" style="margin-top:6px"><thead><tr><th>Shard</th><th>Plan</th><th class="num">Returned</th><th class="num">Examined</th></tr></thead><tbody>${e.per_shard.map((p) => `<tr><td>${esc(p.shard)}</td><td class="mono small">${esc(p.plan)}</td><td class="num">${num(p.n)}</td><td class="num">${num(p.docs)}</td></tr>`).join("")}</tbody></table></div>`;

    const showResult = () => {
      if (!r) { $("#h-chart").innerHTML = `<p class="muted" style="margin:0">No experiment yet. Press <b>Run the 10,000-document experiment</b> below.</p>`; $("#h-stats").innerHTML = ""; return; }
      const a = r.shards[0], b = r.shards[1];
      const tl = (c, s) => r.collections[c].timeline.map((x) => x.docs[s]);
      $("#h-meta").textContent = `${num(r.history)} December-2024 documents, then ${num(r.n - r.history)} of 2025 in ${r.batches} batches`;
      $("#h-chart").innerHTML = lineChart([{ name: `hotspot_data ${a}`, color: "var(--mal)", values: tl("hotspot_data", a) }, { name: `hotspot_data ${b}`, color: "var(--mal)", dash: true, values: tl("hotspot_data", b) },
        { name: `even_data ${a}`, color: "var(--ok)", values: tl("even_data", a) }, { name: `even_data ${b}`, color: "var(--ok)", dash: true, values: tl("even_data", b) }],
        r.collections.hotspot_data.timeline.map((x) => String(x.batch)), { xTitle: "batch of 950 inserts (documents per shard after the batch)" }) +
        `<p class="muted small" style="margin:8px 0 0">Inserts executed by each shard primary in phase 2 (serverStatus opcounters): hotspot_data ${Object.entries(r.summary.hotspot_phase2_inserts).map(([k, v]) => `${esc(k)} ${num(v)}`).join(", ")}; even_data ${Object.entries(r.summary.even_phase2_inserts).map(([k, v]) => `${esc(k)} ${num(v)}`).join(", ")}.</p>`;
      $("#h-stats").innerHTML = `<div style="overflow-x:auto"><table class="table"><thead><tr><th>Collection</th><th>Shard</th><th class="num">count</th><th class="num">size</th><th class="num">avgObjSize</th></tr></thead><tbody>${["hotspot_data", "even_data"].map((c) => Object.entries(r.collections[c].collstats.shards).map(([s, v]) => `<tr><td class="mono">${c}</td><td>${esc(s)}</td><td class="num">${num(v.count)}</td><td class="num">${(v.size / 1024).toFixed(1)} KiB</td><td class="num">${v.avg_obj} B</td></tr>`).join("")).join("")}</tbody></table></div>`;
      if (r.explain && !$("#ex-out").innerHTML) $("#ex-out").innerHTML = `<table class="table"><thead><tr><th>Query</th><th>Stage</th><th class="num">Shards</th><th class="num">Examined</th></tr></thead><tbody>${Object.entries(r.explain).map(([k, e]) => `<tr><td><b>${k}</b> <span class="muted small">${esc(EXDESC[k])}</span></td><td class="mono small">${esc(e.stage)}</td><td class="num"><span class="hops ${e.targeted ? "hop1" : "hop2"}">${e.n_shards}</span></td><td class="num">${num(e.docsExamined)}</td></tr>`).join("")}</tbody></table><p class="muted small" style="margin:8px 0 0">Saved from the last run. Press a button to run that explain() now.</p>`;
    };
    const loadDist = async (c, added) => {
      try {
        const d = await api(`/api/lab11/distribution?coll=${c}`);
        const counts = Object.fromEntries(Object.entries(d.by_shard).map(([k, v]) => [k, v.count]));
        $(`#rm-${c}`)?.querySelectorAll(".bin").forEach((g) => { g.querySelector(".cnt").textContent = num(counts[g.dataset.s] || 0); });
        $(`#chunks-${c}`).innerHTML = `<div class="muted" style="margin-top:6px">Chunks:</div><ul class="plain mono small">${d.chunks.slice(0, 6).map((x) => `<li>[${esc(x.min)}, ${esc(x.max)}) on <b>${esc(x.shard)}</b></li>`).join("")}${d.chunks.length > 6 ? `<li>... ${d.chunks.length - 6} more</li>` : ""}</ul>`;
        if (added) flyParticles(`rm-${c}`, names, added);
        return d;
      } catch (e) { $(`#chunks-${c}`).innerHTML = `<p class="muted small" style="margin:6px 0 0">${esc(e.message)}</p>`; return null; }
    };
    const gsd = async (c) => { try { $("#gsd").textContent = (await api(`/api/lab11/distribution?coll=${c}`)).text; } catch (e) { $("#gsd").textContent = e.message; } };
    showResult();
    if (st.available) { loadDist("hotspot_data"); loadDist("even_data"); gsd("hotspot_data"); }
    $$("[data-ins]").forEach((b) => b.addEventListener("click", () => act(b, "Inserting", async () => {
      const c = b.dataset.ins;
      const x = await api("/api/lab11/insert", { method: "POST", body: { collection: c, n: 1000 } });
      await loadDist(c, x.added);
      $(`#ins-${c}`).innerHTML = Object.entries(x.added).map(([k, v]) => `${esc(k)} <b>+${num(v)}</b>`).join(", ") + ` in ${x.ms} ms`;
    })));
    $$("[data-ex]").forEach((b) => b.addEventListener("click", () => act(b, b.dataset.ex, async () => {
      $("#ex-out").innerHTML = exCard({ ...(await api("/api/lab11/explain", { method: "POST", body: { preset: b.dataset.ex } })), id: b.dataset.ex }) +
        `<p class="muted small" style="margin:8px 0 0">${esc(EXDESC[b.dataset.ex])}</p>`;
    })));
    $$("[data-gsd]").forEach((b) => b.addEventListener("click", () => gsd(b.dataset.gsd)));
    $("#h-run")?.addEventListener("click", (e) => act(e.currentTarget, "Inserting 2 x 10,000 documents", async () => {
      r = await api("/api/lab11/hotspot", { method: "POST" });
      $("#ex-out").innerHTML = "";
      showResult(); loadDist("hotspot_data"); loadDist("even_data"); gsd("hotspot_data");
      const s = r.summary;
      toast(`hotspot_data ${Object.values(s.hotspot_by_shard).map(num).join(" / ")}, even_data ${Object.values(s.even_by_shard).map(num).join(" / ")}`);
    }));
  }

  // ------------------------------------------------------------------ tab: resharding activity
  function reshardTab(box, st, saved) {
    let alive = true, timer;
    onLeave(() => { alive = false; clearTimeout(timer); });
    box.innerHTML = `<div class="grid cols-2" style="align-items:start">
      <section class="panel"><div class="panel-head"><h2>Steps of a resharding operation</h2><span class="muted">MongoDB 5.0 and later</span></div><div class="panel-pad"><ol class="plain" id="rs-steps"></ol></div></section>
      <section class="panel"><div class="panel-head"><h2>Live demonstration</h2></div><div class="panel-pad">
        <p class="muted" style="margin:0 0 10px">Orders sharded on the bad key <span class="mono">{order_date: 1}</span> are resharded to <span class="mono">{customer_id: 'hashed'}</span> while a writer keeps inserting one order every 20 ms. The phases below are read from <span class="mono">$currentOp</span> while it runs.</p>
        <div class="row-gap" style="flex-wrap:wrap"><button class="btn primary" id="rs-run" ${st.available ? "" : "disabled"}>Run live resharding (about 50 s)</button><span class="muted small" id="rs-phase"></span></div>
        <div class="progress" style="margin-top:12px" aria-hidden="true"><i id="rs-bar" style="width:0"></i></div>
        <div id="rs-tl" style="margin-top:12px"></div></div></section></div>
      <div id="rs-result"></div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Limitations</h2><span class="muted">checked against the MongoDB manual and the running 7.0 server</span></div><div class="panel-pad"><ul class="plain" id="rs-limits"></ul></div></section>`;

    const timeline = (tl) => tl && tl.length ? `<div style="overflow-x:auto;max-height:280px"><table class="table"><thead><tr><th class="num">t (s)</th><th>Component</th><th>State</th><th>Progress</th></tr></thead><tbody>${tl.map((e) => `<tr><td class="num">${e.t.toFixed(1)}</td><td>${esc(e.source)}</td><td class="mono small">${esc(e.state)}</td><td class="small muted">${e.to_copy != null ? `${num(e.copied)} of ~${num(e.to_copy)} copied, ${num(e.oplog_applied)} oplog entries applied` : ""}</td></tr>`).join("")}</tbody></table></div>` : "";
    const result = (x, label) => {
      const w = x.writes, b = x.before, a = x.after;
      $("#rs-result").innerHTML = `<section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Result</h2><span class="muted">${esc(label)}</span></div><div class="panel-pad">
        <div class="grid cols-2" style="align-items:start"><div><table class="table"><thead><tr><th></th><th>Before</th><th>After</th></tr></thead><tbody>
          <tr><td>Shard key</td><td class="mono">${esc(b.key)}</td><td class="mono">${esc(a.key)}</td></tr>
          <tr><td>Documents</td><td>${split(b.by_shard)}<span class="small muted">${Object.entries(b.by_shard).map(([k, v]) => `${esc(k)} ${num(v)}`).join(", ")}</span></td><td>${split(a.by_shard)}<span class="small muted">${Object.entries(a.by_shard).map(([k, v]) => `${esc(k)} ${num(v)}`).join(", ")}</span></td></tr>
          <tr><td>Chunks</td><td class="num">${b.chunks.length}</td><td class="num">${a.chunks.length}</td></tr>
          <tr><td>Customer query</td><td><span class="hops ${b.explain_customer.targeted ? "hop1" : "hop2"}">${b.explain_customer.n_shards} shards</span></td><td><span class="hops ${a.explain_customer.targeted ? "hop1" : "hop2"}">${a.explain_customer.n_shards} shard${a.explain_customer.n_shards > 1 ? "s" : ""}</span></td></tr></tbody></table>
          <p style="margin:12px 0 0">reshardCollection took <b>${x.seconds} s</b>. The application wrote <b>${num(w.count)}</b> orders meanwhile with <b>${w.errors}</b> errors: median ${w.p50_ms} ms, 99th percentile ${w.p99_ms} ms, and one stall of <b>${w.max_ms} ms</b> when writes were blocked for the commit.</p>
          ${x.refused && x.refused.length ? `<p class="muted small">MongoDB refused numInitialChunks ${x.refused.map((y) => y.numInitialChunks).join(", ")} first: ${esc(x.refused[0].error)}</p>` : ""}</div>
          <div>${latencyChart(w.series, x.timeline)}</div></div></div></section>`;
    };
    const paintLists = (steps, limits) => {
      $("#rs-steps").innerHTML = steps.map(([t, d]) => `<li><b>${esc(t)}.</b> ${esc(d)}</li>`).join("");
      $("#rs-limits").innerHTML = limits.map((x) => `<li>${esc(x)}</li>`).join("");
    };
    async function poll(first) {
      let s;
      try { s = await api("/api/lab11/reshard"); } catch (e) { return; }
      if (!alive) return;
      if (first) paintLists(s.steps, s.limits);
      const run = $("#rs-run");
      if (s.running || s.phase) {
        $("#rs-phase").textContent = s.running ? `Running: ${s.phase || ""}` : s.phase === "failed" ? `Failed: ${s.error}` : s.phase === "done" ? "Finished" : "";
        const p = s.progress;
        $("#rs-bar").style.width = s.phase === "done" ? "100%" : p && p.to_copy ? `${Math.min(100, (100 * p.copied) / p.to_copy)}%` : s.running ? "4%" : "0";
        $("#rs-tl").innerHTML = timeline(s.timeline);
      }
      if (run) run.disabled = !!s.running || !st.available;
      if (s.result && !s.running) result(s.result, `live run, ${fmtTime(s.finished)}`);
      if (s.running) timer = setTimeout(() => poll(false), 1000);
    }
    if (saved.resharding) { result(saved.resharding, "saved from the last Lab 11 run"); $("#rs-tl").innerHTML = timeline(saved.resharding.timeline); }
    poll(true);
    $("#rs-run")?.addEventListener("click", (e) => act(e.currentTarget, "Starting", async () => {
      await api("/api/lab11/reshard", { method: "POST", body: { orders: 8000 } });
      toast("Resharding started - the phases appear as MongoDB reports them");
      $("#rs-tl").innerHTML = "";
      poll(false);
    }));
  }

  return { lab11: pageLab11 };
};

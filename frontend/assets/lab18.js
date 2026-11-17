/* Kavach CTI - Lab 18: the comparative capstone. False positives from the threat-intelligence service in MongoDB,
   Cassandra and Neo4j: schemas, 4 benchmarks x 10 runs, FP detection, communities, scalability, recommendation. */
window.KavachLab18 = (h) => {
  "use strict";
  const { $, $$, esc, num, api, toast, copyCmd, intro, explain, act } = h;
  const G = window.KavachPG;
  const TABS = [["design", "Use case + schemas"], ["bench", "Benchmarks (18.1)"], ["fp", "False positives"], ["comm", "Communities (activity)"], ["report", "Comparative report (18.2)"]];
  const notRun = `<div class="panel empty" style="margin-top:12px"><strong>No results yet</strong> Press <b>Run</b> above, start Lab 18 in the <a href="#/labs">Lab control centre</a>, or run <span class="mono">docker compose exec lab18 lab18 all</span>.</div>`;
  const DB3 = ["mongodb", "cassandra", "neo4j"];

  async function page18(page, route) {
    const tab = route.params.get("tab") || "design";
    const [st, saved] = await Promise.all([api("/api/lab18/status").catch((e) => ({ available: false, reason: e.message })), api("/api/lab18/results").catch(() => ({}))]);
    const r = (saved || {}).lab_18_1;
    page.innerHTML = `${intro("Capstone lab 18",
      `<b>Use case:</b> identifying <b>false positives</b> in the threat-intelligence service. ${r ? num(r.load.n) : "100,000"} recent threats from many feeds
       (${r ? `${num(r.load.real)} real Kavach sightings + ${num(r.load.generated)} generated` : "the real Kavach sightings first, topped up with 12 simulated feeds"}) are stored in
       <b>MongoDB</b>, <b>Cassandra</b> and <b>Neo4j</b>; four benchmark queries run 10 times in each (median reported); the same false-positive detector runs on all three; a graph algorithm finds
       coordinated campaigns. Lab 18.2 turns the measurements into the comparative report.`,
      `<div class="row-gap"><a class="btn small" href="/api/lab18/report" target="_blank" rel="noopener">PDF report (18.2)</a></div>`)}
      ${G.backends(st.backends)}
      ${st.available ? "" : `<div class="banner err" style="margin:14px 0"><div><strong>Databases not reachable.</strong> ${esc(st.reason || "")}${copyCmd("docker compose up -d lab18")}</div></div>`}
      <section class="panel panel-pad" style="margin-top:14px"><div class="row-gap" style="flex-wrap:wrap"><button class="btn primary small" id="b-run">Run Lab 18.1</button>
        <label class="small"><input type="checkbox" id="full"> full size (100,000 threats, 5-10 min) - otherwise 10,000</label>
        ${st.counts ? `<span class="muted small" style="margin-left:auto">${num(st.counts.mongodb)} threats in MongoDB, ${num(st.counts.neo4j_reported)} REPORTED relationships, ${num(st.counts.fp_verdicts)} FP verdicts in Kavach</span>` : ""}</div></section>
      <nav class="tabs" role="tablist">${TABS.map(([k, t]) => `<button role="tab" data-tab="${k}" class="${tab === k ? "active" : ""}">${t}</button>`).join("")}</nav><div id="tabbody"></div>`;
    $$("[data-tab]").forEach((b) => b.addEventListener("click", () => { location.hash = `#/lab18?tab=${b.dataset.tab}`; }));
    $("#b-run").addEventListener("click", (e) => act(e.currentTarget, "Running (minutes)", async () => {
      const d = await api("/api/lab18/run", { method: "POST", body: { quick: !$("#full").checked } }); toast(`Lab 18.1 finished${d.pass ? "" : " - see the checks"}`); location.reload(); }));
    ({ design, bench, fp, comm, report }[tab] || design)($("#tabbody"), r, st);
  }

  function arch() {
    const box = (x, y, w, hh, t, s, c) => `<rect x="${x}" y="${y}" width="${w}" height="${hh}" rx="8" style="fill:color-mix(in srgb, ${c} 14%, transparent);stroke:${c}"/>
      <text x="${x + w / 2}" y="${y + 20}" text-anchor="middle" class="ax val">${t}</text>${s.map((l, i) => `<text x="${x + w / 2}" y="${y + 37 + i * 15}" text-anchor="middle" class="ax">${l}</text>`).join("")}`;
    const arr = (x1, y1, x2, y2) => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="var(--muted)" stroke-width="1.4" marker-end="url(#ah)"/>`;
    return `<svg viewBox="0 0 760 300" class="lchart pg-arch"><defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--muted)"/></marker></defs>
      ${box(10, 20, 170, 80, "Threat feeds", ["Kavach (Lab 7) sightings", "12 simulated feeds"], "#ffb000")}
      ${box(10, 190, 170, 80, "Ingest service", ["one write per database", "Q4: 1,000 threats / run"], "#ffb000")}
      ${box(270, 10, 200, 88, "MongoDB (rs0)", ["sources.recent_threats (last 50)", "full_threats + 4 indexes", "Q1 history, analyst documents"], G.DBC.mongodb)}
      ${box(270, 106, 200, 88, "Cassandra", ["threats_by_source / _type", "_by_indicator / _by_family", "daily_volume counters"], G.DBC.cassandra)}
      ${box(270, 202, 200, 88, "Neo4j", ["(Source)-[:REPORTED]->(Indicator)", "-[:OF_FAMILY]->(Family)", "SUSPICIOUS feeds, communities"], G.DBC.neo4j)}
      ${box(560, 60, 190, 80, "FP detector", ["allowlist + corroboration", "+ feed quality -> verdict"], "#d6336c")}
      ${box(560, 170, 190, 80, "Kavach website / Lab 7", ["cti_platform.fp_verdicts", "investigate an indicator"], "#ffb000")}
      ${arr(95, 100, 95, 190)}${arr(180, 230, 270, 54)}${arr(180, 230, 270, 150)}${arr(180, 230, 270, 246)}${arr(470, 54, 560, 95)}${arr(470, 150, 560, 100)}${arr(470, 246, 560, 110)}${arr(655, 140, 655, 170)}</svg>`;
  }

  function design(box, r) {
    box.innerHTML = `<section class="panel panel-pad"><h2>System architecture</h2>${arch()}</section>
      <div class="grid cols-3" style="gap:14px;margin-top:14px">
        <section class="panel panel-pad">${G.badge("mongodb")}<h3 style="margin:8px 0">Documents</h3><p class="small">One document per threat in <span class="mono">full_threats</span>; a <span class="mono">sources</span> document per feed with an embedded
          <span class="mono">recent_threats</span> array (last 50, kept current with <span class="mono">$push $each $sort $slice</span>) for fast display. Index <span class="mono">(from_source, timestamp)</span> for history,
          plus (indicator, timestamp), (family, timestamp), (timestamp, threat_type).</p></section>
        <section class="panel panel-pad">${G.badge("cassandra")}<h3 style="margin:8px 0">Query-driven tables</h3><p class="small"><span class="mono">threats_by_source</span> ((source, month), ts DESC) and <span class="mono">threats_by_type</span> ((type, day), ts DESC) as the mentor asked;
          because CQL cannot join or filter freely, the other questions need their own tables: <span class="mono">threats_by_indicator</span>, <span class="mono">threats_by_family</span>, a <span class="mono">daily_volume</span> counter table and
          <span class="mono">indicator_sources</span> (set of feeds). Every threat is written to all of them - each query reads ONE partition.</p></section>
        <section class="panel panel-pad">${G.badge("neo4j")}<h3 style="margin:8px 0">Nodes and relationships</h3><p class="small"><span class="mono">(:Source)-[:REPORTED {ts, day, type, confidence}]->(:Indicator)</span>, <span class="mono">(:Indicator)-[:OF_FAMILY]->(:Family)</span>, <span class="mono">-[:IN_ASN]->(:ASN)</span>.
          Two feeds that both report at least 3 known-good indicators get a <span class="mono">SUSPICIOUS</span> relationship. Relationship indexes on REPORTED.day and REPORTED.ts.</p></section></div>
      ${r ? `<section class="panel panel-pad" style="margin-top:14px"><h2>Loaded</h2>${G.table(["Database", "Seconds", "What"], [
        ["MongoDB", r.load.mongodb.seconds, `${num(r.load.mongodb.documents)} documents, ${r.load.mongodb.sources} source documents`],
        ["Cassandra", r.load.cassandra.seconds, `${num(r.load.cassandra.rows)} rows in 4 tables, ${num(r.load.cassandra.counters)} counters, ${num(r.load.cassandra.indicator_rows)} indicator rows`],
        ["Neo4j", r.load.neo4j.seconds, `${num(r.load.neo4j.indicators)} indicators, ${r.load.neo4j.sources} sources, ${num(r.load.neo4j.relationships)} REPORTED`]], [1])}
        <p class="small muted">${num(r.load.indicators)} distinct indicators; sightings by ground-truth label: ${esc(JSON.stringify(r.load.labels_by_sighting))}; ${r.load.campaigns.length} planted campaigns.</p></section>` : notRun}`;
  }

  function bench(box, r) {
    if (!r) return (box.innerHTML = notRun);
    const b = r.benchmark, R = b.results;
    const rows = ["q1", "q2", "q3"].map((k) => ({ label: { q1: "Q1 threat history", q2: "Q2 target + spread", q3: "Q3 daily volume" }[k], values: Object.fromEntries(DB3.map((d) => [d, R[k][d].median_ms])) }));
    box.innerHTML = `<section class="panel panel-pad"><h2>Median latency, 10 runs each (ms, lower is better)</h2>${G.grouped(rows, { log: true, fmt: (v) => Number(v).toFixed(v < 10 ? 2 : 0) })}</section>
      <div class="grid cols-2" style="gap:14px;margin-top:14px"><section class="panel panel-pad"><h2>Q4 real-time insert throughput (threats/s, higher is better)</h2>
        ${G.bars(DB3.map((d) => ({ label: G.DBN[d], value: R.q4[d].throughput_per_s, color: G.DBC[d] })), { fmt: (v) => num(Math.round(v)), unit: "/s", label: 100 })}
        <p class="small muted">1,000 new threats per run through each database's full write path; median of 10 runs. Benchmark threats are removed afterwards.</p></section>
        <section class="panel panel-pad"><h2>Same answer everywhere?</h2>${G.table(["Query", "Identical"], ["q1", "q2", "q3"].map((k) => [esc(b.text[k].title), b.same_answer[k] ? `<span class="dot ok"></span>yes` : `<span class="dot err"></span>no`]))}
        ${R.q2.example ? `<p class="small">Q2 example <span class="mono">${esc(R.q2.example.indicator)}</span>: reported by ${R.q2.example.sources.length} feeds, spread ${num(R.q2.example.spread)} related indicators in 7 days.</p>` : ""}</section></div>
      <section class="panel panel-pad" style="margin-top:14px"><h2>Latency table</h2>${G.table(["Query", "Database", "median", "p95", "min", "max"],
        ["q1", "q2", "q3", "q4"].flatMap((k) => DB3.map((d) => [esc(k.toUpperCase()), G.badge(d), `<b>${G.ms(R[k][d].median_ms)}</b>`, G.ms(R[k][d].p95_ms), G.ms(R[k][d].min_ms), G.ms(R[k][d].max_ms)])), [2, 3, 4, 5])}
        ${R.q1.mongodb_full_collection ? `<p class="small muted">Q1 in MongoDB without the embedded array (find + sort on full_threats): median ${G.ms(R.q1.mongodb_full_collection.median_ms)}.</p>` : ""}</section>
      ${["q1", "q2", "q3", "q4"].map((k) => `<section class="panel panel-pad" style="margin-top:14px"><h2>${esc(b.text[k].title)}</h2><div class="grid cols-3" style="gap:10px">${DB3.map((d) => `<div>${G.badge(d)}${G.code(b.text[k][d], 220)}</div>`).join("")}</div></section>`).join("")}`;
  }

  function fp(box, r) {
    box.innerHTML = `<section class="panel panel-pad"><h2>Investigate an indicator</h2><div class="row-gap" style="margin:8px 0;flex-wrap:wrap"><input id="iv" style="flex:1" value="8.8.8.8"><button class="btn small" id="b-iv">Ask all three databases</button>
      <span class="muted small">try 8.8.8.8, google.com, or a value from the table below</span></div><div id="ivout"></div></section><div id="fpres"></div>`;
    $("#b-iv").addEventListener("click", (e) => act(e.currentTarget, "Querying", async () => {
      const d = await api(`/api/lab18/investigate/${encodeURIComponent($("#iv").value.trim())}`);
      $("#ivout").innerHTML = `<p>${d.verdict ? `<span class="pill st-failed" style="margin:0">${esc(d.verdict.verdict)}</span> ${esc(d.verdict.reason)}` : `<span class="pill st-ok" style="margin:0">not flagged</span>`}${d.allowlisted ? " (on the allowlist)" : ""}</p>
        <div class="grid cols-3" style="gap:10px">${DB3.map((k) => `<div class="panel panel-pad">${G.badge(k)} <b style="float:right">${G.ms(d.results[k].ms)}</b><p class="small">${d.results[k].sources.length} feeds: ${esc(d.results[k].sources.join(", "))}<br>spread: <b>${num(d.results[k].spread)}</b></p></div>`).join("")}</div>
        ${G.table(["Time", "Feed", "Type", "Family", "Confidence"], d.sightings.map((s) => [esc(String(s.timestamp).slice(0, 16)), esc(s.from_source), esc(s.threat_type), esc(s.family), s.confidence]), [4])}`;
    }));
    if (!r) return ($("#fpres").innerHTML = notRun);
    const f = r.fp;
    $("#fpres").innerHTML = `${explain("The detector (the same rules on every database)", `<pre class="term" style="height:auto;margin:6px 0">${esc(f.rules)}</pre>
      <p>Each database only supplies <i>indicator -> set of feeds, max confidence, allowlisted?</i> in its own way: MongoDB with a <span class="mono">$group</span> over all threats,
      Cassandra by paging through the <span class="mono">indicator_sources</span> table (the set was maintained on write), Neo4j by aggregating REPORTED relationships.</p>`)}
      <div class="grid cols-2" style="gap:14px;margin-top:14px"><section class="panel panel-pad"><h2>Per database</h2>${G.table(["Database", "Read (ms)", "Indicators", "Flagged", "By reason"], DB3.map((k) => [G.badge(k), num(f.per_db[k].fetch_ms), num(f.per_db[k].indicators), num(f.per_db[k].flagged), `<span class="small">${esc(JSON.stringify(f.per_db[k].by_reason))}</span>`]), [1, 2, 3])}
        <p>Identical verdicts in all three: <b>${f.identical ? "yes" : "no"}</b>. Against the generator's labels: precision <b>${f.accuracy.precision}</b>, recall <b>${f.accuracy.recall}</b> (known-good recall ${f.accuracy.benign_recall});
        ${num(f.accuracy.real_kavach_flagged)} real Kavach indicators flagged.</p></section>
        <section class="panel panel-pad"><h2>Feed quality (corroboration rate)</h2>${G.bars(f.feeds.map((x) => ({ label: x.source.slice(0, 24), value: Math.round(1000 * x.corroboration) / 10, color: x.suspicious_links ? "var(--mal)" : "var(--ok)" })), { unit: " %", label: 170 })}
        <p class="small muted">Red = feed has SUSPICIOUS links in Neo4j (shares >= 3 known-good indicators with another feed).</p></section></div>
      <div class="grid cols-2" style="gap:14px;margin-top:14px"><section class="panel panel-pad"><h2>SUSPICIOUS feed pairs (Neo4j, ${f.suspicious_ms} ms)</h2>${G.table(["Feed", "Feed", "Shared known-good"], f.suspicious.map((s) => [esc(s.a), esc(s.b), s.shared]), [2])}</section>
        <section class="panel panel-pad"><h2>Flagged (sample)</h2>${G.table(["Indicator", "Verdict", "Why"], f.sample.map((s) => [`<a href="javascript:void 0" class="mono small" data-iv="${esc(s.indicator)}">${esc(s.indicator.slice(0, 40))}</a>`, esc(s.verdict), `<span class="small">${esc(s.reason)}</span>`]))}</section></div>`;
    $$("[data-iv]").forEach((a) => a.addEventListener("click", () => { $("#iv").value = a.dataset.iv; $("#b-iv").click(); window.scrollTo(0, 0); }));
  }

  function comm(box, r) {
    if (!r) return (box.innerHTML = notRun);
    const c = r.communities;
    box.innerHTML = `${explain("Activity - communities of threats with unusually high inter-connectivity", `<p>Two indicators are linked when the same feed reports both in the same hour; a link repeated by several feeds gets weight >= 2 (random co-occurrence happens once).
      The co-reporting graph is read from Neo4j and split into communities with <b>Louvain</b>; communities with density >= 0.5 and links repeated by >= 2.5 feeds on average are flagged, their id is written back
      to <span class="mono">Indicator.community</span>, and each is cross-referenced with the MongoDB documents (feeds, families, time window, confidence).</p>
      <p>Co-reporting graph: <b>${num(c.nodes)}</b> indicators, <b>${num(c.edges)}</b> links; ${c.communities} communities, ${c.flagged.length} flagged. Planted coordinated campaigns recovered: <b>${c.campaigns_found}/${c.campaigns_planted}</b>. ${c.gds ? "GDS available." : "(GDS plugin not installed - Louvain from networkx.)"}</p>`)}
      <section class="panel panel-pad" style="margin-top:14px">${G.table(["#", "Size", "Density", "Link freq.", "z", "MongoDB: feeds", "Families", "Window (min)", "Avg conf.", "Campaign match"],
        c.flagged.map((s) => [s.community, s.size, s.density, s.avg_link_frequency, s.z, (s.mongodb.sources || []).length, `<span class="small">${esc((s.mongodb.families || []).join(", "))}</span>`, s.mongodb.window_minutes, s.mongodb.avg_conf, `${Math.round(100 * s.matches_campaign)}% ${esc(s.campaign_family || "")}`]), [1, 2, 3, 4, 5, 7, 8])}</section>`;
  }

  function report(box, r) {
    if (!r) return (box.innerHTML = notRun);
    const sc = r.scale, gb = (v) => (v >= 1024 ? `${(v / 1024).toFixed(1)} TB` : `${v} GB`);
    box.innerHTML = `<section class="panel panel-pad"><h2>Recommendation per access pattern</h2>${G.table(["Access pattern", "Measured fastest", "Recommend", "Why"],
        r.recommendation.map((x) => [esc(x.pattern), G.badge(x.measured_winner), `<b>${esc(x.recommend)}</b>`, `<span class="small">${esc(x.why)}</span>`]))}
        <p style="margin-top:10px"><a class="btn primary small" href="/api/lab18/report" target="_blank" rel="noopener">Open the full comparative report (PDF)</a></p></section>
      <section class="panel panel-pad" style="margin-top:14px"><h2>Scalability assessment</h2>
        <p class="small">Measured size per threat: ${DB3.map((k) => `${G.DBN[k]} <b>${r.sizes[k].per_threat} B</b>`).join(", ")} (incl. indexes / all query tables).</p>
        ${G.table(["Threats", "MongoDB storage / RAM / nodes", "Cassandra storage / RAM / nodes", "Neo4j storage / RAM / nodes", "Ingest needed (peak)"],
          sc.rows.map((x) => [num(x.threats), ...DB3.map((k) => `${gb(x[k].storage_gb)} / ${gb(x[k].ram_gb)} / ${x[k].nodes}`), `${num(x.ingest_needed_per_s)}/s`]))}
        <p class="small muted">Measured insert throughput (one node): ${DB3.map((k) => `${G.DBN[k]} ${num(Math.round(sc.measured_throughput[k] || 0))}/s`).join(", ")}. Assumptions: ${esc(sc.assumptions.join("; "))}.</p>
        ${G.table(["Database", "How it scales horizontally"], DB3.map((k) => [G.badge(k), `<span class="small">${esc(sc.rows[2][k].how)}</span>`]))}</section>
      <section class="panel panel-pad" style="margin-top:14px"><h2>Ergonomics and operations</h2>${G.table(["", "MongoDB", "Cassandra", "Neo4j"], r.ergonomics.map((x) => [`<b>${esc(x[0])}</b>`, `<span class="small">${esc(x[1])}</span>`, `<span class="small">${esc(x[2])}</span>`, `<span class="small">${esc(x[3])}</span>`]))}</section>
      ${r.applied ? explain("Applied to Lab 7", `<p>${num(r.applied.written)} verdicts written to <span class="mono">${esc(r.applied.collection)}</span>; ${num(r.applied.matching_kavach_indicators)} of them are indicators of the Kavach platform.</p>`) : ""}`;
  }

  return { lab18: page18 };
};

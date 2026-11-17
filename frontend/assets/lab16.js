/* Kavach CTI - Lab 16: Cassandra + Neo4j next to MongoDB. 16.1 three data models, activity (hybrid architecture),
   16.2 FraudQueryService (pooling, circuit breaker, tests), applied to the Lab 10 billing ledger. */
window.KavachLab16 = (h) => {
  "use strict";
  const { $, $$, esc, num, api, toast, copyCmd, intro, explain, act } = h;
  const G = window.KavachPG;
  const TABS = [["models", "Three data models (16.1)"], ["hybrid", "Hybrid architecture (activity)"], ["fraud", "FraudQueryService (16.2)"],
    ["applied", "Applied to Lab 10"], ["scripts", "Schemas + code"]];
  const notRun = (cmd) => `<div class="panel empty" style="margin-top:12px"><strong>No results yet</strong> Press the button above, start Lab 16 in the <a href="#/labs">Lab control centre</a>, or run <span class="mono">${esc(cmd)}</span>.</div>`;

  async function page16(page, route) {
    const tab = route.params.get("tab") || "models";
    const [st, saved] = await Promise.all([api("/api/lab16/status").catch((e) => ({ available: false, reason: e.message })), api("/api/lab16/results").catch(() => ({}))]);
    page.innerHTML = `${intro("Cassandra + Neo4j lab 16",
      `Polyglot persistence: the same data in a <b>document</b> database (MongoDB), a <b>wide-column</b> database (Cassandra, one table per query)
       and a <b>graph</b> database (Neo4j, nodes and relationships). Lab 16.1 models a university enrollment system three times and checks that the
       three answers are identical; the activity combines them (MongoDB = operational data, Cassandra = audit log, Neo4j = prerequisite chains);
       Lab 16.2 builds one <b>FraudQueryService</b> over all three with connection pooling and a circuit breaker. Applied to Lab 10: the Kavach billing ledger goes through the same fraud check.`,
      `<div class="row-gap"><a class="btn small" href="/api/lab16/report" target="_blank" rel="noopener">PDF report</a></div>`)}
      ${G.backends(st.backends)}
      ${st.available ? "" : `<div class="banner err" style="margin:14px 0"><div><strong>Cassandra / Neo4j not reachable.</strong> ${esc(st.reason || "")}<div style="margin-top:6px">Start Labs 7 to 16 with:</div>${copyCmd("docker compose up -d lab16")}</div></div>`}
      <nav class="tabs" role="tablist">${TABS.map(([k, t]) => `<button role="tab" data-tab="${k}" class="${tab === k ? "active" : ""}">${t}</button>`).join("")}</nav><div id="tabbody"></div>`;
    $$("[data-tab]").forEach((b) => b.addEventListener("click", () => { location.hash = `#/lab16?tab=${b.dataset.tab}`; }));
    ({ models, hybrid, fraud, applied, scripts }[tab] || models)($("#tabbody"), st, saved || {});
  }

  const runBtn = (id, label) => `<button class="btn primary small" id="${id}">${label}</button>`;
  async function runPart(btn, part, body = {}) {
    await act(btn, "Running", async () => { const r = await api(`/api/lab16/run/${part}`, { method: "POST", body }); toast(`${part} finished${r.pass === false ? " - see FAIL items" : ""}`); location.reload(); });
  }

  // ------------------------------------------------------------------ 16.1
  function models(box, st, saved) {
    const r = saved.lab_16_1;
    box.innerHTML = `${explain("Lab 16.1 - model the same dataset in three databases",
      `<p><b>MongoDB:</b> <span class="mono">students</span> with an embedded <span class="mono">course_ids</span> array and <span class="mono">courses</span> with an embedded student <span class="mono">roster</span>.
       <b>Cassandra:</b> <span class="mono">student_courses</span> (partition key student_id) and <span class="mono">course_students</span> (partition key course_id) - every enrollment is written to both.
       <b>Neo4j:</b> <span class="mono">(:Student)-[:ENROLLED_IN]->(:Course)</span>. Each question is asked of all three databases for 5 different students/courses, 10 runs each; the answers must be identical.</p>
       <div class="row-gap">${runBtn("b-161", "Run Lab 16.1")}<label class="small"><input type="checkbox" id="q161"> quick</label></div>`)}
      <section class="panel panel-pad" style="margin-top:14px"><h2>Try a query in all three databases</h2>
        <div class="row-gap" style="flex-wrap:wrap;margin-top:8px"><select id="lq">${["courses_of_student", "students_in_course", "shared_course"].map((q) => `<option value="${q}">${q.replace(/_/g, " ")}</option>`).join("")}</select>
        <input id="la" value="S0001" style="width:110px"> <button class="btn small" id="b-q">Run in MongoDB, Cassandra and Neo4j</button><span class="muted small">students S0001-S0300 (S0001 = Alice), courses C101-C403</span></div><div id="qout"></div></section>
      <div id="res161"></div>`;
    $("#b-161").addEventListener("click", (e) => runPart(e.currentTarget, "lab_16_1", { quick: $("#q161").checked }));
    $("#lq").addEventListener("change", () => { $("#la").value = $("#lq").value === "students_in_course" ? "C301" : "S0001"; });
    $("#b-q").addEventListener("click", (e) => act(e.currentTarget, "Querying", async () => {
      const d = await api("/api/lab16/query", { method: "POST", body: { query: $("#lq").value, arg: $("#la").value.trim() } });
      $("#qout").innerHTML = `<p style="margin:12px 0 6px">${d.identical ? `<span class="dot ok"></span> identical answers` : `<span class="dot err"></span> answers differ`} - ${num(d.db.mongodb.result.length)} results</p>
        <div class="grid cols-3" style="gap:10px">${["mongodb", "cassandra", "neo4j"].map((k) => `<div class="panel panel-pad">${G.badge(k)} <b style="float:right">${G.ms(d.db[k].median_ms)}</b>${G.code(d.db[k].code, 160)}
        <div class="small mono" style="word-break:break-all">${esc(d.db[k].result.slice(0, 30).join(", "))}${d.db[k].result.length > 30 ? " ..." : ""}</div></div>`).join("")}</div>`;
    }));
    if (!r || !r.queries) return ($("#res161").innerHTML = notRun("docker compose exec lab16 lab16 only lab_16_1"));
    $("#res161").innerHTML = `<section class="panel panel-pad" style="margin-top:14px"><h2>Loaded</h2><p>${num(r.load.students)} students, ${num(r.load.courses)} courses, ${num(r.load.enrollments)} enrollments
      (${num(r.load.cassandra_rows)} Cassandra rows: every enrollment twice). Load seconds: MongoDB ${r.load.load_s.mongodb}, Cassandra ${r.load.load_s.cassandra}, Neo4j ${r.load.load_s.neo4j}.</p>
      ${G.grouped(r.queries.map((q) => ({ label: q.title.replace("Find ", "").slice(0, 38), values: { mongodb: q.db.mongodb.median_ms, cassandra: q.db.cassandra.median_ms, neo4j: q.db.neo4j.median_ms } })), { fmt: (v) => `${Number(v).toFixed(2)}` })}
      <p class="muted small">Median latency in ms (5 arguments x ${r.runs_per_arg} runs each, warm). All answers identical: <b>${r.identical ? "yes" : "NO"}</b>.</p></section>
      ${r.queries.map((q) => `<section class="panel panel-pad" style="margin-top:14px"><h2>${esc(q.title)} ${q.identical_answers ? `<span class="pill st-ok">identical in all 3</span>` : `<span class="pill st-failed">differs</span>`}</h2>
        <div class="grid cols-3" style="gap:10px;margin-top:8px">${["mongodb", "cassandra", "neo4j"].map((k) => `<div>${G.badge(k)} <span class="small muted" style="float:right">${q.db[k].lines} lines, ${q.db[k].chars} chars, ${q.db[k].round_trips} round trip(s) - <b>${G.ms(q.db[k].median_ms)}</b></span>${G.code(q.db[k].code, 200)}</div>`).join("")}</div>
        <p class="small muted" style="margin:6px 0 0">Example ${esc(q.example.arg)}: ${num(q.example.count)} results (${esc(q.example.first.join(", "))}${q.example.count > 8 ? " ..." : ""})</p></section>`).join("")}
      ${explain("Comparison", `<p><b>Syntax.</b> The first two questions are one statement everywhere. "Students who share a course" is one Cypher pattern, one MongoDB pipeline, and in Cassandra
        1 + N queries plus a union in Python - CQL has no joins, so a new question usually means a new table.</p><p><b>Performance.</b> Key lookups on a partition (Cassandra) or an indexed field
        (MongoDB) are similar; Neo4j pays a little per query for planning and the Bolt round trip but its cost grows with the paths it follows, not with the size of the database.
        Cassandra's fan-out query issues N requests in parallel; its latency is set by the slowest one.</p>`)}`;
  }

  // ------------------------------------------------------------------ activity
  function hybrid(box, st, saved) {
    const a = saved.activity;
    box.innerHTML = `${explain("Activity - hybrid architecture",
      `<p><b>MongoDB</b> keeps the operational documents (students, courses, rosters; an enrollment updates both in one transaction). <b>Cassandra</b> keeps the append-only
       <b>audit log</b> (partition = student + day, newest first). <b>Neo4j</b> answers <b>prerequisite chain</b> queries: <span class="mono">(:Course)-[:REQUIRES*1..]->(:Course)</span>, and refuses an
       enrollment while any course in the chain is not COMPLETED.</p><div class="row-gap">${runBtn("b-act", "Run the activity")}</div>`)}
      <div class="grid cols-2" style="gap:14px;margin-top:14px">
        <section class="panel panel-pad"><h2>Prerequisite chain (Neo4j)</h2><div class="row-gap" style="margin:8px 0"><input id="pc" value="C403" style="width:90px"><button class="btn small" id="b-pc">Show chain</button></div><div id="pcout"></div></section>
        <section class="panel panel-pad"><h2>Enroll a student (all three databases)</h2><div class="row-gap" style="margin:8px 0;flex-wrap:wrap"><input id="es" value="S0003" style="width:90px"><input id="ec" value="C301" style="width:90px">
          <button class="btn small" id="b-en">Enroll</button><button class="btn small" id="b-co">Mark course completed</button></div><div id="enout"></div></section></div>
      <div id="actres"></div>`;
    $("#b-act").addEventListener("click", (e) => runPart(e.currentTarget, "activity"));
    $("#b-pc").addEventListener("click", (e) => act(e.currentTarget, "Reading", async () => {
      const d = await api(`/api/lab16/prereqs/${encodeURIComponent($("#pc").value.trim())}`);
      $("#pcout").innerHTML = `<p>Longest chain: <b class="mono">${esc(d.longest_path.join(" -> "))}</b></p>` + G.table(["Course", "Title", "Depth"], d.all.map((x) => [esc(x.id), esc(x.title), x.depth]), [2]);
    }));
    const enroll = async () => {
      const d = await api("/api/lab16/enroll", { method: "POST", body: { student: $("#es").value.trim(), course: $("#ec").value.trim() } });
      $("#enout").innerHTML = `<p>${d.ok ? `<span class="dot ok"></span> enrolled (${G.ms(d.total_ms)}; graph check ${G.ms(d.graph_ms)})` : `<span class="dot err"></span> refused by Neo4j - missing: <b class="mono">${esc(d.missing.join(", "))}</b>`}</p>
        <h3 style="margin:10px 0 4px;font-size:14px">Audit trail today (Cassandra)</h3>` + G.table(["Time", "Action", "Actor", "Outcome", "Details"], d.audit.map((x) => [esc(x.at.slice(11, 19)), esc(x.action), esc(x.actor), esc(x.outcome), `<span class="mono small">${esc(JSON.stringify(x.details))}</span>`]));
    };
    $("#b-en").addEventListener("click", (e) => act(e.currentTarget, "Checking", enroll));
    $("#b-co").addEventListener("click", (e) => act(e.currentTarget, "Saving", async () => { await api("/api/lab16/complete", { method: "POST", body: { student: $("#es").value.trim(), course: $("#ec").value.trim() } }); toast("Marked COMPLETED in Neo4j and audited in Cassandra"); }));
    if (!a || !a.steps) return ($("#actres").innerHTML = notRun("docker compose exec lab16 lab16 only activity"));
    $("#actres").innerHTML = `<section class="panel panel-pad" style="margin-top:14px"><h2>Measured run ${a.pass ? `<span class="pill st-ok">pass</span>` : `<span class="pill st-failed">check</span>`}</h2>
      <p>${esc(a.student)} asks for ${esc(a.course)}; chain <b class="mono">${esc(a.chain.longest_path.join(" -> "))}</b> (${a.chain.all.length} prerequisite courses).</p>
      ${G.table(["Step", "Result", "Missing prerequisites"], a.steps.map((s) => [esc(s.step), s.ok ? `<span class="dot ok"></span> ok` : `<span class="dot err"></span> refused`, `<span class="mono small">${esc((s.missing || []).join(", "))}</span>`]))}
      <p class="small muted">Stored afterwards: MongoDB ${a.stored.mongodb ? "yes" : "no"}, Cassandra ${a.stored.cassandra ? "yes" : "no"}; ${a.audit.length} audit rows.</p></section>`;
  }

  // ------------------------------------------------------------------ 16.2
  function fraud(box, st, saved) {
    const r = saved.lab_16_2;
    box.innerHTML = `${explain("Lab 16.2 - a hybrid query service",
      `<p><span class="mono">FraudQueryService</span> asks <b>MongoDB</b> for a transaction document (<span class="mono">get_transaction</span>), <b>Cassandra</b> for a user's time-ordered events
       (<span class="mono">get_user_profile</span>, partition user_id, clustering event_time DESC) and <b>Neo4j</b> for money-transfer rings (<span class="mono">detect_fraud_rings</span>, up to 3 hops), and merges them.
       Three consecutive failures of a backend open its <b>circuit</b>: it is skipped (partial answer, no waiting) and retried after 30 s.</p>
       <div class="row-gap">${runBtn("b-162", "Run Lab 16.2 (load + tests + latency)")}<label class="small"><input type="checkbox" id="q162"> quick</label></div>`)}
      <section class="panel panel-pad" style="margin-top:14px"><h2>Analyse a user live</h2>
        <div class="row-gap" style="flex-wrap:wrap;margin:8px 0"><input id="fu" value="U00001" style="width:100px"><select id="fh"><option>1</option><option>2</option><option selected>3</option></select><button class="btn small" id="b-fu">Analyse</button>
          <span class="muted small">Simulate an outage:</span>${["mongodb", "cassandra", "neo4j"].map((k) => `<label class="small"><input type="checkbox" data-fault="${k}"> ${G.DBN[k]} down</label>`).join("")}</div>
        <div id="ringlist" class="small muted"></div><div id="fout"></div></section><div id="res162"></div>`;
    $("#b-162").addEventListener("click", (e) => runPart(e.currentTarget, "lab_16_2", { quick: $("#q162").checked }));
    api("/api/lab16/rings").then((rings) => { if (rings.length) $("#ringlist").innerHTML = `Planted rings (click a member): ${rings.map((r) => r.map((u) => `<a href="javascript:void 0" data-u="${u}" class="mono">${u}</a>`).join(" -> ")).join(" &nbsp;|&nbsp; ")}`;
      $$("[data-u]").forEach((a) => a.addEventListener("click", () => { $("#fu").value = a.dataset.u; $("#b-fu").click(); })); }).catch(() => {});
    $$("[data-fault]").forEach((c) => c.addEventListener("change", async () => { const d = await api("/api/lab16/fault", { method: "POST", body: { backend: c.dataset.fault, on: c.checked } }); toast(`Injected outages: ${d.faults.join(", ") || "none"}`); }));
    $("#b-fu").addEventListener("click", (e) => act(e.currentTarget, "Analysing", async () => {
      const d = await api(`/api/lab16/fraud/${encodeURIComponent($("#fu").value.trim())}?hops=${$("#fh").value}`);
      const lvl = { high: "var(--mal)", medium: "var(--med)", low: "var(--ok)" }[d.risk.level];
      const src = (k) => d.sources[k] || {};
      $("#fout").innerHTML = `<div class="row-gap" style="margin:10px 0"><b style="font-size:20px;color:${lvl}">risk ${esc(d.risk.level)} (${d.risk.score})</b><span class="muted">${esc(d.risk.reasons.join("; ") || "no signal")}</span><span class="muted small" style="margin-left:auto">${G.ms(d.total_ms)} total</span></div>
        <div class="grid cols-3" style="gap:10px">${[["mongodb", "Recent transactions", (d.transactions || []).map((t) => `${esc(t.txn_id)} ${esc(t.merchant)} Rs ${num(Math.round(t.amount))} risk ${t.risk_score}`)],
          ["cassandra", "Profile events (newest first)", (d.profile || []).map((p) => `${esc(String(p.event_time).slice(0, 16))} ${esc(p.event_type)} Rs ${num(Math.round(p.amount))}`)],
          ["neo4j", "Transfer graph", [...((d.graph || {}).rings || []).map((x) => `RING ${x.ring.join(" -> ")} (Rs ${num(Math.round(x.total))}, ${x.minutes} min)`), ...((d.graph || {}).connected || []).slice(0, 8).map((c) => `${c.user}: ${c.hops} hop(s)`)]]].map(([k, t, rows]) =>
          `<div class="panel panel-pad">${G.badge(k)} <span class="small" style="float:right">${src(k).ok ? G.ms(src(k).ms) : `<span style="color:var(--mal)">${src(k).skipped ? "skipped (circuit open)" : "failed"}</span>`}</span><h3 style="font-size:14px;margin:8px 0">${t}</h3>
           ${src(k).ok ? `<div class="small mono">${rows.slice(0, 10).map(esc).join("<br>") || "<span class='muted'>none</span>"}</div>` : `<p class="small muted">${esc(src(k).error || "")}</p>`}</div>`).join("")}</div>
        <p class="small" style="margin-top:8px">Circuit breakers: ${d.breakers.map((b) => `${G.DBN[b.backend]} <b>${b.state}</b> (${b.failures}/${b.threshold})`).join(" &middot; ")}</p>`;
    }));
    if (!r || !r.unit_tests) return ($("#res162").innerHTML = notRun("docker compose exec lab16 lab16 only lab_16_2"));
    const lat = Object.entries(r.latency);
    $("#res162").innerHTML = `<div class="grid cols-2" style="gap:14px;margin-top:14px">
      <section class="panel panel-pad"><h2>Unit tests (unittest.mock) - ${r.unit_tests.passed}/${r.unit_tests.total}</h2><p class="small mono">${esc(r.unit_tests.command)}</p>
        ${G.table(["Test", "Case", ""], r.unit_tests.tests.map((t) => [`<span class="mono small">${esc(t.test)}</span>`, esc(t.case), t.ok ? `<span class="dot ok"></span>ok` : `<span class="dot err"></span>FAIL`]))}</section>
      <section class="panel panel-pad"><h2>Integration test (real databases)</h2>${G.table(["Check", ""], r.integration.map((t) => [esc(t.test), t.ok ? `<span class="dot ok"></span>pass` : `<span class="dot err"></span>FAIL`]))}
        <p class="small muted">Dataset: ${num(r.load.users)} users, ${num(r.load.transactions)} transactions (MongoDB), ${num(r.load.profile_rows)} events (Cassandra), ${num(r.load.transfers)} transfers (Neo4j), ${r.load.rings.length} planted rings.</p></section></div>
      <section class="panel panel-pad" style="margin-top:14px"><h2>Latency per method (median of 50 calls)</h2>${G.bars(lat.map(([k, v]) => ({ label: k.replace(/ \(.*\)/, ""), value: v.median_ms, color: G.DBC[k.includes("Mongo") ? "mongodb" : k.includes("Cassandra") ? "cassandra" : k.includes("Neo4j") ? "neo4j" : "vector"] })), { unit: " ms", fmt: (v) => Number(v).toFixed(2), label: 240 })}
        ${G.table(["Method", "median", "p95", "min", "max"], lat.map(([k, v]) => [esc(k), G.ms(v.median_ms), G.ms(v.p95_ms), G.ms(v.min_ms), G.ms(v.max_ms)]), [1, 2, 3, 4])}
        <p class="small muted">Connection pooling: ${Object.entries(r.pooling).map(([k, v]) => `${G.DBN[k]}: ${esc(v)}`).join("; ")}.</p></section>
      <section class="panel panel-pad" style="margin-top:14px"><h2>Circuit breaker on the real service ${r.circuit.pass ? `<span class="pill st-ok">pass</span>` : `<span class="pill st-failed">check</span>`}</h2>
        ${G.table(["Call", "Cassandra circuit", "Cassandra result", "Degraded", "ms"], r.circuit.events.map((e) => [esc(e.call), `<b>${esc(e.cassandra)}</b>`, e.cassandra_result.ok ? "answered" : e.cassandra_result.skipped ? "skipped - not called" : "error", esc((e.degraded || []).join(", ") || "-"), G.ms(e.ms)]), [4])}
        <p class="small muted">The 30-second wait is simulated with a controllable clock (the same CircuitBreaker class, clock injected).</p></section>`;
  }

  function applied(box, st, saved) {
    const a = saved.applied;
    box.innerHTML = `${explain("Lab 16 applied to Lab 10 (mentor's rule)", `<p>The classifier billing ledger of Lab 10 (wherever it lives now - replica set, shards or the Lab 13 production set) is imported into the fraud dataset:
      each payment becomes a MongoDB transaction document and a Cassandra profile event, each Kavach user a Neo4j User node, and FraudQueryService analyses them like any other user.</p>
      <div class="row-gap">${runBtn("b-ap", "Import + analyse")}</div>`)}<div id="apres"></div>`;
    $("#b-ap").addEventListener("click", (e) => runPart(e.currentTarget, "applied"));
    if (!a) return ($("#apres").innerHTML = notRun("docker compose exec lab16 lab16 only applied"));
    $("#apres").innerHTML = `<section class="panel panel-pad" style="margin-top:14px">${a.available === false ? `<p>Billing ledger not reachable: ${esc(a.reason)}</p>` :
      `<p>${num(a.imported || 0)} payments of ${num(a.users || 0)} users from <b>${esc(a.backend || "")}</b>.</p>` + G.table(["User", "Risk", "Transactions", "Profile events", "Rings"], (a.sample || []).map((s) => [esc(s.user), `${esc(s.risk.level)} (${s.risk.score})`, s.transactions, s.profile_events, s.rings]), [2, 3, 4])}</section>`;
  }

  async function scripts(box) {
    const files = [["service", "fraud_service.py"], ["tests", "test_fraud_service.py"], ["cql", "schema.cql"], ["cypher", "schema.cypher"], ["queries", "queries.md"], ["runner", "run_lab16.py"]];
    box.innerHTML = `<section class="panel panel-pad"><div class="row-gap" style="flex-wrap:wrap">${files.map(([k, n]) => `<button class="btn small" data-f="${k}">${n}</button>`).join("")}</div><div id="fv"></div>
      <h2 style="margin-top:16px">Commands</h2>${copyCmd("docker compose exec lab16 lab16 all")}${copyCmd("docker compose exec lab16 python -m unittest -v labs/lab16/test_fraud_service.py")}${copyCmd("docker exec -it cassandra cqlsh -e \"SELECT * FROM lab16.user_profiles WHERE user_id = 'U00001' LIMIT 5\"")}${copyCmd("docker exec -it neo4j cypher-shell -u neo4j -p 'Lab16-neo4j!' \"MATCH p=(:User)-[:TRANSFERRED_TO*3]->(:User) RETURN p LIMIT 3\"")}
      <p class="small muted">Neo4j Browser: <a href="http://localhost:7474" target="_blank" rel="noopener">http://localhost:7474</a> (user neo4j, password Lab16-neo4j!).</p></section>`;
    const show = async (k) => { const r = await fetch(`/api/lab16/file/${k}`); $("#fv").innerHTML = G.code(await r.text(), 520); };
    $$("[data-f]").forEach((b) => b.addEventListener("click", () => show(b.dataset.f)));
    show("service");
  }

  return { lab16: page16 };
};

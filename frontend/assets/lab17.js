/* Kavach CTI - Lab 17: AI-native MongoDB. 17.1 RAG pipeline, activity (conversation memory), 17.2 HNSW vs IVF,
   applied to Lab 7 (Ask Kavach). Vector search runs on MongoDB Atlas Local (mongod + mongot). */
window.KavachLab17 = (h) => {
  "use strict";
  const { $, $$, esc, num, api, toast, copyCmd, intro, explain, act } = h;
  const G = window.KavachPG;
  const TABS = [["rag", "RAG pipeline (17.1)"], ["chat", "Chat with memory (activity)"], ["ann", "HNSW vs IVF (17.2)"], ["kavach", "Ask Kavach (17 on 7)"], ["code", "Code"]];
  const notRun = (cmd) => `<div class="panel empty" style="margin-top:12px"><strong>No results yet</strong> Press the button above, start Lab 17 in the <a href="#/labs">Lab control centre</a>, or run <span class="mono">${esc(cmd)}</span>.</div>`;
  let USER = null;
  try { USER = localStorage.getItem("lab17-user"); } catch (e) { /* storage blocked */ }
  if (!USER) { USER = `analyst-${Math.random().toString(36).slice(2, 7)}`; try { localStorage.setItem("lab17-user", USER); } catch (e) { /* ignore */ } }

  async function page17(page, route) {
    const tab = route.params.get("tab") || "rag";
    const [st, saved] = await Promise.all([api("/api/lab17/status").catch((e) => ({ available: false, reason: e.message })), api("/api/lab17/results").catch(() => ({}))]);
    const e = st.embedder || {}, g = st.generator || {};
    page.innerHTML = `${intro("Vector search lab 17",
      `MongoDB as an <b>AI database</b>: text is turned into <b>embeddings</b> (vectors), stored next to the text, and found by meaning with <span class="mono">$vectorSearch</span>.
       Lab 17.1 builds a <b>RAG</b> pipeline (chunk, embed, retrieve the top 3, generate an answer that cites them); the activity stores the conversation in MongoDB so follow-up
       questions work; Lab 17.2 compares <b>HNSW</b> and <b>IVF</b> indexes on 5,000 documents (latency, recall, build time). Applied to Lab 7: <b>Ask Kavach</b> answers questions about the platform's own threat reports.`,
      `<div class="row-gap"><a class="btn small" href="/api/lab17/report" target="_blank" rel="noopener">PDF report</a></div>`)}
      <div class="row-gap" style="flex-wrap:wrap;margin:14px 0 4px"><span class="pill" style="margin:0"><span class="dot ${st.available ? "ok" : "err"}"></span> Atlas Local ${esc((st.vector || {}).version || "offline")} ${st.available ? "+ mongot" : ""}</span>
        <span class="pill" style="margin:0">embeddings: ${esc(e.provider || "?")} - ${esc(e.model || "")} (${e.dims || "?"} d)</span><span class="pill" style="margin:0">generator: ${esc(g.provider || "?")} ${esc(g.model || "")}</span>
        ${st.counts ? `<span class="pill" style="margin:0">${num(st.counts.rag_chunks)} chunks &middot; ${num(st.counts.sentences)} vectors &middot; ${num(st.counts.kavach_reports)} Kavach reports</span>` : ""}</div>
      ${st.available ? "" : `<div class="banner warn" style="margin:14px 0"><div><strong>Vector search engine not reachable</strong> ${esc(st.reason || "")} - answers use exact NumPy search instead.${copyCmd("docker compose up -d lab17")}</div></div>`}
      <nav class="tabs" role="tablist">${TABS.map(([k, t]) => `<button role="tab" data-tab="${k}" class="${tab === k ? "active" : ""}">${t}</button>`).join("")}</nav><div id="tabbody"></div>`;
    $$("[data-tab]").forEach((b) => b.addEventListener("click", () => { location.hash = `#/lab17?tab=${b.dataset.tab}`; }));
    ({ rag, chat, ann, kavach, code }[tab] || rag)($("#tabbody"), st, saved || {});
  }

  async function runPart(btn, part, body = {}) {
    await act(btn, "Running", async () => { const r = await api(`/api/lab17/run/${part}`, { method: "POST", body }); toast(`${part} finished${r.pass === false ? " - check the results" : ""}`); location.reload(); });
  }
  const hit = (x, i) => `<div class="pg-hit"><b>[${i + 1}]</b> <span class="mono small">${esc(x.source || x.title || "")}${x.chunk != null ? ` #${x.chunk}` : ""}</span> <span class="muted small">score ${Number(x.score).toFixed(4)}</span><br>${esc((x.text || "").slice(0, 420))}${(x.text || "").length > 420 ? " ..." : ""}</div>`;

  function rag(box, st, saved) {
    const r = saved.lab_17_1;
    box.innerHTML = `${explain("Lab 17.1 - a simple RAG pipeline",
      `<p>(1) 5 short documents about threat intelligence are cut into <b>100-word chunks</b> with a sliding window (20 words overlap). (2) Each chunk is embedded and stored as
       <span class="mono">{text, source, embedding}</span> in <span class="mono">rag_demo.documents</span> with a <span class="mono">vectorSearch</span> index. (3) <span class="mono">retrieve()</span> embeds the question and runs
       <span class="mono">$vectorSearch</span> with numCandidates 20, limit 3. (4) The prompt "Answer based on these documents: {chunks}. Question: {query}" goes to the LLM.</p>
       <div class="row-gap"><button class="btn primary small" id="b-171">Rebuild + evaluate</button></div>`)}
      <section class="panel panel-pad" style="margin-top:14px"><h2>Retrieve</h2><div class="row-gap" style="margin:8px 0"><input id="rq" style="flex:1" value="Why do public DNS resolvers show up as false positives?"><button class="btn small" id="b-rq">Top 3 chunks</button></div><div id="rqout"></div></section>
      <div id="r171"></div>`;
    $("#b-171").addEventListener("click", (e) => runPart(e.currentTarget, "lab_17_1"));
    $("#b-rq").addEventListener("click", (e) => act(e.currentTarget, "Searching", async () => {
      const d = await api("/api/lab17/retrieve", { method: "POST", body: { query: $("#rq").value } });
      $("#rqout").innerHTML = `<p class="small muted">${esc(d.mode)} - embedding ${G.ms(d.embed_ms)}, search ${G.ms(d.search_ms)}</p>` + d.hits.map(hit).join("");
    }));
    if (!r || !r.build) return ($("#r171").innerHTML = notRun("docker compose exec lab17 lab17 only lab_17_1"));
    const b = r.build;
    $("#r171").innerHTML = `<div class="grid cols-2" style="gap:14px;margin-top:14px"><section class="panel panel-pad"><h2>Documents and chunks</h2>
      ${G.table(["Document", "Words"], b.documents.map((d) => [`<a href="/api/lab17/doc/${encodeURIComponent(d.source)}" target="_blank" rel="noopener">${esc(d.title)}</a>`, d.words]), [1])}
      <p class="small">${b.chunks} chunks, sizes ${esc(b.chunk_sizes.join(", "))} words. Embeddings: ${esc(b.embedder.provider)} ${esc(b.embedder.model)} (${b.embedder.dims} dimensions), ${b.embed_s} s.
      Index ${b.index.status === "NO_SEARCH" ? "not created (no mongot)" : `queryable after ${b.index.build_s} s`}.</p>${G.code(JSON.stringify(b.index.definition || {}, null, 1), 160)}</section>
      <section class="panel panel-pad"><h2>Retrieval evaluation: hit@1 ${r.hit_at_1}/${r.questions}, hit@3 ${r.hit_at_3}/${r.questions}</h2>
      ${G.table(["Question", "Expected", "Top 3", ""], r.evaluation.map((x) => [esc(x.question), `<span class="mono small">${esc(x.expected.slice(0, 2))}</span>`, `<span class="mono small">${x.top3.map((s) => esc(s.slice(0, 2))).join(", ")}</span>`, x.hit_at_3 ? `<span class="dot ok"></span>` : `<span class="dot err"></span>`]))}
      <p class="small muted">Mode: ${esc(r.mode)}. Generator: ${esc(r.generator.provider)} (${esc(r.generator.model)}).</p></section></div>
      <section class="panel panel-pad" style="margin-top:14px"><h2>One full round trip</h2><p><b>Q:</b> ${esc(r.demo.question)}</p><p><b>A:</b> ${esc(r.demo.answer)}</p>
      <details><summary class="muted">The prompt sent to the generator</summary>${G.code(r.demo.prompt, 360)}</details></section>`;
  }

  async function chat(box, st, saved) {
    const m = saved.memory;
    box.innerHTML = `${explain("Activity - conversation memory",
      `<p>Every question and answer is stored in <span class="mono">rag_demo.conversations {user_id, timestamp, role, content, sources}</span> (index user_id + timestamp).
       Before answering, the last <b>5 exchanges</b> (10 messages) are read back and put into the prompt; a follow-up such as "how can we reduce them?" is rewritten with the previous question
       before retrieval, otherwise "them" would find nothing.</p><div class="row-gap"><button class="btn primary small" id="b-mem">Run the scripted 6-turn test</button></div>`)}
      <section class="panel panel-pad" style="margin-top:14px"><div class="panel-head" style="padding:0 0 8px"><h2>Chat (user <span class="mono">${esc(USER)}</span>)</h2><button class="btn small" id="b-forget">Forget me</button></div>
        <div class="pg-chat" id="chatbox"></div>
        <form class="row-gap" id="chatform" style="margin-top:10px"><input id="cq" style="flex:1" placeholder="Ask about threat intelligence - then ask a follow-up" autocomplete="off"><button class="btn primary small">Ask</button></form></section><div id="rmem"></div>`;
    $("#b-mem").addEventListener("click", (e) => runPart(e.currentTarget, "memory"));
    const draw = async () => {
      const hs = await api(`/api/lab17/history/${encodeURIComponent(USER)}`).catch(() => []);
      $("#chatbox").innerHTML = hs.map((x) => `<div class="pg-msg ${x.role}">${esc(x.content)}<span class="meta">${x.role} &middot; ${esc(String(x.timestamp).slice(11, 19))}</span></div>`).join("") || `<p class="muted small">No messages yet. Try "What causes false positives in threat feeds?" and then "How do we reduce them?"</p>`;
      $("#chatbox").scrollTop = 1e9;
    };
    $("#chatform").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#cq").value.trim(); if (!q) return;
      act($("#chatform button"), "Thinking", async () => { const d = await api("/api/lab17/ask", { method: "POST", body: { user_id: USER, question: q } }); $("#cq").value = "";
        await draw(); toast(`Retrieved with: "${esc(d.retrieval_query)}" (${d.history_used} messages of history, ${esc(d.provider)})`, 6000); }); });
    $("#b-forget").addEventListener("click", async () => { await api(`/api/lab17/forget/${encodeURIComponent(USER)}`, { method: "POST" }); draw(); });
    draw();
    if (!m || !m.turns) return ($("#rmem").innerHTML = notRun("docker compose exec lab17 lab17 only memory"));
    $("#rmem").innerHTML = `<section class="panel panel-pad" style="margin-top:14px"><h2>Scripted test ${m.pass ? `<span class="pill st-ok">pass</span>` : `<span class="pill st-failed">check</span>`}</h2>
      ${G.table(["#", "Question", "History used", "Rewritten retrieval query", "Top document"], m.turns.map((t, i) => [i + 1, esc(t.question), t.history_used, `<span class="small">${esc(t.retrieval_query)}</span>`, `<span class="mono small">${esc(t.top_source || "")}</span>`]), [0, 2])}
      <p class="small muted">${m.stored_messages} messages stored; the window read back is ${m.window} messages (limit ${m.window_limit}).</p></section>`;
  }

  function ann(box, st, saved) {
    const r = saved.lab_17_2;
    box.innerHTML = `${explain("Lab 17.2 - HNSW vs IVF",
      `<p><b>HNSW</b> (Hierarchical Navigable Small World) is the graph index of Atlas Vector Search: each vector links to its nearest neighbours on several layers; a query walks the graph.
       <span class="mono">maxEdges</span> (M) and <span class="mono">numEdgeCandidates</span> (efConstruction) set how dense the graph is, <span class="mono">numCandidates</span> how far a query explores.
       <b>IVF</b> (inverted file) clusters the vectors with k-means into <span class="mono">nlist</span> lists; a query scans only the <span class="mono">nprobe</span> closest lists. Atlas has no IVF index type, so IVF is built
       <i>inside MongoDB</i>: centroids in <span class="mono">ivf_centroids</span>, an indexed list id on every document. Ground truth = exact brute force; recall@20 = |found &cap; true top 20| / 20.</p>
       <div class="row-gap"><button class="btn primary small" id="b-172">Run Lab 17.2</button><label class="small"><input type="checkbox" id="q172"> quick (1,500 docs)</label></div>`)}
      <section class="panel panel-pad" style="margin-top:14px"><h2>Semantic search over the 5,000 sentences</h2><div class="row-gap" style="margin:8px 0"><input id="sq" style="flex:1" value="ransomware hitting hospitals through a VPN flaw"><button class="btn small" id="b-sq">Search</button></div><div id="sqout"></div></section><div id="r172"></div>`;
    $("#b-172").addEventListener("click", (e) => runPart(e.currentTarget, "lab_17_2", { quick: $("#q172").checked }));
    $("#b-sq").addEventListener("click", (e) => act(e.currentTarget, "Searching", async () => {
      const d = await api("/api/lab17/search", { method: "POST", body: { query: $("#sq").value } });
      $("#sqout").innerHTML = `<p class="small muted">index ${esc(d.index)} - ${G.ms(d.ms)}</p>` + G.table(["Score", "Sentence"], d.hits.map((x) => [Number(x.score).toFixed(4), esc(x.text)]), [0]);
    }));
    if (!r || !r.rows) return ($("#r172").innerHTML = notRun("docker compose exec lab17 lab17 only lab_17_2"));
    const colors = ["#ffb000", "#e8590c", "#d6336c", "#13aa52", "#1287b1", "#7048e8"];
    const series = [...Object.entries(r.curve.HNSW || {}).map(([k, p], i) => ({ name: `HNSW ${k.replace("hnsw_", "")}`, color: colors[i], points: p })),
      ...Object.entries(r.curve.IVF || {}).map(([k, p], i) => ({ name: `IVF ${k.replace("ivf_", "")}`, color: colors[3 + i], points: p }))];
    $("#r172").innerHTML = `<section class="panel panel-pad" style="margin-top:14px"><h2>Latency - recall trade-off</h2>${G.curve(series)}
      <p class="small muted">${num(r.n)} documents (${num(r.corpus.real)} sentences from the Kavach reports, ${num(r.corpus.generated)} generated), ${r.dims} dimensions (${esc(r.embedder.provider)}), ${r.queries} queries per point,
      inserted in batches of 500 in ${r.insert_s} s. Up and to the left is better.</p></section>
      <section class="panel panel-pad" style="margin-top:14px"><h2>Comparison table</h2>${G.table(["Index type", "Parameters", "Build time (s)", "Avg latency (ms)", "p95 (ms)", "Recall@20 (%)"],
        r.rows.map((x) => [esc(x.index), `<span class="small">${esc(x.params)}</span>`, x.build_s == null ? "-" : x.build_s, Number(x.avg_ms).toFixed(2), Number(x.p95_ms).toFixed(2), `<b>${Number(x.recall).toFixed(1)}</b>`]), [2, 3, 4, 5])}</section>
      ${explain("Recommendation (from these measurements)", r.recommendation.text.map((t) => `<p>${esc(t)}</p>`).join(""))}
      ${(r.notes || []).length ? explain("Notes", r.notes.map((t) => `<p>${esc(t)}</p>`).join("")) : ""}
      ${r.classroom_sample && Object.keys(r.classroom_sample).length ? explain("The classroom sample, tested as written", Object.entries(r.classroom_sample).map(([k, v]) => `<p><b>${k === "dims_mismatch" ? "Index with numDimensions 1536, vectors with " + esc(v.vector_dims || "?") + " dimensions" : "The solution's indexOption {type: 'ivf'}"}:</b> ${esc(v.result)}</p>`).join("")) : ""}`;
  }

  function kavach(box, st, saved) {
    const a = saved.applied;
    box.innerHTML = `${explain("Lab 17 applied to Lab 7 - Ask Kavach", `<p>The newest Lab 7 threat reports (title + summary) are embedded and stored in <span class="mono">kavach_rag.reports</span> with a vector index. A question is answered from
      the reports that are closest in meaning - not only those that share its words - and every answer cites them.</p><div class="row-gap"><button class="btn primary small" id="b-kv">Re-index the Kavach reports</button></div>`)}
      <section class="panel panel-pad" style="margin-top:14px"><form class="row-gap" id="kf"><input id="kq" style="flex:1" value="Which ransomware attacks hit hospitals recently?"><button class="btn primary small">Ask Kavach</button></form><div id="kout"></div></section><div id="rkv"></div>`;
    $("#b-kv").addEventListener("click", (e) => runPart(e.currentTarget, "applied"));
    $("#kf").addEventListener("submit", (e) => { e.preventDefault(); act($("#kf button"), "Searching", async () => {
      const d = await api("/api/lab17/kavach", { method: "POST", body: { question: $("#kq").value } });
      $("#kout").innerHTML = `<p style="margin-top:12px"><b>Answer</b> <span class="muted small">(${esc(d.provider)}, ${esc(d.mode)}, search ${G.ms(d.search_ms)})</span><br>${esc(d.answer)}</p>` +
        d.hits.map((x, i) => `<div class="pg-hit"><b>[${i + 1}]</b> ${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)} <span class="muted small">score ${Number(x.score).toFixed(4)} ${esc(String(x.published || "").slice(0, 10))}</span></div>`).join("");
    }); });
    if (a && a.build) $("#rkv").innerHTML = `<section class="panel panel-pad" style="margin-top:14px"><h2>Indexed: ${num(a.build.reports)} reports in ${a.build.seconds} s</h2>${G.table(["Question", "Top report"], a.questions.map((q) => [esc(q.question), esc(q.top[0] || "-")]))}</section>`;
  }

  async function code(box) {
    const files = [["pipeline", "backend/app/lab17.py"], ["ai", "backend/app/ai.py (embeddings + generator)"], ["runner", "run_lab17.py"]];
    box.innerHTML = `<section class="panel panel-pad"><div class="row-gap" style="flex-wrap:wrap">${files.map(([k, n]) => `<button class="btn small" data-f="${k}">${n}</button>`).join("")}</div><div id="fv"></div>
      <h2 style="margin-top:16px">Commands</h2>${copyCmd("docker compose exec lab17 lab17 all")}${copyCmd("docker exec -it atlas-local mongosh rag_demo --eval \"db.documents.getSearchIndexes()\"")}
      <p class="small muted">Optional: set ANTHROPIC_API_KEY (or OLLAMA_URL for a local model, VOYAGE_API_KEY for Voyage embeddings) in .env - without them the built-in generator and the local MiniLM model are used.</p></section>`;
    const show = async (k) => { const r = await fetch(`/api/lab17/file/${k}`); $("#fv").innerHTML = G.code(await r.text(), 520); };
    $$("[data-f]").forEach((b) => b.addEventListener("click", () => show(b.dataset.f)));
    show("pipeline");
  }

  return { lab17: page17 };
};

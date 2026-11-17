/* Kavach CTI - SaaS lab 15: the TaskTracker product (a real multi-tenant UI on /tasktracker), the Lab 15.1 tests (isolation,
   rate limiting, RBAC), Lab 15.2 migration + comparison, the design, and Lab 15 applied to Labs 14..7. */
window.KavachLab15 = (h) => {
  "use strict";
  const { $, $$, esc, num, api, toast, copyCmd, onLeave, intro, explain, act, dot } = h;
  const TT = "/tasktracker";
  const S = { token: null, me: null, rate: null };
  const ms = (x) => (x == null ? "-" : `${Number(x).toFixed(x < 10 ? 2 : 1)} ms`);
  const yes = (v, t = "pass", f = "FAIL") => `${dot(!!v)}${v ? t : f}`;
  const code = (c) => `<span class="pill ${c < 300 ? "st-ok" : c === 429 ? "st-warn" : "st-failed"}">${c}</span>`;
  const logBox = (l) => (l && l.length ? `<details class="l14-log"><summary>Output (${l.length} lines)</summary><pre class="term" style="max-height:320px">${esc(l.join("\n"))}</pre></details>` : "");
  const notRun = () => `<div class="panel empty"><strong>No results yet</strong> Press the button above, start Lab 15 in the <a href="#/labs">Lab control centre</a>, or run <span class="mono">docker compose exec lab15 lab15 all</span>.</div>`;

  async function tt(path, opts = {}) {
    const r = await fetch(TT + path, { method: opts.method || "GET", headers: { "Content-Type": "application/json", ...(S.token ? { Authorization: `Bearer ${S.token}` } : {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined });
    const lim = r.headers.get("X-RateLimit-Limit");
    if (lim) S.rate = { limit: +lim, remaining: +r.headers.get("X-RateLimit-Remaining"), reset: +r.headers.get("X-RateLimit-Reset") };
    const body = r.status === 204 ? null : await r.json().catch(() => null);
    if (!r.ok) { const e = new Error((body && body.detail) || r.statusText); e.status = r.status; e.retry = r.headers.get("Retry-After"); throw e; }
    return body;
  }

  // ================================================================== page
  const TABS = [["app", "TaskTracker app"], ["crud", "Isolation + CRUD (15.1)"], ["rate", "Rate limiting"], ["rbac", "RBAC (activity)"],
    ["mig", "Migration (15.2)"], ["design", "Design + applied to 14..7"], ["scripts", "Scripts + API"]];

  async function page15(page, route) {
    const tab = route.params.get("tab") || "app";
    const [st, saved] = await Promise.all([api("/api/lab15/status").catch((e) => ({ available: false, reason: e.message, demo_users: [] })),
      api("/api/lab15/results").catch(() => ({}))]);
    page.innerHTML = `${intro("SaaS lab 15",
      `Lab 15 turns MongoDB into a <b>product</b>: <b>TaskTracker</b>, a multi-tenant task SaaS. Every request carries a JWT with the tenant; every query is filtered by it;
       each tenant has a <b>rate limit</b> counted in MongoDB; <b>admins</b> manage all tasks of their company, <b>members</b> only their own. Lab 15.2 migrates the data from
       one shared collection to a <b>collection per tenant</b> and measures both. Following the mentor's rule the product runs on the <b>Lab 13 production set</b>, with a
       least-privilege user, a Lab 10 transaction for signup, and Lab 14 backups and monitoring.`,
      `<div class="row-gap"><a class="btn small" href="/tasktracker/docs" target="_blank" rel="noopener">API docs (Swagger)</a><a class="btn small" href="/api/lab15/report" target="_blank" rel="noopener">PDF report</a></div>`)}
      ${st.available ? (st.ready ? `<section class="panel" style="margin-top:18px"><div class="stat-line" style="border-top:none">
          <div><b>${num((st.counts || {}).tenants)}</b><span>tenants</span></div><div><b>${num((st.counts || {}).users)}</b><span>users</span></div>
          <div><b>${num((st.counts || {}).tasks)}</b><span>tasks</span></div><div><b class="mono" style="font-size:15px">tasktracker</b><span>database on prod rs0</span></div>
          <div><b>${(st.rate_windows || []).length}</b><span>active rate-limit counters</span></div></div></section>`
        : `<div class="banner warn" style="margin:18px 0"><div><strong>TaskTracker is not set up yet.</strong> ${esc(st.reason || "")} <button class="btn small" id="b-seed">Set it up now</button></div></div>`)
        : `<div class="banner err" style="margin:18px 0"><div><strong>Production replica set not available.</strong> ${esc(st.reason || "")}<div style="margin-top:6px">Start Labs 7 to 15 with:</div>${copyCmd("docker compose up -d lab15")}</div></div>`}
      <nav class="tabs" role="tablist">${TABS.map(([k, t]) => `<button role="tab" data-tab="${k}" class="${tab === k ? "active" : ""}">${t}</button>`).join("")}</nav><div id="tabbody"></div>`;
    $$("[data-tab]").forEach((b) => b.addEventListener("click", () => { location.hash = `#/lab15?tab=${b.dataset.tab}`; }));
    const seed = $("#b-seed");
    if (seed) seed.addEventListener("click", (e) => act(e.currentTarget, "Creating", async () => { await api("/api/lab15/seed", { method: "POST" }); location.reload(); }));
    ({ app: appTab, crud: crudTab, rate: rateTab, rbac: rbacTab, mig: migTab, design: designTab, scripts: scriptsTab }[tab] || appTab)($("#tabbody"), st, saved || {});
  }

  // ================================================================== the product
  function appTab(box, st) {
    if (!S.token) return loginView(box, st);
    box.innerHTML = `<section class="panel l15-top"><div><b>${esc(S.me.tenant_name || S.me.tenant_id)}</b><span class="pill">${esc(S.me.plan || "")} plan</span>
        <span class="muted">signed in as</span> <b>${esc(S.me.name || S.me.email)}</b> <span class="role ${S.me.role === "admin" ? "r-PRIMARY" : "r-SECONDARY"}">${esc(S.me.role)}</span></div>
        <div class="l15-rate" id="ratebox"></div><button class="btn small" id="b-out">Sign out</button></section>
      <div class="l15-grid"><div>
        <section class="panel panel-pad"><h2 style="margin-bottom:10px">New task</h2><div class="l15-form">
          <input id="f-title" placeholder="Title" maxlength="200"><input id="f-desc" placeholder="Description (optional)">
          <select id="f-pri"><option value="low">low</option><option value="medium" selected>medium</option><option value="high">high</option></select>
          <button class="btn primary" id="b-add">Add task</button></div>
          <div class="row-gap" style="margin-top:10px;flex-wrap:wrap"><label class="small"><input type="checkbox" id="f-mine"> only my tasks</label>
          <button class="btn small" id="b-burst" title="Fires 70 requests to show the limit">Stress the rate limit (70 requests)</button></div></section>
        <div class="l15-board" id="board"></div></div>
        <aside><section class="panel panel-pad" id="side"></section></aside></div>`;
    $("#b-out").addEventListener("click", () => { S.token = null; S.me = null; appTab(box, st); });
    $("#b-add").addEventListener("click", (e) => act(e.currentTarget, "Saving", async () => {
      const title = $("#f-title").value.trim();
      if (!title) return toast("A title is required");
      await tt("/tasks/", { method: "POST", body: { title, description: $("#f-desc").value, priority: $("#f-pri").value } });
      $("#f-title").value = ""; $("#f-desc").value = ""; await board();
    }));
    $("#f-mine").addEventListener("change", board);
    $("#b-burst").addEventListener("click", (e) => act(e.currentTarget, "Firing", async () => {
      const codes = { 200: 0, 429: 0 };
      for (let i = 0; i < 70; i++) { try { await tt("/me"); codes[200]++; } catch (x) { if (x.status === 429) codes[429]++; } }
      rate(); toast(`70 requests: ${codes[200]} accepted, ${codes[429]} refused with HTTP 429 (limit ${S.rate ? S.rate.limit : "?"} per minute for the whole tenant)`);
    }));
    board(); side();
  }

  function rate() {
    const r = S.rate, b = $("#ratebox");
    if (!b || !r) return;
    const used = r.limit - r.remaining, pct = Math.min(100, (100 * used) / r.limit);
    b.innerHTML = `<span class="small muted">rate limit (tenant, this minute)</span><div class="l14-meter" style="width:180px"><i style="width:${pct}%;background:${pct > 90 ? "var(--mal)" : pct > 70 ? "var(--med)" : "var(--ok)"}"></i></div>
      <span class="small"><b>${num(used)}</b> / ${num(r.limit)} used &middot; resets ${new Date(r.reset * 1000).toLocaleTimeString("en-IN", { hour12: false })}</span>`;
  }

  async function board() {
    const el = $("#board");
    if (!el) return;
    try {
      const tasks = await tt(`/tasks/?limit=200${$("#f-mine") && $("#f-mine").checked ? "&mine=true" : ""}`);
      rate();
      const col = (s, t) => `<section class="panel l15-col"><div class="panel-head"><h2>${t}</h2><span class="muted">${tasks.filter((x) => x.status === s).length}</span></div><div class="l15-cards">
        ${tasks.filter((x) => x.status === s).map((x) => `<div class="l15-card p-${x.priority}"><b>${esc(x.title)}</b>${x.description ? `<span>${esc(x.description)}</span>` : ""}
          <div class="l15-meta"><span class="pill">${esc(x.priority)}</span><span class="muted small">${esc(x.owner || "")}</span></div>
          <div class="l15-act">${x.can_edit ? ["open", "in_progress", "done"].filter((n) => n !== s).map((n) => `<button class="btn small" data-mv="${x.id}" data-to="${n}">&rarr; ${n.replace("_", " ")}</button>`).join("") + `<button class="btn small" data-del="${x.id}">Delete</button>`
            : `<span class="small muted">&#128274; read-only: ${esc(S.me.role)}s change only their own tasks</span><button class="btn small" data-try="${x.id}">Try to edit</button>`}</div></div>`).join("") || `<p class="muted small" style="margin:0">Nothing here.</p>`}</div></section>`;
      el.innerHTML = col("open", "Open") + col("in_progress", "In progress") + col("done", "Done");
      $$("[data-mv]").forEach((b) => b.addEventListener("click", async () => { try { await tt(`/tasks/${b.dataset.mv}`, { method: "PATCH", body: { status: b.dataset.to } }); board(); } catch (e) { toast(`HTTP ${e.status}: ${esc(e.message)}`); } }));
      $$("[data-del]").forEach((b) => b.addEventListener("click", async () => { try { await tt(`/tasks/${b.dataset.del}`, { method: "DELETE" }); board(); } catch (e) { toast(`HTTP ${e.status}: ${esc(e.message)}`); } }));
      $$("[data-try]").forEach((b) => b.addEventListener("click", async () => { try { await tt(`/tasks/${b.dataset.try}`, { method: "PATCH", body: { status: "done" } }); board(); } catch (e) { toast(`The API refused it: HTTP ${e.status} - ${esc(e.message)}`); } }));
    } catch (e) { el.innerHTML = `<div class="banner err"><div>HTTP ${e.status || ""}: ${esc(e.message)}${e.retry ? ` - retry in ${e.retry} s` : ""}</div></div>`; }
  }

  async function side() {
    const el = $("#side");
    try {
      const s = await tt("/stats");
      let html = `<h2 style="margin-bottom:8px">My tenant</h2><div class="kvr"><span>Tasks</span><b>${num(s.total)}</b></div>
        ${Object.entries(s.by_status).map(([k, v]) => `<div class="kvr"><span>${esc(k.replace("_", " "))}</span><b>${num(v)}</b></div>`).join("")}
        <p class="muted small" style="margin:10px 0 0">Every number here comes from an aggregation whose first stage is <span class="mono">$match: {tenant_id: "${esc(S.me.tenant_id)}"}</span> - taken from your token, never from the request.</p>`;
      if (S.me.role === "admin") {
        const us = await tt("/users/");
        html += `<h2 style="margin:16px 0 8px">Team (admin only)</h2>${us.map((u) => `<div class="kvr"><span>${esc(u.name || u.email)}</span><b><span class="role ${u.role === "admin" ? "r-PRIMARY" : "r-SECONDARY"}">${esc(u.role)}</span></b></div>`).join("")}
          <div class="l15-form" style="margin-top:8px"><input id="u-mail" placeholder="new.member@${esc(S.me.tenant_id)}.test"><button class="btn small" id="b-user">Add member</button></div>`;
      } else html += `<p class="muted small" style="margin:12px 0 0">Members cannot see the user list (GET /users/ answers 403).</p>`;
      el.innerHTML = html;
      const bu = $("#b-user");
      if (bu) bu.addEventListener("click", (e) => act(e.currentTarget, "Adding", async () => { const m = $("#u-mail").value.trim(); if (!m) return; await tt("/users/", { method: "POST", body: { email: m, name: m.split("@")[0], password: "Lab15-demo!" } }); toast(`${esc(m)} added (password Lab15-demo!)`); side(); }));
      rate();
    } catch (e) { el.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
  }

  function loginView(box, st) {
    const by = {};
    (st.demo_users || []).forEach((u) => { (by[u.tenant_id] = by[u.tenant_id] || { name: u.tenant, plan: u.plan, users: [] }).users.push(u); });
    box.innerHTML = `<section class="panel panel-pad"><h2 style="margin-bottom:4px">Sign in to TaskTracker</h2><p class="muted" style="margin:0 0 14px">Three companies share one database. Pick a user - every demo password is <span class="mono">${esc(st.password || "Lab15-demo!")}</span>. Sign in as two users of different companies in two browser windows to see the isolation.</p>
      <div class="l15-tenants">${Object.entries(by).map(([tid, t]) => `<div class="l15-tenant"><b>${esc(t.name)}</b><span class="muted small">tenant <span class="mono">${esc(tid)}</span> &middot; ${esc(t.plan)} plan</span>
        ${t.users.map((u) => `<button class="btn small l15-login" data-t="${esc(tid)}" data-e="${esc(u.email)}"><span>${esc(u.name)}</span><span class="role ${u.role === "admin" ? "r-PRIMARY" : "r-SECONDARY"}">${esc(u.role)}</span></button>`).join("")}</div>`).join("")}</div>
      <details style="margin-top:14px"><summary class="muted">Or sign in by hand / sign up a new company</summary><div class="l15-form" style="margin-top:10px"><input id="l-t" placeholder="tenant (e.g. acme)"><input id="l-e" placeholder="email"><input id="l-p" type="password" placeholder="password"><button class="btn" id="b-login">Sign in</button></div>
      <div class="l15-form" style="margin-top:8px"><input id="s-t" placeholder="new tenant id (a-z, 0-9, -)"><input id="s-n" placeholder="company name"><input id="s-e" placeholder="admin email"><input id="s-p" type="password" placeholder="password (8+)"><button class="btn" id="b-signup">Create company</button></div>
      <p class="muted small" style="margin:6px 0 0">Signup inserts the tenant and its admin in ONE MongoDB transaction (Lab 10).</p></details></section>`;
    const go = async (tenant_id, email, password) => {
      const r = await tt("/auth/login", { method: "POST", body: { tenant_id, email, password } });
      S.token = r.access_token; S.me = await tt("/me"); appTab(box, st);
    };
    $$(".l15-login").forEach((b) => b.addEventListener("click", (e) => act(e.currentTarget, "Signing in", () => go(b.dataset.t, b.dataset.e, st.password || "Lab15-demo!"))));
    $("#b-login").addEventListener("click", (e) => act(e.currentTarget, "Signing in", () => go($("#l-t").value.trim(), $("#l-e").value.trim(), $("#l-p").value)));
    $("#b-signup").addEventListener("click", (e) => act(e.currentTarget, "Creating", async () => {
      await tt("/auth/signup", { method: "POST", body: { tenant_id: $("#s-t").value.trim(), tenant_name: $("#s-n").value.trim(), admin_email: $("#s-e").value.trim(), admin_name: "Admin", password: $("#s-p").value } });
      await go($("#s-t").value.trim(), $("#s-e").value.trim(), $("#s-p").value);
    }));
  }

  // ================================================================== test tabs
  const testTable = (rows) => `<div style="overflow-x:auto"><table class="table"><thead><tr><th>Test (real HTTP request)</th><th>Expected</th><th>Got</th><th></th></tr></thead><tbody>
    ${(rows || []).map((r) => `<tr><td>${esc(r.test)}${r.detail ? `<div class="small muted">${esc(r.detail)}</div>` : ""}</td><td>${typeof r.expected === "number" ? code(r.expected) : esc(String(r.expected))}</td><td>${typeof r.got === "number" ? code(r.got) : esc(String(r.got))}</td><td>${yes(r.pass)}</td></tr>`).join("")}</tbody></table></div>`;

  function runner(box, saved, key, part, title, btn, view, help) {
    box.innerHTML = `<section class="panel"><div class="panel-head"><h2>${title}</h2><button class="btn small primary" id="b-run">${btn}</button></div><div class="panel-pad" id="out">${saved[key] ? view(saved[key]) : notRun()}</div></section><div id="lg"></div>${help || ""}`;
    $("#b-run").addEventListener("click", (e) => act(e.currentTarget, "Running", async () => { const r = await api(`/api/lab15/run/${part}`, { method: "POST", body: {} }); $("#out").innerHTML = view(r); $("#lg").innerHTML = logBox(r.log); }));
  }

  function crudView(r) {
    const s = r.sample || {}, e = r.explain || {};
    return `<div class="stat-line" style="border-top:none;margin-top:0"><div><b>${(r.tests || []).filter((x) => x.pass).length}/${(r.tests || []).length}</b><span>tests passed</span></div>
      <div><b>${esc(e.stage || "-")}</b><span>list query, index ${esc(e.index || "")}</span></div><div><b>${num(e.docs_examined)} / ${num(e.returned)}</b><span>docs examined / returned (of ${num(e.total_tasks)} in the collection)</span></div></div>
      ${testTable(r.tests)}
      <h3 style="margin:18px 0 8px">The classroom sample, run unchanged against the same database</h3>
      <table class="table"><tbody><tr><td>POST /tasks/</td><td>${code(s.create_status)} but the task <b>was stored</b> (${yes(s.stored_despite_error, "yes", "no")}) - insert_one adds an ObjectId <span class="mono">_id</span> to the dict and FastAPI cannot turn it into JSON. A client that retries creates duplicates.</td></tr>
        <tr><td>priority "urgent!!"</td><td>stored: ${yes(s.invalid_priority_stored, "yes", "no")} - plain <span class="mono">str</span>, no validator</td></tr>
        <tr><td>GET /tasks/?tenant_id=acme with no login</td><td>${code(s.read_other_tenant_without_login)} - returned ${esc((s.leaked_titles || []).join(", "))}: anyone can read any tenant</td></tr></tbody></table>`;
  }
  function crudTab(box, st, saved) {
    runner(box, saved, "lab_15_1", "crud", "CRUD with tenant_id on every query - and every way to break it", "Run the 15.1 tests", crudView,
      explain("How isolation is enforced", `<ul class="plain"><li>The tenant comes <b>only from the verified JWT</b> (FastAPI dependency <span class="mono">current_user</span>), never from the URL or the body.</li>
        <li>Every query is built by <b>one function</b>, <span class="mono">scoped(user, extra)</span> = <span class="mono">{"tenant_id": user.tenant_id, ...extra}</span>. One place to review, impossible to forget per endpoint.</li>
        <li>Another tenant's task answers <b>404, not 403</b>: the API never confirms that an id exists elsewhere.</li><li>The user is re-read from MongoDB on every request, with the tenant from the token: a token whose tenant was swapped, a deleted user or an expired token gets 401.</li>
        <li>A <span class="mono">$jsonSchema</span> validator on the collection rejects bad data even if the API is bypassed - and the app user may not change it (collMod refused).</li></ul>`));
  }

  function rateView(r) {
    const s = r.burst.series || [];
    const W = 640, bw = W / s.length;
    const bars = s.map((x, i) => `<rect x="${(i * bw).toFixed(1)}" y="${x.code === 200 ? 10 : 40}" width="${(bw - 1).toFixed(1)}" height="${x.code === 200 ? 50 : 20}" style="fill:${x.code === 200 ? "var(--ok)" : "var(--mal)"}"><title>request ${x.i}: HTTP ${x.code}</title></rect>`).join("");
    return `<div class="l14-kpis">${kpi("Allowed", r.burst.ok, `of ${r.burst.requests} (limit ${r.limit}/min)`)}${kpi("Refused (429)", r.burst.limited, `Retry-After ${r.burst.retry_after} s`, true)}
      ${kpi("Other tenant", r.other_tenant_status, "at the same moment")}${kpi("Concurrent test", `${r.concurrency.allowed}/${r.concurrency.requests}`, `${r.concurrency.threads} threads, exactly the limit`)}</div>
      <svg viewBox="0 0 ${W} 70" class="lchart" style="margin-top:14px">${bars}</svg><div class="lg"><span><i style="background:var(--ok)"></i>HTTP 200</span><span><i style="background:var(--mal)"></i>HTTP 429</span></div>
      <h3 style="margin:16px 0 6px">The counter document in MongoDB</h3><pre class="term">${esc(JSON.stringify(r.counter_doc, null, 2))}</pre>
      <p class="small muted">TTL index ${esc(JSON.stringify(r.ttl_index.key))}, expireAfterSeconds ${r.ttl_index.expireAfterSeconds}: old windows delete themselves. Headers returned: ${esc(Object.entries(r.burst.headers || {}).map(([k, v]) => `${k}: ${v}`).join(", "))}</p>`;
  }
  function kpi(l, v, s, bad) { return `<div class="l14-kpi ${bad ? "bad" : ""}"><span>${l}</span><b>${v}</b><em>${s}</em></div>`; }
  function rateTab(box, st, saved) {
    runner(box, saved, "rate", "rate", "Rate limiting with MongoDB as the counter store", "Run the rate-limit test (~15 s)", rateView,
      explain("findAndModify, atomically", `<p style="margin:0">One document per tenant per minute: <span class="mono">{_id: "acme:1794474720", count, ts}</span>. Each request runs
        <span class="mono">find_one_and_update({_id}, {$inc: {count: 1}, $setOnInsert: {...}}, upsert=True, return_document=AFTER)</span> - the increment and the read are one atomic operation, so 200 simultaneous
        requests can never let more than the limit through (measured: exactly 50). A TTL index on <span class="mono">ts</span> removes old windows. The limit is per tenant and depends on the plan (free 60, pro 600 per minute).</p>`));
  }

  function rbacView(r) { return testTable(r.tests); }
  function rbacTab(box, st, saved) {
    runner(box, saved, "rbac", "rbac", "Admins manage all tasks, members only their own", "Run the RBAC tests", rbacView,
      explain("Where the role is checked", `<p style="margin:0">The role is a field of the user document (<span class="mono">admin | member</span>). Writes go through <span class="mono">scoped(user, {_id}, write=True)</span>,
        which adds <span class="mono">owner_id</span> for members, so the database itself refuses to match someone else's task. Admin-only endpoints use the dependency <span class="mono">require_admin</span>.
        Because the user is re-read on every request, promoting or demoting someone takes effect immediately, without a new token.</p>`));
  }

  // ================================================================== 15.2
  function bars3(rows) {
    const items = [];
    rows.forEach((c) => {
      [["list 20", "list_20"], ["create", "create"]].forEach(([n, k]) => {
        items.push({ l: `${c.scale}: ${n}`, a: c[k]["document-level"].median_ms, b: c[k]["collection-per-tenant"].median_ms });
      });
      items.push({ l: `${c.scale}: analytics`, a: c.analytics["document-level"].median_ms, b: c.analytics["collection-per-tenant ($unionWith)"].median_ms });
    });
    const W = 640, L = 250, mx = Math.max(...items.flatMap((i) => [i.a, i.b]));
    const g = items.map((it, i) => { const y = i * 30; const wa = ((W - L - 60) * it.a) / mx, wb = ((W - L - 60) * it.b) / mx;
      return `<text x="${L - 6}" y="${y + 15}" text-anchor="end" class="ax">${esc(it.l)}</text><rect x="${L}" y="${y + 3}" width="${Math.max(wa, 1)}" height="10" style="fill:var(--accent)"/><text x="${L + wa + 4}" y="${y + 12}" class="ax">${it.a.toFixed(2)}</text>
        <rect x="${L}" y="${y + 15}" width="${Math.max(wb, 1)}" height="10" style="fill:var(--low)"/><text x="${L + wb + 4}" y="${y + 24}" class="ax">${it.b.toFixed(2)}</text>`; }).join("");
    return `<svg viewBox="0 0 ${W} ${items.length * 30 + 4}" class="lchart" style="max-width:760px">${g}</svg><div class="lg"><span><i style="background:var(--accent)"></i>document-level (one collection)</span><span><i style="background:var(--low)"></i>collection-per-tenant</span><span class="muted">median ms</span></div>`;
  }
  function migView(m) {
    if (!m || !m.validate) return notRun();
    const v = m.validate, b = m.backup || {}, sc = m.schema || {};
    return `<h3 style="margin:0 0 8px">1-3. Seed, migrate, validate</h3><p style="margin:0 0 8px">${num(m.seed.documents)} documents in <span class="mono">tasks</span> &rarr; ${v.tenants.length} collections with <b>${esc(m.migrate.method)}</b> in ${m.migrate.seconds} s (Python loop: ${m.migrate.loop_seconds} s).
      Indexes right after $out: <span class="mono">${esc((m.migrate.tenants[0].indexes_after_out || []).join(", "))}</span> - $out does not copy the source's indexes, so <span class="mono">created</span> is built afterwards.</p>
      <div style="overflow-x:auto"><table class="table"><thead><tr><th>Tenant</th><th class="num">Shared</th><th>Collection</th><th class="num">Migrated</th><th class="num">Foreign docs</th><th>Same _ids</th><th>sha256 (shared = tenant)</th><th></th></tr></thead><tbody>
      ${v.tenants.map((r) => `<tr><td>${esc(r.tenant)}</td><td class="num">${r.shared_count}</td><td class="mono small">tasks_tenant_${esc(r.tenant)}</td><td class="num">${r.tenant_collection_count}</td><td class="num">${r.foreign_docs}</td><td>${yes(r.same_ids, "yes", "no")}</td><td class="mono small">${esc(r.sha256_shared)} = ${esc(r.sha256_tenant)}</td><td>${yes(r.ok)}</td></tr>`).join("")}</tbody></table></div>
      <p class="small">Negative control: ${esc(m.leak_check.injected)} &rarr; detected: ${yes(m.leak_check.detected, "yes", "NO")}. Total ${v.total_migrated}/${v.total_shared}, ids in two collections: ${v.ids_in_two_collections}.</p>
      <h3 style="margin:16px 0 8px">4. Performance (wall clock, median)</h3>${bars3(m.compare || [])}
      <table class="table" style="margin-top:8px"><thead><tr><th>Scale</th><th>List: docs examined (shared / own)</th><th>Analytics result identical</th></tr></thead><tbody>${(m.compare || []).map((c) => `<tr><td>${esc(c.scale)}</td><td>${c.list_20.explain["document-level"].docs_examined} / ${c.list_20.explain["collection-per-tenant"].docs_examined} (${esc(c.list_20.explain["document-level"].stage)} both)</td><td>${yes(c.analytics_same_result, "yes", "no")}</td></tr>`).join("")}</tbody></table>
      ${b.collection_per_tenant ? `<h3 style="margin:16px 0 8px">5. Restore ONE tenant (Lab 14 tools)</h3><table class="table"><tbody>
        <tr><td>collection-per-tenant</td><td class="mono small">${esc(b.collection_per_tenant.command)}</td><td>${b.collection_per_tenant.restored}/${b.collection_per_tenant.expected} in ${b.collection_per_tenant.restore_s} s</td></tr>
        <tr><td>document-level</td><td class="mono small">${esc(b.document_level.command)}</td><td>${b.document_level.restored}/${b.document_level.expected}, others unchanged ${yes(b.document_level.others_unchanged, "yes", "no")}</td></tr>
        <tr><td><b style="color:var(--mal)">pitfall</b></td><td colspan="2">mongorestore <b>--drop</b> with a one-tenant dump on the shared collection wiped <b>${b.pitfall_drop_on_shared.other_tenants_wiped} other tenants</b> (left: ${esc(JSON.stringify(b.pitfall_drop_on_shared.tenants_left_after))}). Repaired from the full dump: ${yes(b.pitfall_drop_on_shared.repaired, "yes", "no")}</td></tr></tbody></table>` : ""}
      ${sc.document_level ? `<h3 style="margin:16px 0 8px">6. Schema evolution</h3><p style="margin:0">${esc(sc.change)}: <b>${sc.document_level.operations}</b> operations (${sc.document_level.seconds} s) on the shared collection vs <b>${sc.collection_per_tenant.operations}</b> (${sc.collection_per_tenant.seconds} s) for the tenant collections - ${esc(sc.collection_per_tenant.note)}.</p>` : ""}
      <h3 style="margin:16px 0 8px">7. Recommendation</h3><div style="overflow-x:auto"><table class="table"><thead><tr><th>Criterion</th><th>Document-level</th><th>Collection-per-tenant</th><th>Measured</th><th>Better</th></tr></thead><tbody>
      ${(m.recommendation || []).map((r) => `<tr><td><b>${esc(r.criterion)}</b></td><td class="small">${esc(r.document_level)}</td><td class="small">${esc(r.collection_per_tenant)}</td><td class="small">${esc(r.measured)}</td><td><b>${esc(r.winner)}</b></td></tr>`).join("")}</tbody></table></div>
      <p style="margin:10px 0 0"><b>Verdict:</b> start with document-level isolation (one collection, compound index starting with tenant_id, the filter in one function). Move a tenant to its own collection - or database - only when it needs independent backup/restore, strict regulatory separation or its own tuning.</p>`;
  }
  function migTab(box, st, saved) {
    box.innerHTML = `<section class="panel"><div class="panel-head"><h2>Document-level &rarr; collection-per-tenant</h2><div class="row-gap"><button class="btn small" id="b-mq">Run (5 x 100 only, fast)</button><button class="btn small primary" id="b-mf">Run all scales (~1 min)</button></div></div><div class="panel-pad" id="out">${migView(saved.lab_15_2)}</div></section><div id="lg"></div>`;
    const go = (quick) => async (e) => act(e.currentTarget, "Migrating", async () => { const r = await api("/api/lab15/run/migration", { method: "POST", body: { quick } }); $("#out").innerHTML = migView(r); $("#lg").innerHTML = logBox(r.log); });
    $("#b-mq").addEventListener("click", (e) => go(true)(e)); $("#b-mf").addEventListener("click", (e) => go(false)(e));
  }

  // ================================================================== design + applied
  function designTab(box, st, saved) {
    const a = saved.applied || {};
    const coll = (n, f, ix) => `<div class="l15-coll"><b>${n}</b><ul>${f.map((x) => `<li class="mono small">${x}</li>`).join("")}</ul>${ix.map((x) => `<span class="pill">${x}</span>`).join(" ")}</div>`;
    box.innerHTML = `<section class="panel panel-pad"><h2 style="margin-bottom:10px">Schema (database tasktracker on the Lab 13 production set)</h2><div class="l15-schema">
        ${coll("tenants", ["_id: \"acme\" (slug)", "name", "plan: free | pro | enterprise", "rate_limit (optional)"], ["$jsonSchema"])}
        ${coll("users", ["_id", "tenant_id", "email", "name", "role: admin | member", "pw: pbkdf2-sha256"], ["unique (tenant_id, email)", "$jsonSchema"])}
        ${coll("tasks", ["_id", "tenant_id", "owner_id, owner", "title, description", "priority: low|medium|high", "status: open|in_progress|done", "created_at, updated_at"], ["(tenant_id, created_at)", "(tenant_id, owner_id, created_at)", "$jsonSchema"])}
        ${coll("rate_limits", ["_id: \"acme:&lt;minute&gt;\"", "tenant_id, window, limit", "count ($inc)", "ts (TTL 120 s)"], ["TTL ts"])}</div></section>
      <section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:10px">One request, step by step</h2><div class="l14-steps">
        <div class="l14-step"><b>1</b><div><strong>HTTPBearer</strong><span>JWT HS256, exp 8 h; claims sub, tenant_id, role</span></div></div>
        <div class="l14-step"><b>2</b><div><strong>current_user</strong><span>verify signature + exp, re-read the user WITH the token's tenant &rarr; 401</span></div></div>
        <div class="l14-step"><b>3</b><div><strong>rate_limit</strong><span>findAndModify $inc on tenant:minute &rarr; 429 + Retry-After</span></div></div>
        <div class="l14-step"><b>4</b><div><strong>scoped()</strong><span>{tenant_id, ...} (+ owner_id for member writes) &rarr; 404 / 403</span></div></div>
        <div class="l14-step ok"><b>5</b><div><strong>MongoDB</strong><span>IXSCAN (tenant_id, created_at), w: majority, $jsonSchema</span></div></div></div></section>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Lab 15 applied to the lower labs</h2><button class="btn small primary" id="b-ap">Run</button></div><div class="panel-pad" id="ap">${appliedView(a)}</div></section><div id="lg"></div>`;
    $("#b-ap").addEventListener("click", (e) => act(e.currentTarget, "Checking", async () => { const r = await api("/api/lab15/run/applied", { method: "POST" }); $("#ap").innerHTML = appliedView(r); $("#lg").innerHTML = logBox(r.log); }));
  }
  function appliedView(a) {
    if (!a.least_privilege) return notRun();
    const m = a.least_privilege.matrix, t = a.transaction || {};
    return `<table class="table"><thead><tr><th>Lab</th><th>How Lab 15 uses it</th><th>Measured</th></tr></thead><tbody>
      <tr><td>Lab 13</td><td>runs on the production replica set (keyFile, SCRAM)</td><td class="small">${esc(((a.deployment || {}).members || []).join(", "))}</td></tr>
      <tr><td>Lab 12</td><td>least-privilege user <span class="mono">tasktracker_app</span> = readWrite on tasktracker only</td><td class="small">${Object.entries(m).map(([k, v]) => `${v === "allowed" ? dot(true) : '<span class="dot err"></span>'}${esc(k)}: ${esc(v)}`).join("<br>")}</td></tr>
      <tr><td>Lab 10</td><td>signup = tenant + first admin in ONE transaction</td><td class="small">signup ${t.signup}, again ${t.signup_again}; failing signup ${t.failing_signup} &rarr; tenant rolled back ${yes(t.tenant_rolled_back, "yes", "NO")}</td></tr>
      <tr><td>Lab 9</td><td>every write w: majority</td><td class="small">client option w="majority"</td></tr>
      <tr><td>Lab 14</td><td>hourly backup target <span class="mono">tasktracker</span>, monitored by the ops daemon; per-tenant restores (15.2)</td><td class="small">${a.backup ? `${yes(a.backup.ok, "backup rc 0")} ${esc(a.backup.name || a.backup.error || "")}` : "-"}</td></tr>
      <tr><td>Lab 7</td><td>the same website and API server, mounted at <span class="mono">/tasktracker</span></td><td class="small"><a href="/tasktracker/docs" target="_blank" rel="noopener">/tasktracker/docs</a></td></tr></tbody></table>`;
  }
  function scriptsTab(box) {
    const fb = (n, l) => `<a class="btn small" href="/api/lab15/file/${n}" target="_blank" rel="noopener">${esc(l)}</a>`;
    box.innerHTML = `<section class="panel panel-pad"><h2 style="margin-bottom:10px">Files</h2><div class="row-gap" style="flex-wrap:wrap">${fb("app", "tasktracker/app.py (15.1 API)")}${fb("migrate", "migrate_tenants.py (15.2)")}${fb("sample", "sample_app.py (classroom sample, unchanged)")}${fb("runner", "run_lab15.py")}${fb("readme", "README")}
      <a class="btn small" href="/tasktracker/docs" target="_blank" rel="noopener">Swagger UI</a></div></section>
      <section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:10px">Commands</h2>${["docker compose up -d lab15", "docker compose exec lab15 lab15 all", "docker compose exec lab15 lab15 crud", "docker compose exec lab15 lab15 rate", "docker compose exec lab15 lab15 rbac", "docker compose exec lab15 lab15 migration", "docker compose exec lab15 lab15 users", "docker compose exec lab15 lab15 token acme ravi@acme.test"].map(copyCmd).join("")}
      <p class="muted" style="margin:10px 0 0">With curl (token from <span class="mono">lab15 token</span>):</p>${copyCmd('curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/tasktracker/tasks/')}
      ${copyCmd('curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d \'{"title":"From curl","priority":"high"}\' http://localhost:8000/tasktracker/tasks/')}</section>`;
  }

  return { lab15: page15 };
};

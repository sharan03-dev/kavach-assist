/* Kavach CTI - Security lab 12 (database security & the CIA triad). Loaded before labs.js; labs.js calls
   window.KavachLab12(helpers) and adds the returned route. Everything comes from the hardened mongo-secure server
   through /api/lab12/* - each button runs the real operation as the real MongoDB user. */
window.KavachLab12 = (h) => {
  "use strict";
  const { $, $$, esc, num, api, toast, copyCmd, onLeave, intro, explain, act, dot } = h;
  const RES = { allowed: "allowed", denied: "not authorized", rejected: "validation failed", "auth-failed": "login refused",
    refused: "connection refused", invalid: "invalid", "read-only view": "read-only view", error: "error" };
  const tag = (r) => `<span class="sec-res r-${esc(r)}">${esc(RES[r] || r)}</span>`;
  const noServer = (reason) => `<div class="banner err" style="margin:18px 0"><div><strong>Secure server not available.</strong> ${esc(reason || "")}
    <div style="margin-top:6px">Lab 12 runs on its own hardened MongoDB (auth + TLS + client certificates). Start it, together with Labs 7 to 11, with:</div>${copyCmd("docker compose up -d lab12")}</div></div>`;
  const notRun = (what) => `<div class="panel empty"><strong>No results yet</strong>${what} Start Lab 12 in the <a href="#/labs">Lab control centre</a>, or run <span class="mono">docker compose exec lab12 lab12 all</span>.</div>`;
  const pj = (o) => `<pre class="sec-json">${esc(JSON.stringify(o, null, 2))}</pre>`;

  // ------------------------------------------------------------------ the mentor's threat-modelling picture, live
  function threatSvg(tm) {
    const th = tm.threats || [], ct = tm.controls || [];
    const W = 980;
    let g = `<rect x="10" y="10" width="${W - 20}" height="96" rx="8" class="tm-band a"/><text x="${W / 2}" y="30" text-anchor="middle" class="tm-h">Attack surface</text>`;
    th.forEach((t, i) => {
      const x = 30 + i * 238;
      g += `<rect x="${x}" y="42" width="214" height="52" rx="6" class="tm-box ${t.mitigated ? "ok" : "bad"}"/>
        <text x="${x + 107}" y="64" text-anchor="middle" class="tm-b">${esc(t.threat)}</text><text x="${x + 107}" y="82" text-anchor="middle" class="tm-s">${esc(t.cause)}</text>
        <path d="M${x + 107} 94 V150" class="tm-link"/><text x="${x + 113}" y="126" class="tm-s">prevents ${t.mitigated ? "&#10003;" : "?"}</text>`;
    });
    g += `<rect x="10" y="150" width="${W - 20}" height="140" rx="8" class="tm-band c"/><text x="${W / 2}" y="170" text-anchor="middle" class="tm-h">MongoDB security controls &mdash; green = verified on mongo-secure</text>`;
    ct.forEach((c, i) => {
      const x = 30 + (i % 5) * 188, y = i < 5 ? 184 : 238;
      g += `<g><title>${esc(c.evidence)}</title><rect x="${x}" y="${y}" width="172" height="42" rx="6" class="tm-box ${c.ok === null ? "na" : c.ok ? "ok" : "bad"}"/>
        <text x="${x + 86}" y="${y + 18}" text-anchor="middle" class="tm-b sm">${esc(c.control.split(" (")[0])}</text><text x="${x + 86}" y="${y + 34}" text-anchor="middle" class="tm-s">${esc(c.cia)}</text></g>`;
    });
    g += `<path d="M${W / 2} 290 V318" class="tm-link"/><rect x="${W / 2 - 230}" y="318" width="460" height="196" rx="8" class="tm-band y"/><text x="${W / 2}" y="338" text-anchor="middle" class="tm-h">CIA triad defence layers</text>`;
    Object.entries(tm.cia || {}).forEach(([k, v], i) => {
      g += `<rect x="${W / 2 - 200}" y="${350 + i * 54}" width="400" height="46" rx="6" class="tm-cia"/><text x="${W / 2}" y="${369 + i * 54}" text-anchor="middle" class="tm-b w">${esc(k)}</text><text x="${W / 2}" y="${386 + i * 54}" text-anchor="middle" class="tm-s w">${esc(v.join(" \u00b7 "))}</text>`;
    });
    return `<svg viewBox="0 0 ${W} 524" class="tm" role="img" aria-label="Threat model: attack surface, MongoDB security controls and the CIA triad">${g}</svg>`;
  }

  function matrixTable(m) {
    if (!m || !m.rows) return "";
    return `<div style="overflow-x:auto"><table class="table sec-matrix"><thead><tr><th>User</th>${m.ops.map((o) => `<th>${esc(o)}</th>`).join("")}</tr></thead><tbody>
      ${m.rows.map((r) => `<tr><td class="mono">${esc(r.user)}</td>${m.ops.map((o) => { const c = r.cells[o];
        return `<td class="mc ${c.result === "allowed" ? "a" : "x"} ${c.ok ? "" : "bad"}" title="${esc(`${RES[c.result] || c.result}${c.code ? " (code " + c.code + ")" : ""} - expected ${c.expected}\n${c.detail || ""}`)}">${c.result === "allowed" ? "A" : "&times;"}</td>`; }).join("")}</tr>`).join("")}
      </tbody></table></div><p class="muted small panel-pad" style="margin:0;padding-top:8px">A = allowed, &times; = refused by MongoDB (hover a cell for the server's answer). ${m.pass ? `${dot(true)} every cell equals the expected least-privilege result` : `${dot(false)} a cell differs from the expected result (outlined)`}</p>`;
  }

  // ================================================================== page
  async function pageLab12(page, route) {
    const tab = route.params.get("tab") || "model";
    const [st, saved] = await Promise.all([api("/api/lab12/status").catch((e) => ({ available: false, reason: e.message })),
      api("/api/lab12/results").catch(() => ({}))]);
    page.innerHTML = `${intro("Security lab 12",
      `Database security is the <b>CIA triad</b>: <b>confidentiality</b> (only the right people read the data), <b>integrity</b>
       (data is not changed wrongly, and every change is accountable) and <b>availability</b>. This lab hardens a MongoDB server with
       authentication, roles, redacting views and TLS, and then applies it to the Kavach platform of Lab 7. Every result here is
       measured on the real server <span class="mono">mongo-secure</span>, logged in as the real user.`,
      `<div class="row-gap"><a class="btn small" href="/api/lab12/report" target="_blank" rel="noopener">Download PDF report</a></div>`)}
      ${st.available ? statusLine(st) : noServer(st.reason)}
      <nav class="tabs" role="tablist">${[["model", "Threat model"], ["rbac", "Auth + RBAC (12.1)"], ["redact", "Redaction (activity)"], ["tls", "TLS (12.2)"],
        ["kavach", "Kavach on the secure server (12 on 7)"], ["connect", "Connect"]]
        .map(([k, t]) => `<button role="tab" data-tab="${k}" class="${tab === k ? "active" : ""}" aria-selected="${tab === k}">${t}</button>`).join("")}</nav>
      <div id="tabbody"></div>`;
    $$("[data-tab]").forEach((b) => b.addEventListener("click", () => { location.hash = `#/lab12?tab=${b.dataset.tab}`; }));
    ({ model: modelTab, rbac: rbacTab, redact: redactTab, tls: tlsTab, kavach: kavachTab, connect: connectTab }[tab] || modelTab)($("#tabbody"), st, saved || {});
  }

  const statusLine = (st) => `<section class="panel" style="margin-top:18px"><div class="stat-line" style="border-top:none">
    <div><b>${esc(st.version)}</b><span>MongoDB (${esc(st.set)})</span></div><div><b>${esc(st.tls_mode)}</b><span>TLS mode</span></div>
    <div><b>${st.client_certs_required ? "required" : "optional"}</b><span>client certificates</span></div>
    <div><b>${esc(String(st.mechanisms))}</b><span>authentication mechanism</span></div><div><b>${num(st.users.length)}</b><span>users, ${num(st.roles.length)} custom roles</span></div>
    <div><b>${num((st.transport_security || {})["1.3"] || 0)}</b><span>TLS 1.3 connections so far</span></div></div></section>`;

  // ------------------------------------------------------------------ tab: threat model
  function modelTab(box, st, saved) {
    const tm = saved.threat_model;
    if (!tm) { box.innerHTML = notRun("The threat model is filled in from the measured results."); return; }
    box.innerHTML = `<section class="panel panel-pad" style="overflow-x:auto">${threatSvg(tm)}</section>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Every control, with the evidence</h2><span class="muted">measured, not assumed</span></div>
      <div style="overflow-x:auto"><table class="table"><thead><tr><th>Control</th><th>CIA</th><th>Status</th><th>Evidence</th></tr></thead><tbody>
      ${tm.controls.map((c) => `<tr><td><b>${esc(c.control)}</b></td><td>${esc(c.cia)}</td><td>${c.ok === null ? `<span class="muted">see Lab 8</span>` : `${dot(c.ok)}${c.ok ? "verified" : "FAILED"}`}</td><td class="muted">${esc(c.evidence)}</td></tr>`).join("")}
      </tbody></table></div></section>
      ${(saved.checks || []).length ? `<section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:8px">Checks of the last run</h2>${saved.checks.map((c) => `<div class="kvr"><span>${esc(c.name)}</span><b>${dot(c.ok)}${c.ok ? "pass" : "FAIL"}</b></div>`).join("")}</section>` : ""}
      ${explain("Reading the picture", `<ol class="plain"><li><b>Unauthenticated access</b> is stopped by authentication: mongo-secure runs with a keyFile (auth on) and accepts only SCRAM-SHA-256.</li>
        <li><b>Weak credentials</b>: every user has a random 24-character password and only the privileges of its role, so a stolen app password cannot drop data or read salaries.</li>
        <li><b>Network eavesdropping</b>: requireTLS - plain connections are closed; clients also need a certificate from the Kavach CA.</li>
        <li><b>Insider threat</b>: least privilege, redacting views, client-side encryption of UPI ids (even root sees ciphertext) and an audit trail of who did what.</li>
        <li><b>Availability</b> is the replica set of Labs 8-9 (automatic failover); mongo-secure itself is a one-member set used as the security test bed.</li></ol>`)}`;
  }

  // ------------------------------------------------------------------ tab: 12.1
  function rbacTab(box, st, saved) {
    const r = saved.lab_12_1;
    if (!r) { box.innerHTML = notRun("Lab 12.1 has not run yet."); return; }
    box.innerHTML = `<div class="grid cols-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
      <section class="panel"><div class="panel-head"><h2>The classroom snippet, run as given</h2>${tag(r.snippet.result)}</div>
        <pre class="sec-json">${esc(r.snippet.source || "")}</pre>
        <div class="panel-pad" style="padding-top:0"><p style="margin:0 0 6px"><b>MongoDB answered:</b> <span class="mono">${esc(r.snippet.detail)}</span></p>
        <p class="muted" style="margin:0">'aggregate' is a command, not a privilege <i>action</i>; an aggregation is allowed by <b>find</b>. The solution's 'delete' fails the same way (${esc((r.delete_action || {}).detail || "")}) - the action is <b>remove</b>. The lab creates the role with find, in the admin database (the user refers to it as db 'admin').</p></div></section>
      <section class="panel"><div class="panel-head"><h2>Users</h2><span class="muted">SCRAM-SHA-256, random passwords</span></div>
        <table class="table"><tbody>${r.users.map((u) => `<tr><td class="mono">${esc(u.user)}</td><td class="mono small">${esc(u.roles.map((x) => x.role).join(", "))}</td><td class="muted small">${esc(u.purpose)}</td></tr>`).join("")}</tbody></table></section></div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Roles (createRole)</h2><span class="muted">least privilege: named collections, named actions</span></div>
        <div style="overflow-x:auto"><table class="table"><thead><tr><th>Role</th><th>Privileges</th><th>Why</th></tr></thead><tbody>
        ${r.roles.map((x) => `<tr><td class="mono">${esc(x.role)}</td><td class="mono small">${x.privileges.map((p) => `${esc(p.resource.db)}.${esc(p.resource.collection || "*")}: ${esc(p.actions.join(", "))}`).join("<br>")}</td><td class="muted small">${esc(x.note)}</td></tr>`).join("")}</tbody></table></div></section>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Step 3 - verify every user</h2><button class="btn small primary" id="m-run">Run the matrix again</button></div>
        <div id="m-out">${matrixTable(r.matrix)}</div></section>
      ${explain("What to notice", `<ul class="plain"><li>app_user_read can aggregate orders although its role only says <b>find</b>.</li>
        <li>app_user_write cannot touch analytics or employees - its role names two collections only.</li>
        <li><b>sec_admin</b> (userAdminAnyDatabase) cannot read a single document, but it CAN create a user with root: user administration is as sensitive as root.</li></ul>`)}`;
    $("#m-run").addEventListener("click", (e) => act(e.currentTarget, "Logging in as 7 users", async () => {
      const m = await api("/api/lab12/matrix", { method: "POST", body: { which: "classroom" } });
      $("#m-out").innerHTML = matrixTable(m);
      toast(m.pass ? "Every user can do exactly what its role allows" : "A result differs from the expected one");
    }));
  }

  // ------------------------------------------------------------------ tab: redaction
  function redactTab(box, st, saved) {
    const paint = (r) => `<div class="grid cols-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
        <section class="panel"><div class="panel-head"><h2>hr_user reads <span class="mono">employees</span></h2>${tag("allowed")}</div>${pj(r.sample_hr)}</section>
        <section class="panel"><div class="panel-head"><h2>staff_user reads <span class="mono">employees_public</span></h2>${tag("allowed")}</div>${pj(r.sample_staff)}</section></div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Tests</h2><span class="muted">as staff_user unless stated</span></div><div class="panel-pad">
        ${Object.entries(r.checks).map(([k, v]) => `<div class="kvr"><span>${esc(k)}</span><b>${dot(v)}${v ? "pass" : "FAIL"}</b></div>`).join("")}
        <div class="kvr"><span>staff_user: average salary through the view</span><b class="mono small">${esc(r.staff_avg_salary.detail)}</b></div>
        <div class="kvr"><span>staff_user: <span class="mono">find()</span> on employees</span><b>${tag(r.staff_collection.result)}</b></div></div></section>
      <section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:8px">Measured weakness of an exclusion view</h2>
        <p style="margin:0 0 8px">HR added a new sensitive field <span class="mono">bank_account</span> to employee 1. Then staff read both views:</p>
        <div class="kvr"><span><span class="mono">employees_public</span> &mdash; <span class="mono">{$project: {salary: 0}}</span> (exclusion, as the activity asks)</span><b>${dot(!r.leak.exclusion_leaks)}${r.leak.exclusion_leaks ? "LEAKS bank_account" : "no leak"}</b></div>
        <div class="kvr"><span><span class="mono">employees_directory</span> &mdash; allow-list of named fields</span><b>${dot(!r.leak.allowlist_leaks)}${r.leak.allowlist_leaks ? "LEAKS" : "no leak"}</b></div>
        <p class="muted" style="margin:8px 0 0">So a redaction view should list what may be shown. Kavach's customer view (tab 12 on 7) is written that way.</p></section>`;
    const r = saved.redaction;
    box.innerHTML = `<section class="panel panel-pad"><div class="row-gap" style="justify-content:space-between;flex-wrap:wrap"><div>
        <p style="margin:0 0 6px"><b>Activity:</b> configure field-level redaction using a view that excludes 'salary' for users without the HR role.</p>
        ${copyCmd("db.createView('employees_public', 'employees', [{ $project: { salary: 0 } }])")}</div>
        <button class="btn small primary" id="r-run">Run the activity now</button></div></section><div id="r-out" style="margin-top:18px">${r ? paint(r) : notRun("")}</div>`;
    $("#r-run").addEventListener("click", (e) => act(e.currentTarget, "Reading as hr_user and staff_user", async () => {
      $("#r-out").innerHTML = paint(await api("/api/lab12/redaction", { method: "POST" }));
    }));
  }

  // ------------------------------------------------------------------ tab: TLS
  function tlsTab(box, st, saved) {
    const paint = (t) => `<section class="panel"><div class="panel-head"><h2>Connection attempts</h2><span class="muted">each one is a new connection from this container</span></div>
        <div style="overflow-x:auto"><table class="table"><thead><tr><th>Attempt</th><th>Expected</th><th>Result</th><th>What the client saw</th></tr></thead><tbody>
        ${t.tests.map((x) => `<tr><td>${esc(x.test)}</td><td>${esc(x.expected)}</td><td>${dot(x.ok)}<b>${esc(x.result)}</b></td><td class="mono small">${esc(String(x.detail).slice(0, 140))}</td></tr>`).join("")}</tbody></table></div></section>
      <div class="grid cols-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr);margin-top:18px">
        <section class="panel panel-pad"><h2 style="margin-bottom:8px">Handshake</h2>
          <div class="kvr"><span>Protocol</span><b>${esc(t.handshake.protocol)}</b></div><div class="kvr"><span>Cipher</span><b class="mono small">${esc(t.handshake.cipher)} (${t.handshake.bits} bit)</b></div>
          <div class="kvr"><span>Server certificate</span><b class="small">${esc(t.handshake.server_subject)}</b></div><div class="kvr"><span>Subject alternative names</span><b class="mono small">${esc(t.handshake.san.join(", "))}</b></div>
          <div class="kvr"><span>Server: connections per TLS version</span><b class="mono small">${esc(JSON.stringify(t.transport_security))}</b></div></section>
        <section class="panel panel-pad"><h2 style="margin-bottom:8px">Certificate chain</h2>
          <div class="chainv">${t.chain.certs.filter((c) => c.file !== "rogue-ca.crt").map((c, i) => `<div class="ch ${c.is_ca ? "ca" : ""}" style="margin-left:${i ? 26 : 0}px"><b>${esc(c.cn)}</b> <span class="muted small">${esc(c.file)}</span>
            <div class="muted small">issued by ${esc(c.issuer_cn)} &middot; RSA ${c.key_bits} &middot; ${esc(c.signature)} &middot; until ${esc(c.not_after.slice(0, 10))}${c.san.length ? " &middot; " + esc(c.san.join(", ")) : ""}</div></div>`).join("")}</div>
          ${t.chain.verify.map((v) => `<div class="kvr"><span>${esc(v.cert)} signed by Kavach-MongoDB-CA</span><b>${dot(v.trusted === (v.cert !== "rogue-client.pem"))}${v.trusted ? "yes" : "no - rejected"}</b></div>`).join("")}</section></div>`;
    const t = saved.lab_12_2;
    box.innerHTML = `<section class="panel panel-pad"><div class="row-gap" style="justify-content:space-between;flex-wrap:wrap"><div>
      <p style="margin:0 0 6px">mongo-secure is started with</p>${copyCmd("mongod --tlsMode requireTLS --tlsCertificateKeyFile server.pem --tlsCAFile ca.crt --keyFile ... --setParameter authenticationMechanisms=SCRAM-SHA-256")}</div>
      <button class="btn small primary" id="t-run">Run the TLS tests now</button></div></section><div id="t-out" style="margin-top:18px">${t ? paint(t) : notRun("")}</div>
      ${explain("How the certificates were made", `<ol class="plain"><li>CA key + self-signed CA certificate (<span class="mono">openssl genrsa -out ca.key 4096; openssl req -new -x509 -days 365 ...</span>).</li>
        <li>Server key + certificate signing request, CN=mongo-secure.</li><li>The CA signs it with subjectAltName <span class="mono">DNS:mongo-secure, DNS:localhost, IP:127.0.0.1</span> (PyMongo checks the host name against the SAN).</li>
        <li>A client certificate (clientAuth) for PyMongo and mongosh; <span class="mono">server.pem</span> / <span class="mono">client.pem</span> = key + certificate.</li></ol>
        <p style="margin:8px 0 0">The lab automates this with the Python <i>cryptography</i> library (<span class="mono">labs/lab12/setup/certs.py</span>, run by the <span class="mono">lab12-certs</span> container); <span class="mono">labs/lab12/openssl_certs.sh</span> is the same with OpenSSL.</p>`)}`;
    $("#t-run").addEventListener("click", (e) => act(e.currentTarget, "Connecting six ways", async () => {
      $("#t-out").innerHTML = paint(await api("/api/lab12/tls", { method: "POST" }));
    }));
  }

  // ------------------------------------------------------------------ tab: Kavach (12 on 7)
  async function kavachTab(box, st, saved) {
    const k = saved.kavach;
    if (!k) { box.innerHTML = notRun("Lab 12 has not been applied to Kavach yet."); return; }
    const acts = await api("/api/lab12/actions").catch(() => ({ personas: [], kavach: [], classroom: [] }));
    box.innerHTML = `<section class="panel panel-pad"><p style="margin:0">Lab 12 applied to Lab 7: the Kavach data is on mongo-secure and every part of the platform uses its own account.
      The <b>website</b> reads as <span class="mono">analyst_app</span>, the <b>5-minute auto-updater</b> writes as <span class="mono">ingest_app</span> (no delete, no drop),
      the <b>classifier billing</b> (Lab 10) uses <span class="mono">finance_app</span>.</p></section>
      <div class="grid cols-2" style="grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);margin-top:18px">
        <section class="panel"><div class="panel-head"><h2>Threat feed, read as analyst_app over TLS</h2><span class="muted" id="f-ms"></span></div><div id="feed" class="panel-pad"><span class="spin"></span></div></section>
        <section class="panel"><div class="panel-head"><h2>Customers, as an analyst sees them</h2><span class="muted">view accounts_masked</span></div><div id="cust"></div></section></div>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Try it as a user</h2><span class="muted">runs the real operation, logged in as that user</span></div>
        <div class="panel-pad row-gap" style="flex-wrap:wrap"><select id="p-user" class="sec-sel">${acts.personas.map((p) => `<option>${esc(p)}</option>`).join("")}</select>
        <select id="p-act" class="sec-sel"><optgroup label="Kavach">${acts.kavach.map((a) => `<option>${esc(a)}</option>`).join("")}</optgroup><optgroup label="Lab 12.1">${acts.classroom.map((a) => `<option>${esc(a)}</option>`).join("")}</optgroup></select>
        <button class="btn small primary" id="p-go">Run</button></div><div id="p-out" class="panel-pad" style="padding-top:0"></div></section>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Kavach permission matrix</h2><button class="btn small" id="km-run">Run again</button></div><div id="km">${matrixTable(k.matrix)}</div></section>
      <div class="grid cols-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr);margin-top:18px">
        <section class="panel panel-pad"><div class="row-gap" style="justify-content:space-between"><h2>Integrity: schema validation</h2><button class="btn small" id="i-run">Run</button></div><div id="i-out"></div></section>
        <section class="panel panel-pad"><div class="row-gap" style="justify-content:space-between"><h2>Log-in hardening</h2><button class="btn small" id="a-run">Run</button></div><div id="a-out"></div></section></div>
      <section class="panel panel-pad" style="margin-top:18px"><div class="row-gap" style="justify-content:space-between"><h2>Insider threat: client-side field level encryption</h2><button class="btn small" id="c-run">Run</button></div><div id="c-out"></div></section>
      <section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Audit trail: who did what</h2><button class="btn small" id="au-run">Change a wallet as finance_app</button></div><div id="au-out"></div></section>
      ${explain("Data copied from Lab 7", `<table class="table"><thead><tr><th>Collection</th><th class="num">Copied</th><th class="num">In Lab 7</th><th>Id checksum</th></tr></thead><tbody>
        ${k.copies.map((c) => `<tr><td class="mono">cti_secure.${esc(c.collection)}</td><td class="num">${num(c.copied)}</td><td class="num">${num(c.source_total)}</td><td>${dot(c.verified)}${c.verified ? "matches" : "differs"}</td></tr>`).join("")}</tbody></table>
        <p class="muted" style="margin:8px 0 0">Billing: ${num(k.billing.accounts)} accounts (${num(k.billing.real_accounts)} real, from ${esc(k.billing.source)}), ${num(k.billing.payments)} payments, ${num(k.billing.encrypted_upi)} UPI ids encrypted.
        After every 5-minute cycle the Lab 7 auto-updater pushes the newest reports to cti_secure as ingest_app ${k.sync && k.sync.at ? `(last: ${num(k.sync.reports)} reports, ${num(k.sync.indicators)} indicators)` : ""}.</p>
        <div style="margin-top:8px"><button class="btn small" id="s-run">Sync now as ingest_app</button></div>`)}`;

    const integ = (o) => Object.entries(o).filter(([, v]) => v && v.result).map(([n, v]) => `<div class="kvr"><span>${esc(n.replace(/_/g, " "))}</span><b>${tag(v.result)}</b></div>`).join("");
    const auth = (o) => o.weak_passwords.map((w) => `<div class="kvr"><span>default password <span class="mono">${esc(w.user)}/${esc(w.password)}</span></span><b>${tag(w.result)}</b></div>`).join("") +
      `<div class="kvr"><span>SCRAM-SHA-1 log-in</span><b>${tag(o.scram_sha1.result)}</b></div><div class="kvr"><span>no credentials: listDatabases</span><b>${tag(o.anonymous.result)}</b></div>
       <div class="kvr"><span>ip_locked (only from 10.99.99.99)</span><b>${tag(o.ip_locked.result)}</b></div><div class="kvr"><span>finance_app (private networks only)</span><b>${tag(o.finance_from_private_net.result)}</b></div>`;
    const fle = (o) => !o.available ? `<p class="muted">No encrypted payments.</p>` : `<div class="kvr"><span>kavach_admin (<b>root</b>) reads <span class="mono">payments.upi_vpa</span></span><b class="mono small">${esc(o.root_sees)}</b></div>
       <div class="kvr"><span>finance_app with the master key</span><b class="mono">${esc(o.finance_decrypts)}</b></div><div class="kvr"><span>server-side search for the plaintext</span><b>${num(o.query_by_plaintext)} documents</b></div>
       <p class="muted small" style="margin:8px 0 0">${esc(o.algorithm)}. The data key is in <span class="mono">${esc(o.key_vault)}</span>, wrapped by a master key that never reaches the server - a DBA or a stolen backup sees only ciphertext.</p>`;
    const aud = (o) => !o.available ? `<p class="muted panel-pad">No accounts.</p>` : `<div class="panel-pad" style="padding-bottom:0">${o.change_event ? `<p style="margin:0 0 8px"><b>Change stream</b> (pre- and post-images): ${esc(o.change_event.operation)} of <span class="mono">${esc(o.change_event.document)}</span>, wallet <b>${o.change_event.wallet_before} &rarr; ${o.change_event.wallet_after}</b></p>` : ""}
       <p class="muted small" style="margin:0">Profiler of kavach_billing (level 2) - the user of every operation: ${Object.entries(o.by_user).map(([u, n]) => `<b>${esc(u)}</b> ${n}`).join(", ")}</p></div>
       <div style="overflow-x:auto;max-height:340px"><table class="table"><thead><tr><th>Time</th><th>User</th><th>Op</th><th>Namespace</th><th>Detail</th></tr></thead><tbody>
       ${o.profile.slice(0, 25).map((p) => `<tr><td class="mono small">${esc(String(p.at).slice(11, 19))}</td><td class="mono small">${esc(p.user)}</td><td>${esc(p.op)}</td><td class="mono small">${esc(p.ns)}</td><td class="mono small">${esc(p.detail)}</td></tr>`).join("")}</tbody></table></div>`;
    $("#i-out").innerHTML = integ(k.integrity);
    $("#a-out").innerHTML = auth(k.ip_weak);
    $("#c-out").innerHTML = fle(k.csfle);
    $("#au-out").innerHTML = aud(k.audit);

    api("/api/lab12/feed?n=8").then((f) => {
      $("#f-ms").textContent = `${f.ms} ms, roles: ${f.roles.map((r) => r.role).join(", ")}`;
      $("#feed").innerHTML = `<ul class="mini-list">${f.reports.map((r) => `<li style="flex-wrap:wrap"><span>${esc(r.title || "")}</span><span class="muted small">${esc(String(r.published_at || "").slice(0, 10))} ${esc(((r.classification || {}).category) || "")}</span></li>`).join("") || `<li class="muted">No reports.</li>`}</ul>`;
      $("#cust").innerHTML = `<table class="table"><thead><tr><th>Customer</th><th>Name</th><th>E-mail</th><th class="num">Uses</th><th>Plan</th></tr></thead><tbody>${f.customers.map((c) => `<tr><td class="mono small">${esc(c.customer)}</td><td>${esc(c.name || "")}</td><td class="mono small">${esc(c.email)}</td><td class="num">${num(c.uses_total || 0)}</td><td>${c.plan_active ? "active" : "-"}</td></tr>`).join("")}</tbody></table>
        <p class="muted small panel-pad" style="margin:0;padding-top:6px">No wallet, no payments, no full e-mail: the view lists only the allowed fields. Reading <span class="mono">accounts</span> as analyst_app is not authorized.</p>`;
    }).catch((e) => { $("#feed").innerHTML = `<p class="muted">${esc(e.message)}</p>`; });

    $("#p-go").addEventListener("click", (e) => act(e.currentTarget, "Running", async () => {
      const r = await api("/api/lab12/try", { method: "POST", body: { persona: $("#p-user").value, action: $("#p-act").value } });
      $("#p-out").innerHTML = `<div class="kvr"><span><span class="mono">${esc(r.persona)}</span>: ${esc(r.action)}</span><b>${tag(r.result)}${r.code ? ` <span class="muted small">code ${r.code} ${esc(r.codeName || "")}</span>` : ""}</b></div><pre class="sec-json" style="margin:8px 0 0">${esc(r.detail)}</pre>`;
    }));
    $("#km-run").addEventListener("click", (e) => act(e.currentTarget, "Logging in as 5 users", async () => { $("#km").innerHTML = matrixTable(await api("/api/lab12/matrix", { method: "POST", body: { which: "kavach" } })); }));
    $("#i-run").addEventListener("click", (e) => act(e.currentTarget, "Writing", async () => { $("#i-out").innerHTML = integ(await api("/api/lab12/integrity", { method: "POST" })); }));
    $("#a-run").addEventListener("click", (e) => act(e.currentTarget, "Logging in", async () => { $("#a-out").innerHTML = auth(await api("/api/lab12/auth-hardening", { method: "POST" })); }));
    $("#c-run").addEventListener("click", (e) => act(e.currentTarget, "Decrypting", async () => { $("#c-out").innerHTML = fle(await api("/api/lab12/csfle", { method: "POST" })); }));
    $("#au-run").addEventListener("click", (e) => act(e.currentTarget, "Changing a wallet", async () => { $("#au-out").innerHTML = aud(await api("/api/lab12/audit", { method: "POST" })); }));
    $("#s-run").addEventListener("click", (e) => act(e.currentTarget, "Syncing", async () => {
      const s = await api("/api/lab12/sync", { method: "POST" });
      toast(`ingest_app pushed ${s.reports} reports and ${s.indicators} indicators over TLS (${s.rejected_by_validator} rejected by the validator)`);
    }));
  }

  // ------------------------------------------------------------------ tab: connect
  function connectTab(box, st) {
    box.innerHTML = `<section class="panel panel-pad"><h2 style="margin-bottom:8px">mongosh inside the stack (easiest)</h2>
      <p class="muted" style="margin:0 0 8px">Opens mongosh over TLS with the client certificate, logged in as the chosen user (passwords are in the certificates volume).</p>
      ${copyCmd("docker compose exec lab12 lab12 shell analyst_app")}${copyCmd("docker compose exec lab12 lab12 shell kavach_admin")}
      <p class="muted" style="margin:10px 0 8px">Then try: <span class="mono">use cti_secure; db.reports.findOne()</span> (allowed) and <span class="mono">db.reports.deleteOne({})</span> (not authorized).</p></section>
      <section class="panel panel-pad" style="margin-top:18px"><h2 style="margin-bottom:8px">From your computer (Compass / mongosh)</h2>
      <p class="muted" style="margin:0 0 8px">1. Copy the CA, the client certificate and the password out of the stack:</p>
      ${copyCmd("docker compose cp lab12:/certs/ca.crt . && docker compose cp lab12:/certs/client.pem . && docker compose exec lab12 lab12 password analyst_app")}
      <p class="muted" style="margin:10px 0 8px">2. Connect (Compass: paste this, then Advanced options &rarr; TLS/SSL &rarr; CA file + client certificate):</p>
      ${copyCmd("mongosh \"mongodb://analyst_app@localhost:27040/?directConnection=true&tls=true&authSource=admin\" --tlsCAFile ca.crt --tlsCertificateKeyFile client.pem")}
      <p class="muted" style="margin:10px 0 0">Without <span class="mono">--tls</span> the server closes the connection; without the client certificate the TLS handshake fails - that is Lab 12.2.</p></section>
      ${st.available ? `<section class="panel" style="margin-top:18px"><div class="panel-head"><h2>Users on mongo-secure</h2><span class="muted">db.getUsers()</span></div><div style="overflow-x:auto"><table class="table"><thead><tr><th>User</th><th>Roles</th><th>Mechanisms</th><th>Allowed from</th></tr></thead><tbody>
        ${st.users.map((u) => `<tr><td class="mono">${esc(u.user)}</td><td class="mono small">${esc(u.roles.join(", "))}</td><td class="small">${esc((u.mechanisms || []).join(", "))}</td><td class="mono small">${esc((u.restrictions || []).map((r) => (r.clientSource || []).join(", ")).join("; ") || "anywhere")}</td></tr>`).join("")}</tbody></table></div></section>` : ""}`;
  }

  return { lab12: pageLab12 };
};

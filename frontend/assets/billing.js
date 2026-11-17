/* Kavach CTI - classifier billing (Lab 10 applied to Lab 7, sharded by Lab 11).
   3 free classifications per user, then Rs 100 per month. Payments are simulated (wallet topped up by "UPI");
   every change is one ACID transaction on the server. */
window.KavachBilling = (() => {
  "use strict";
  const KEY = "kavach.user";
  let H = null, box = null, subIdem = null;
  const user = () => { try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; } };
  const setUser = (u) => { try { u ? localStorage.setItem(KEY, u) : localStorage.removeItem(KEY); } catch (e) { /* private mode */ } };
  const idem = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
  const rupees = (n) => `\u20b9${Number(n || 0).toLocaleString("en-IN")}`;
  const day = (d) => (d ? new Date(d).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "");

  // ------------------------------------------------------------------ classifier page: account bar
  function mount(el, helpers) {
    H = helpers; box = el; subIdem = idem();
    return refresh();
  }

  async function refresh() {
    if (!box) return null;
    const { api, esc } = H;
    const u = user();
    if (!u) {
      box.innerHTML = `<section class="panel panel-pad acct"><div class="acct-signin">
        <div><h2 style="margin:0 0 4px">Sign in to classify</h2><p class="muted" style="margin:0">Every user gets <b>3 free classifications</b>. After that the classifier costs <b>${rupees(100)} per month</b>. Payments are simulated, and every change is one ACID transaction.</p></div>
        <div class="acct-form"><label class="sr-only" for="b-name">Name</label><input id="b-name" placeholder="Your name" autocomplete="name">
          <label class="sr-only" for="b-email">E-mail</label><input id="b-email" type="email" placeholder="you@example.com" autocomplete="email">
          <button class="btn primary" id="b-in">Sign in</button></div></div></section>`;
      const go = async () => {
        try {
          const a = await api("/api/billing/signin", { method: "POST", body: { email: $("#b-email").value, name: $("#b-name").value } });
          setUser(a.user_id); H.toast(`Signed in as ${esc(a.user_id)}. ${a.free_left} free classifications left.`); refresh();
        } catch (e) { H.toast(esc(e.message)); }
      };
      $("#b-in").addEventListener("click", go);
      $("#b-email").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
      return null;
    }
    let me;
    try { me = await api("/api/billing/me", { method: "POST", body: { user: u } }); } catch (e) {
      if (e.status === 401) { setUser(""); return refresh(); }
      box.innerHTML = `<div class="banner err" style="margin-bottom:16px"><div><strong>Billing unavailable.</strong> ${esc(e.message)}</div></div>`;
      return null;
    }
    const a = me.account, h = me.history;
    const dots = Array.from({ length: a.free_limit }, (_, i) => `<i class="${i < a.free_used ? "used" : ""}"></i>`).join("");
    box.innerHTML = `<section class="panel acct"><div class="acct-grid">
        <div><span class="k">Signed in</span><b>${esc(a.name || "")}</b><span class="mono small muted">${esc(a.user_id)}</span></div>
        <div><span class="k">Free classifications</span><b>${a.free_left} of ${a.free_limit} left</b><span class="trial-dots" aria-hidden="true">${dots}</span></div>
        <div><span class="k">Plan</span><b class="${a.plan.active ? "ok-t" : ""}">${a.plan.active ? `Monthly, until ${esc(day(a.plan.expires_at))}` : "No plan"}</b><span class="small muted">${rupees(a.plan.price)} for ${a.plan.period_days} days</span></div>
        <div><span class="k">Wallet</span><b>${rupees(a.wallet)}</b><span class="small muted">top-ups are simulated UPI payments</span></div>
        <div class="acct-actions"><button class="btn small" id="b-top">Add ${rupees(200)}</button>
          <button class="btn small primary" id="b-sub">${a.plan.active ? "Extend by 30 days" : "Subscribe"} for ${rupees(100)}</button>
          <button class="btn small ghost" id="b-out">Sign out</button></div></div>
      <details class="acct-hist"><summary>Your usage and payments</summary><div class="grid cols-2" style="align-items:start;gap:14px;padding:4px 18px 16px">
        <div><table class="table"><thead><tr><th>When</th><th>Charged as</th><th>Result</th></tr></thead><tbody>${h.usage.map((x) => `<tr><td class="small">${esc(H.fmtTime(x.at))}</td><td>${x.kind === "free" ? `free trial ${x.trial}` : "subscription"}</td><td class="small">${esc(x.verdict || "")}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">No classifications yet.</td></tr>`}</tbody></table></div>
        <div><table class="table"><thead><tr><th>When</th><th>Payment</th><th class="num">Amount</th></tr></thead><tbody>${h.payments.map((x) => `<tr><td class="small">${esc(H.fmtTime(x.at))}</td><td>${x.purpose === "topup" ? "wallet top-up" : `plan until ${esc(day(x.period && x.period.to))}`}</td><td class="num">${rupees(x.amount)}</td></tr>`).join("") || `<tr><td colspan="3" class="muted">No payments yet.</td></tr>`}</tbody></table></div></div></details>
      <p class="faint small" style="margin:0;padding:0 18px 12px">Billing data: ${esc(me.backend)}. Each use, top-up and payment is one multi-document transaction (Lab 10).</p></section>`;
    $("#b-top").addEventListener("click", (e) => pay(e.currentTarget, "/api/billing/topup", { user: u, amount: 200, idem: idem() }, (r) => `Added ${rupees(r.credited)} to your wallet.`));
    $("#b-sub").addEventListener("click", (e) => pay(e.currentTarget, "/api/billing/subscribe", { user: u, idem: subIdem },
      (r) => (r.duplicate ? "That payment was already made - you were not charged twice." : `Paid ${rupees(r.charged)}. Plan active until ${day(r.period.to)}.`), true));
    $("#b-out").addEventListener("click", () => { setUser(""); refresh(); });
    return a;
  }

  async function pay(btn, url, body, msg, isSub) {
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "Processing";
    try {
      const r = await H.api(url, { method: "POST", body });
      if (isSub && !r.duplicate) subIdem = idem();          // a new payment needs a new key; a double click reuses it
      H.toast(H.esc(msg(r)));
    } catch (e) { H.toast(H.esc(e.message)); }
    btn.disabled = false; btn.textContent = label;
    refresh();
  }

  function paywall(err) {
    const esc = H.esc;
    if (err.status === 401) return `<div class="panel empty"><strong>Sign in first</strong>${esc(err.message)}</div>`;
    return `<div class="panel panel-pad paywall"><h2 style="margin:0 0 6px">Your free classifications are used</h2>
      <p style="margin:0 0 12px">${esc(err.message).replace(/Rs /g, "\u20b9")} Add money to your wallet, then subscribe. Both buttons are in the account bar above.</p>
      <p class="muted small" style="margin:0">Nothing was charged for this attempt: the transaction checked your plan, found no free trial left and was aborted.</p></div>`;
  }

  function receipt(b) {
    if (!b) return "";
    const what = b.kind === "free" ? `Free classification ${b.trial} of 3.` : "Included in your monthly plan.";
    return `<p class="receipt">${what} Counted in one transaction${b.attempts > 1 ? ` (retried ${b.attempts - 1}x after a write conflict)` : ""} on the ${H.esc(b.backend)}.</p>`;
  }

  // ------------------------------------------------------------------ Lab 10 page: ACID checks on the classifier billing
  function lab10Panel(host, h) {
    const { api, esc, explain } = h;
    host.innerHTML = `<section class="panel" style="margin-top:22px"><div class="panel-head"><h2>Applied to the classifier (Lab 7)</h2><span class="muted">3 free uses, then ${rupees(100)} per month</span></div>
      <div class="panel-pad"><p style="margin:0 0 10px">The Lab 7 classifier now has a paywall built from this lab's transactions. One use checks the free trials or the plan, runs the classifier and records the use, all in one transaction. A subscription debits the wallet, extends the plan, records the payment and credits revenue, also all in one. Try it on the <a href="#/classify">Classifier</a> page, or prove each ACID property here:</p>
      <div class="row-gap"><button class="btn primary" id="acid-run">Run the ACID checks (about 1 s)</button><span class="muted small" id="acid-be"></span></div>
      <div id="acid-out" style="margin-top:14px"></div></div></section>
      ${explain ? explain("What each check proves", `<ul class="plain"><li><b>Atomicity + isolation:</b> 10 classifications at the same instant by a new user. Exactly 3 get a free trial; the rest are refused. Conflicting transactions are retried, never double-counted.</li>
        <li><b>Atomicity:</b> a crash is simulated right after the wallet was debited. The whole payment rolls back, so the wallet is unchanged and no plan is created.</li>
        <li><b>Consistency:</b> two Pay requests with the same idempotency key (a double click). The unique index {user_id, idem} lets only one commit, so the user is charged once. Money is checked afterwards: top-ups minus subscriptions equals the wallets, and revenue equals the subscriptions.</li>
        <li><b>Durability:</b> commits use write concern majority, and the replica set journals majority writes (writeConcernMajorityJournalDefault).</li></ul>`) : ""}`;
    $("#acid-run").addEventListener("click", (e) => h.act(e.currentTarget, "Running", async () => {
      const r = await api("/api/billing/acid-checks", { method: "POST" });
      const row = (name, ok, text) => `<div class="result ${ok ? "ok" : "err"}" style="margin-bottom:8px"><b>${ok ? "PASS" : "FAIL"} ${esc(name)}</b><span>${text}</span></div>`;
      const f = r.free_trial_race, c = r.crash_after_debit, d = r.double_click, i = r.invariants;
      $("#acid-be").textContent = `Ran on the ${r.backend}`;
      $("#acid-out").innerHTML =
        row("Atomicity + isolation: free-trial race", f.pass, `${f.requests} simultaneous requests: ${f.granted} granted, ${f.refused} refused, ${f.retries} write-conflict retries, free_used = ${f.free_used}.`) +
        row("Atomicity: crash after debit", c.pass, `Wallet ${rupees(c.wallet_before)} before and ${rupees(c.wallet_after)} after; plan active: ${c.plan_active_after}; payments recorded: ${c.subscription_payments}.`) +
        row("Consistency: double click on Pay", d.pass, `Responses: ${esc(d.responses.join(", "))}. Wallet ${rupees(d.wallet_before)} to ${rupees(d.wallet_after)}, ${d.subscription_payments} payment.`) +
        row("Consistency: money conserved", i.money_conserved && i.revenue_matches, `Top-ups ${rupees(i.topups)} minus subscriptions ${rupees(i.subscriptions)} = wallets ${rupees(i.wallets)}; revenue ${rupees(i.revenue)}.`) +
        row("Durability: majority + journal", r.durability.pass, `writeConcern majority, writeConcernMajorityJournalDefault = ${esc(String(r.durability.journal_default))}.`) +
        `<pre class="term" style="height:auto;max-height:220px;margin-top:10px">${esc(r.log.join("\n"))}</pre>`;
    }));
  }

  // ------------------------------------------------------------------ Lab 11 page: the billing data on the shards
  function lab11Panel(host, h) {
    const { api, esc, num, explain, act } = h;
    host.innerHTML = `<section class="panel"><div class="panel-head"><h2>The classifier's billing on the shards</h2><span class="muted" id="bs-be"></span></div><div id="bs-body" class="panel-pad"></div></section>
      <div class="row-gap" style="margin-top:16px;flex-wrap:wrap"><button class="btn primary" id="bs-run">Move billing to the shards and load-test with 120 users</button><span class="muted small">The move happens once; after that only the load test runs.</span></div>
      <div id="bs-out"></div>
      ${explain("How Lab 11 shapes the Lab 10 billing data", `<ul class="plain"><li><b>Shard keys:</b> accounts on <span class="mono">{_id: 'hashed'}</span> (the user id), usage on <span class="mono">{user_id: 'hashed', at: 1}</span> (the refinement recommended in Lab 11.1), payments on <span class="mono">{user_id: 'hashed'}</span>. Every request of a user goes to one shard.</li>
        <li><b>Unique keys:</b> MongoDB refuses a unique index on the payment's idempotency key alone in a sharded collection. The index must contain the shard key, so it is <span class="mono">{user_id, idem}</span>.</li>
        <li><b>No hot document:</b> revenue is split into 8 bucket documents instead of one counter that every payment would fight over.</li>
        <li><b>Distributed transactions:</b> when a transaction writes to one shard it commits there (singleShard). When it also updates a revenue bucket on the other shard, mongos runs a <b>two-phase commit</b>. The counts below come from mongos serverStatus.</li></ul>`)}`;
    const paint = async () => {
      const s = await api("/api/billing/shards").catch((e) => ({ available: false, reason: e.message }));
      $("#bs-be").textContent = s.backend ? `live backend: ${s.backend}` : "";
      if (!s.available) { $("#bs-body").innerHTML = `<p class="muted" style="margin:0">${esc(s.reason || "")}</p>`; return; }
      const names = Object.keys(s.distribution.accounts || {});
      const ct = s.commit_types || {};
      $("#bs-body").innerHTML = (s.backend_name !== "sharded" ? `<p style="margin:0 0 12px">Billing still runs on the replica set. Press the button below to move it to the shards.</p>` : "") +
        `<div style="overflow-x:auto"><table class="table"><thead><tr><th>Collection</th><th>Shard key</th>${names.map((n) => `<th class="num">${esc(n)}</th>`).join("")}</tr></thead><tbody>${Object.entries(s.distribution).map(([c, d]) => `<tr><td class="mono">cti_billing.${esc(c)}</td><td class="mono small">${esc(s.keys[c] || "not sharded yet")}</td>${names.map((n) => `<td class="num">${num(d[n] || 0)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
        <div class="stat-line" style="margin-top:12px"><div><b>${num(ct.singleShard || 0)}</b><span>single-shard commits</span></div><div><b>${num(ct.singleWriteShard || 0)}</b><span>single write shard</span></div><div><b>${num(ct.twoPhaseCommit || 0)}</b><span>two-phase commits</span></div><div><b>${num(ct.readOnly || 0)}</b><span>read-only</span></div></div>
        <p class="muted small" style="margin:8px 0 0">Successful transaction commits counted by mongos since it started.</p>`;
    };
    paint();
    $("#bs-run").addEventListener("click", (e) => act(e.currentTarget, "Moving data and running 120 users", async () => {
      const r = await api("/api/billing/scale-out", { method: "POST", body: { users: 120 } });
      const l = r.load;
      $("#bs-out").innerHTML = `<section class="panel" style="margin-top:16px"><div class="panel-head"><h2>Load test result</h2><span class="muted">${l.users} users, ${l.threads} threads, ${l.seconds} s</span></div><div class="panel-pad">
        <div class="stat-line" style="border-top:none"><div><b>${num(l.ok)}</b><span>classifications</span></div><div><b>${num(l.refused)}</b><span>refused after 3 free</span></div><div><b>${num(l.subscribed)}</b><span>subscriptions</span></div><div><b>${num(l.commit_types.twoPhaseCommit || 0)}</b><span>two-phase commits</span></div><div><b>${l.errors}</b><span>errors</span></div></div>
        <table class="table" style="margin-top:10px"><thead><tr><th>Query</th><th class="num">Shards</th><th>Routing</th></tr></thead><tbody>${Object.entries(l.targeting).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num"><span class="hops ${v.n_shards === 1 ? "hop1" : "hop2"}">${v.n_shards} of ${v.total_shards}</span></td><td class="small">${esc(v.kind)}</td></tr>`).join("")}</tbody></table>
        <p style="margin:10px 0 0">Money conserved across shards: <b>${l.invariants.money_conserved && l.invariants.revenue_matches ? "yes" : "NO"}</b> (wallets ${rupees(l.invariants.wallets)}, revenue ${rupees(l.invariants.revenue)}).</p>
        <pre class="term" style="height:auto;max-height:240px;margin-top:10px">${esc(r.log.join("\n"))}</pre></div></section>`;
      paint();
    }));
  }

  const $ = (s) => document.querySelector(s);
  return { mount, refresh, user, paywall, receipt, lab10Panel, lab11Panel };
})();

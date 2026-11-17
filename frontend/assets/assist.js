/* Kavach Assist - final assessment front end: login screen (password + Google), the RAG chat, the admin console.
   Loaded before app.js. window.KavachAuth gates the whole website; window.KavachAssist adds the #/assist and #/admin pages. */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SHIELD = `<svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true"><path d="M16 2 4 7v8c0 7.5 5.1 13.4 12 15 6.9-1.6 12-7.5 12-15V7L16 2Z" fill="var(--accent)"/><path d="M16 9v14M10 14h12" stroke="var(--bg)" stroke-width="3" stroke-linecap="round"/></svg>`;

  async function call(path, opts = {}) {
    const res = await fetch(path, { method: opts.method || "GET", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: opts.body ? JSON.stringify(opts.body) : undefined });
    let data = null;
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) { const err = new Error((data && data.detail && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || `Request failed (${res.status})`); err.status = res.status; throw err; }
    return data;
  }

  // ================================================================== auth
  const Auth = { user: null, config: null, _resolve: null, _shown: false };
  window.KavachAuth = Auth;

  Auth.ready = async function ready() {
    try { Auth.config = await call("/api/auth/config"); } catch (e) { Auth.config = { auth_required: true, signup_open: true }; }
    try { Auth.user = (await call("/api/auth/me")).user; } catch (e) { Auth.user = null; }
    if (Auth.user || Auth.config.auth_required === false) { paintUser(); return Auth.user; }
    await new Promise((resolve) => { Auth._resolve = resolve; showLogin(false); });
    return Auth.user;
  };
  Auth.expired = function expired() { if (!Auth._shown && Auth.config && Auth.config.auth_required !== false) showLogin(true); };
  Auth.logout = async function logout() { try { await call("/api/auth/logout", { method: "POST" }); } catch (e) { /* ignore */ } location.hash = ""; location.reload(); };

  function paintUser() {
    const u = Auth.user;
    const slot = $("#topbar-right") || $(".rail-foot");
    if (!slot) return;
    let chip = $("#user-chip");
    if (!u) { if (chip) chip.remove(); return; }
    if (!chip) { chip = document.createElement("div"); chip.id = "user-chip"; chip.className = "user-chip"; slot.prepend(chip); }
    const initials = (u.name || u.email).split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
    chip.innerHTML = `<span class="av">${u.picture ? `<img src="${esc(u.picture)}" alt="" referrerpolicy="no-referrer">` : esc(initials)}</span>
      <span class="who"><b title="${esc(u.email)}">${esc(u.name || u.email)}</b><span>${u.role === "admin" ? "Administrator" : u.provider === "google" ? "Google account" : "Signed in"}</span></span>
      <button type="button" id="logout-btn" title="Sign out">Sign out</button>`;
    $("#logout-btn").addEventListener("click", Auth.logout);
    $$("[data-admin-only]").forEach((el) => { el.hidden = u.role !== "admin"; });
  }

  function showLogin(expired) {
    Auth._shown = true;
    const cfg = Auth.config || {};
    const el = document.createElement("div");
    el.id = "auth-screen";
    el.innerHTML = `
      <section class="auth-side">
        <div class="auth-brand">${SHIELD}<span>Kavach</span></div>
        <div>
          <h1>Ask your threat intelligence a question.</h1>
          <p class="lede">Kavach Assist answers from live threat data in MongoDB: which threats are Active, Evolving, Anticipated or Resolved for your platform, and exactly how to mitigate them.</p>
        </div>
        <ul class="auth-points">
          <li><span class="tick">1</span><span><b>Grounded answers.</b> Facts come from MongoDB queries and vector search; a local language model writes the explanation.</span></li>
          <li><span class="tick">2</span><span><b>Remembers the conversation.</b> Every chat is stored in MongoDB, so follow-up questions work and survive restarts.</span></li>
          <li><span class="tick">3</span><span><b>Scanner bot.</b> Finds vulnerable dependencies and settings, and fixes them only after you confirm in chat.</span></li>
        </ul>
        <p class="small muted">23CSE528 NoSQL Database using MongoDB - final assessment - M Nagasai Girisharan (24BTRCL114)</p>
      </section>
      <section class="auth-main"><div class="auth-card">
        <h2 id="auth-title">Sign in</h2>
        <p class="muted" id="auth-sub">${expired ? "Your session ended. Sign in again to continue." : "Use your Google account or your email and password."}</p>
        <div class="auth-tabs" role="tablist"><button type="button" data-mode="login" class="active">Sign in</button><button type="button" data-mode="register">Create account</button></div>
        <div id="auth-msg"></div>
        <form id="auth-form" novalidate>
          <div class="fld" id="fld-name" hidden><label for="au-name">Your name</label><input id="au-name" autocomplete="name" maxlength="80"></div>
          <div class="fld"><label for="au-email">Email</label><input id="au-email" type="email" autocomplete="username" required></div>
          <div class="fld"><label for="au-pass">Password</label><input id="au-pass" type="password" autocomplete="current-password" required><span class="hint" id="pass-hint" hidden>At least 8 characters with a letter and a number.</span></div>
          <button class="btn primary wide" type="submit" id="auth-submit">Sign in</button>
        </form>
        <div class="auth-or">or</div>
        <div id="g-btn"></div>
        <p class="auth-note">The administrator signs in with the admin email and password. Everyone else can use Google or create an account${cfg.signup_needs_approval ? " (new accounts wait for the administrator's approval)" : ""}.</p>
      </div></section>`;
    document.body.appendChild(el);
    let mode = "login";
    const msg = (text, ok) => { $("#auth-msg").innerHTML = text ? `<p class="${ok ? "auth-ok" : "auth-err"}" role="alert">${esc(text)}</p>` : ""; };
    const setMode = (m) => {
      mode = m;
      $$(".auth-tabs button", el).forEach((b) => b.classList.toggle("active", b.dataset.mode === m));
      $("#fld-name").hidden = m !== "register"; $("#pass-hint").hidden = m !== "register";
      $("#auth-title").textContent = m === "register" ? "Create your account" : "Sign in";
      $("#auth-submit").textContent = m === "register" ? "Create account" : "Sign in";
      $("#au-pass").autocomplete = m === "register" ? "new-password" : "current-password";
      msg("");
    };
    $$(".auth-tabs button", el).forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
    if (cfg.signup_open === false) $$(".auth-tabs button", el)[1].disabled = true;
    const done = (user) => {
      Auth.user = user; Auth._shown = false; el.remove(); paintUser();
      if (expired) { location.reload(); return; }
      if (!location.hash || location.hash === "#/") history.replaceState(null, "", "#/assist");   // no hashchange event: app.js renders once
      if (Auth._resolve) { Auth._resolve(user); Auth._resolve = null; }
    };
    $("#auth-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("#auth-submit");
      btn.disabled = true; msg("");
      try {
        const body = { email: $("#au-email").value.trim(), password: $("#au-pass").value, name: $("#au-name").value.trim() };
        const r = await call(mode === "register" ? "/api/auth/register" : "/api/auth/login", { method: "POST", body });
        if (r.pending) { msg(r.detail, true); setMode("login"); msg(r.detail, true); } else done(r.user);
      } catch (err) { msg(err.message); } finally { btn.disabled = false; }
    });
    // ---- Google Identity Services
    const gbox = $("#g-btn");
    const fallback = (why) => {
      gbox.innerHTML = `<button type="button" class="g-fallback"><svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.2C12.4 13.6 17.7 9.5 24 9.5z"/><path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 7l7.2 5.6c4.2-3.9 7.1-9.6 7.1-17.1z"/><path fill="#FBBC05" d="M10.5 28.6c-.5-1.4-.8-3-.8-4.6s.3-3.2.8-4.6l-7.9-6.2C1 16.500 0 20.1 0 24s1 7.5 2.600 10.800l7.900-6.200z"/><path fill="#34A853" d="M24 48c6.300 0 11.700-2.100 15.600-5.700l-7.200-5.600c-2.100 1.400-4.900 2.300-8.400 2.300-6.300 0-11.600-4.100-13.500-9.800l-7.900 6.200C6.500 42.600 14.600 48 24 48z"/></svg>Continue with Google</button>`;
      $(".g-fallback", gbox).addEventListener("click", () => msg(why));
    };
    if (!cfg.google_client_id) {
      fallback("Google sign-in is not switched on yet. The administrator adds GOOGLE_CLIENT_ID to the .env file (README, section Google sign-in) and restarts. Until then, create an account with email and password.");
    } else {
      const s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client"; s.async = true;
      s.onload = () => {
        try {
          window.google.accounts.id.initialize({ client_id: cfg.google_client_id, callback: async (resp) => {
            try { done((await call("/api/auth/google", { method: "POST", body: { credential: resp.credential } })).user); } catch (err) { msg(err.message); }
          } });
          window.google.accounts.id.renderButton(gbox, { theme: document.documentElement.dataset.theme === "dark" ? "filled_black" : "outline", size: "large", width: 360, text: "continue_with", shape: "rectangular" });
        } catch (e) { fallback("Google sign-in could not start in this browser. Use email and password."); }
      };
      s.onerror = () => fallback("Google could not be reached (no internet?). Use email and password.");
      document.head.appendChild(s);
    }
    setTimeout(() => $("#au-email") && $("#au-email").focus(), 50);
  }

  // ================================================================== small markdown renderer (escapes first)
  function inline(t) {
    return esc(t)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])_([^_]+)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
      .replace(/(^|[\s(])\*([^*\s][^*]*)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
      .replace(/\[(\d+)\]/g, '<sup class="muted">[$1]</sup>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;])/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  }
  function md(text) {
    const lines = String(text || "").replace(/\r/g, "").split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const l = lines[i];
      if (!l.trim()) { i++; continue; }
      if (/^\s*---+\s*$/.test(l)) { out.push("<hr>"); i++; continue; }
      if (/^\s*\|.*\|\s*$/.test(l)) {
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(lines[i].trim().slice(1, -1).split("|").map((c) => c.trim())); i++; }
        const body = rows.filter((r) => !r.every((c) => /^:?-+:?$/.test(c)));
        out.push(`<table>${body.map((r, k) => `<tr>${r.map((c) => (k === 0 ? `<th>${inline(c)}</th>` : `<td>${inline(c)}</td>`)).join("")}</tr>`).join("")}</table>`);
        continue;
      }
      const ol = /^\s*\d+[.)]\s+/, ul = /^\s*[-*]\s+/;
      if (ol.test(l) || ul.test(l)) {
        const ordered = ol.test(l), rx = ordered ? ol : ul, items = [];
        while (i < lines.length && (rx.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
          if (rx.test(lines[i])) items.push(lines[i].replace(rx, "")); else items[items.length - 1] += "<br>" + "\u0000" + lines[i].trim();
          i++;
        }
        out.push(`<${ordered ? "ol" : "ul"}>${items.map((it) => `<li>${it.split("<br>\u0000").map(inline).join("<br>")}</li>`).join("")}</${ordered ? "ol" : "ul"}>`);
        continue;
      }
      const m = /^(#{1,4})\s+(.*)$/.exec(l);
      if (m) { out.push(`<p><strong>${inline(m[2])}</strong></p>`); i++; continue; }
      const para = [];
      while (i < lines.length && lines[i].trim() && !ol.test(lines[i]) && !ul.test(lines[i]) && !/^\s*\|.*\|\s*$/.test(lines[i]) && !/^\s*---+\s*$/.test(lines[i])) { para.push(lines[i]); i++; }
      out.push(`<p>${para.map(inline).join("<br>")}</p>`);
    }
    return out.join("");
  }

  // ================================================================== pages
  window.KavachAssist = (h) => {
    const { num, api, toast, onLeave, ago, fmtTime } = h;
    const S = { cid: null, platform: null, busy: false, status: null, lastNote: null };
    try { S.platform = sessionStorage.getItem("ka-platform") || null; } catch (e) { /* ignore */ }

    const card = (c) => `<div class="tcard st-${esc(c.status)}" data-ask="Tell me about ${esc(c.kind === "vulnerability" ? c.id : `the report "${(c.title || "").slice(0, 70)}"`)} and how to mitigate it" ${c.kind === "vulnerability" ? "" : `data-url="${esc(c.url || "")}"`}>
        <div class="top"><span class="id">${esc(c.kind === "vulnerability" ? c.id : "Threat report")}</span><span class="badge">${esc(c.status)}</span></div>
        <div class="t">${esc(c.title || "")}</div><div class="r">${esc(c.severity || "")}${c.cvss ? ` - CVSS ${c.cvss}` : ""} - ${esc((c.platforms || []).slice(0, 3).join(", "))}<br>${esc(c.reason || "")}</div></div>`;
    const sources = (list) => (list && list.length ? `<details><summary>Sources (${list.length})</summary><ol>${list.map((s) => `<li>${esc(s.title || "")} <span class="muted">- ${esc(s.kind || "")}${s.source ? `, ${esc(s.source)}` : ""}${s.score != null ? `, score ${Number(s.score).toFixed(2)}` : ""}</span></li>`).join("")}</ol></details>` : "");
    const metaLine = (m) => {
      if (!m || !m.provider) return "";
      const bits = [m.provider === "builtin" ? "built-in writer" : `${esc(m.provider)} - ${esc(m.model || "")}`];
      if (m.ms != null) bits.push(m.ms >= 1000 ? `${(m.ms / 1000).toFixed(1)} s` : `${m.ms} ms`);
      if (m.tokens) bits.push(`${num(m.tokens)} tokens`);
      if (m.retrieval) bits.push(esc(m.retrieval));
      if (m.llm_error) bits.push(`model unavailable, answered from data`);
      return `<div class="meta">${bits.map((b) => `<span>${b}</span>`).join("")}</div>`;
    };
    function bubble(role, content, meta = {}, live = false) {
      if (role === "user") return `<div class="msg user">${esc(content)}</div>`;
      return `<div class="msg assistant"><div class="who">Kavach Assist</div><div class="body">${live && !content ? '<span class="typing"><i></i><i></i><i></i></span>' : md(content)}</div>
        ${(meta.cards || []).length ? `<div class="cards">${meta.cards.map(card).join("")}</div>` : ""}
        ${meta.confirm ? `<div class="confirm-row"><button class="btn primary small" data-say="yes">Yes, apply</button><button class="btn small" data-say="no">No, cancel</button></div>` : ""}
        ${sources(meta.sources)}${metaLine(meta)}</div>`;
    }
    const scrollDown = () => { const t = $("#thread"); if (t) t.scrollTop = t.scrollHeight; };

    function emptyState() {
      const st = S.status || {};
      return `<div class="empty-chat"><h2>What do you want to know${S.platform ? ` about ${esc(S.platform)}` : ""}?</h2>
        <p>Ask about Active, Evolving, Anticipated or Resolved threats for a platform, a CVE id, an IP address, the scanner bot, or anything from Labs 7 to 18.</p>
        <div class="sugg">${(st.suggestions || []).map((s) => `<button type="button" data-ask="${esc(s)}">${esc(s)}</button>`).join("")}</div></div>`;
    }

    async function loadBoard() {
      const b = await api(`/api/assist/board${S.platform ? `?platform=${encodeURIComponent(S.platform)}` : ""}`).catch(() => null);
      const box = $("#board");
      if (!b || !box) return;
      box.innerHTML = ["Active", "Evolving", "Anticipated", "Resolved"].map((s) => {
        const c = b.by_status[s] || { total: 0, vulnerability: 0, report: 0 };
        return `<button type="button" class="st-${s}" data-ask="Show the ${s.toLowerCase()} threats for ${esc(S.platform || "all platforms")} and how to mitigate them"><span class="l">${s}</span><span class="n">${num(c.total)}</span><span class="s">${num(c.vulnerability)} ${c.vulnerability === 1 ? "vulnerability" : "vulnerabilities"}, ${num(c.report)} ${c.report === 1 ? "report" : "reports"}</span></button>`;
      }).join("");
    }
    async function loadConvs() {
      const r = await api("/api/assist/conversations").catch(() => ({ items: [] }));
      const box = $("#convs");
      if (!box) return;
      box.innerHTML = r.items.length ? r.items.map((c) => `<div class="ka-conv ${c.id === S.cid ? "active" : ""}"><a href="#/assist/${encodeURIComponent(c.id)}" title="${esc(c.title)}">${esc(c.title || "Conversation")}</a><button type="button" data-del="${esc(c.id)}" title="Delete conversation" aria-label="Delete conversation">&times;</button></div>`).join("")
        : `<p class="small muted" style="padding:6px">No conversations yet.</p>`;
    }

    async function send(text) {
      text = (text || "").trim();
      if (!text || S.busy) return;
      S.busy = true;
      const thread = $("#thread");
      const empty = $(".empty-chat", thread);
      if (empty) empty.remove();
      thread.insertAdjacentHTML("beforeend", bubble("user", text));
      const holder = document.createElement("div");
      holder.innerHTML = bubble("assistant", "", {}, true);
      const node = holder.firstElementChild;
      thread.appendChild(node);
      scrollDown();
      $("#ka-send").disabled = true;
      let answer = "", meta = {};
      const paint = (final) => { const tmp = document.createElement("div"); tmp.innerHTML = bubble("assistant", answer, meta, !final); node.innerHTML = tmp.firstElementChild.innerHTML; scrollDown(); };
      try {
        const res = await fetch("/api/assist/chat/stream", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text, conversation_id: S.cid, platform: S.platform }) });
        if (!res.ok) {
          let d = null; try { d = await res.json(); } catch (e) { /* ignore */ }
          if (res.status === 401 && window.KavachAuth) window.KavachAuth.expired();
          throw new Error((d && d.detail) || `Request failed (${res.status})`);
        }
        const reader = res.body.getReader(), dec = new TextDecoder();
        let buf = "", lastPaint = 0;
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let k;
          while ((k = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, k); buf = buf.slice(k + 2);
            if (!chunk.startsWith("data:")) continue;
            const ev = JSON.parse(chunk.slice(5));
            if (ev.type === "meta") { meta = { ...meta, cards: [], sources: [], provider: null, _cards: ev.cards, _sources: ev.sources }; if (!S.cid) { S.cid = ev.conversation_id; history.replaceState(null, "", `#/assist/${encodeURIComponent(S.cid)}`); } }
            else if (ev.type === "token") { answer += ev.text; if (Date.now() - lastPaint > 60) { paint(false); lastPaint = Date.now(); } }
            else if (ev.type === "reset") { answer = ""; }
            else if (ev.type === "done") { answer = ev.answer; meta = ev; }
            else if (ev.type === "error") { throw new Error(ev.detail); }
          }
        }
        paint(true);
      } catch (err) {
        answer = `**I could not answer.** ${err.message}`; meta = {}; paint(true);
      } finally {
        S.busy = false; $("#ka-send").disabled = false; $("#ka-input").focus();
        loadConvs();
        if (/scan|fix|undo|finding/i.test(text) || meta.intent && String(meta.intent).startsWith("scan")) refreshStatus();
      }
    }

    async function refreshStatus() {
      const st = await api("/api/assist/status").catch(() => null);
      if (!st) return;
      S.status = st;
      const box = $("#ka-pills");
      if (!box) return;
      const llm = st.llm || {}, k = st.knowledge || {}, bot = st.bot || {};
      const idx = (k.index || {}).queryable;
      box.innerHTML = `<span class="ka-pill" title="${esc(llm.note || "")}"><span class="dot ${llm.provider === "builtin" ? "warn" : "ok"}"></span>Model: <b>${llm.provider === "builtin" ? "built-in writer" : esc(llm.model)}</b>${llm.provider === "builtin" && llm.note ? ` <span>(${esc(llm.note)})</span>` : llm.local ? " (local, Ollama)" : ""}${llm.provider !== "builtin" && llm.note ? ' <span class="dot warn"></span>' : ""}</span>
        <span class="ka-pill"><span class="dot ${idx ? "ok" : "warn"}"></span>Knowledge: <b>${num(k.chunks || 0)} chunks</b> ${idx ? "- $vectorSearch" : "- exact search"}</span>
        <span class="ka-pill"><span class="dot ${bot.online ? "ok" : "err"}"></span>Scanner bot: <b>${bot.online ? "online" : "offline"}</b>${st.open_findings ? ` - ${st.open_findings} open` : ""}</span>`;
    }

    async function pollNotes(gen) {
      if (gen !== S.gen) return;
      const q = S.lastNote ? `?since=${encodeURIComponent(S.lastNote)}` : "";
      const r = await api(`/api/assist/notifications${q}`).catch(() => null);
      if (!r || gen !== S.gen) return;
      const first = S.lastNote === null;
      for (const n of r.items) {
        S.lastNote = n.ts;
        if (first && Date.now() - new Date(n.ts).getTime() > 15 * 60 * 1000) continue;     // on page load only show fresh ones
        const thread = $("#thread");
        if (!thread) continue;
        const empty = $(".empty-chat", thread);
        if (empty) empty.remove();
        thread.insertAdjacentHTML("beforeend", `<div class="msg bot"><div class="who">Scanner bot - ${esc(ago(n.ts))}</div>${esc(n.text)}${(n.top || []).length ? `<ul style="margin:6px 0 0;padding-left:20px">${n.top.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
          <div class="quick"><button type="button" data-ask="Show the scanner findings">Show findings</button><button type="button" data-ask="fix all">Fix all</button></div></div>`);
        scrollDown();
      }
      if (S.lastNote === null) S.lastNote = new Date().toISOString();
    }

    async function pageChat(page, route) {
      S.cid = route.arg || null;
      S.lastNote = null;
      const gen = S.gen = (S.gen || 0) + 1;              // a newer render of this page cancels the older one's async work
      const plats = ["Linux", "Windows", "macOS", "Android", "iOS", "Network devices", "Web & server software", "Cloud & SaaS"];
      page.classList.add("ka-page");
      onLeave(() => page.classList.remove("ka-page"));
      page.innerHTML = `<div class="ka-head"><h1>Kavach Assist</h1><div class="right"><div class="ka-pills" id="ka-pills"></div><button class="btn small" type="button" id="ka-new">New chat</button></div></div>
        <div class="plat-row" role="group" aria-label="Platform"><button type="button" data-plat="" class="${S.platform ? "" : "active"}">All platforms</button>${plats.map((p) => `<button type="button" data-plat="${esc(p)}" class="${S.platform === p ? "active" : ""}">${esc(p)}</button>`).join("")}</div>
        <div class="board" id="board"></div>
        <div class="ka-wrap"><aside class="ka-side"><h3>Your conversations</h3><div id="convs"></div></aside>
          <section class="ka-main"><div class="thread" id="thread" aria-live="polite"></div>
            <form class="composer" id="ka-form"><label class="sr-only" for="ka-input">Your question</label><textarea id="ka-input" rows="1" maxlength="2000" placeholder="Ask about threats, a CVE, the scanner bot or the labs..."></textarea><button class="btn primary" id="ka-send" type="submit">Send</button></form>
            <div class="composer-hint"><span>Enter to send, Shift+Enter for a new line. Conversations are stored in MongoDB.</span><span id="ka-plat-hint">${S.platform ? `Platform: ${esc(S.platform)}` : "Platform: all"}</span></div></section></div>`;
      await refreshStatus();
      if (gen !== S.gen) return;
      const thread = $("#thread");
      if (S.cid) {
        try {
          const c = await api(`/api/assist/conversations/${encodeURIComponent(S.cid)}`);
          thread.innerHTML = c.messages.map((m, i) => bubble(m.role, m.content, { ...m.meta, confirm: m.meta && m.meta.confirm && i === c.messages.length - 1 && c.pending })).join("") || emptyState();
        } catch (e) { S.cid = null; thread.innerHTML = emptyState(); history.replaceState(null, "", "#/assist"); }
      } else thread.innerHTML = emptyState();
      if (gen !== S.gen) return;
      scrollDown();
      loadBoard(); loadConvs(); pollNotes(gen);
      const t1 = setInterval(() => pollNotes(gen), 20000), t2 = setInterval(refreshStatus, 30000);
      onLeave(() => { clearInterval(t1); clearInterval(t2); });
      const input = $("#ka-input");
      const grow = () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 150)}px`; };
      input.addEventListener("input", grow);
      input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#ka-form").requestSubmit(); } });
      $("#ka-form").addEventListener("submit", (e) => { e.preventDefault(); const v = input.value; input.value = ""; grow(); send(v); });
      $("#ka-new").addEventListener("click", () => { if (location.hash === "#/assist") h.render(); else location.hash = "#/assist"; });
      page.addEventListener("click", async (e) => {
        const ask = e.target.closest("[data-ask]"), say = e.target.closest("[data-say]"), plat = e.target.closest("[data-plat]"), del = e.target.closest("[data-del]");
        if (say) { say.closest(".confirm-row").remove(); send(say.dataset.say); }
        else if (ask) send(ask.dataset.ask);
        else if (plat) {
          S.platform = plat.dataset.plat || null;
          try { sessionStorage.setItem("ka-platform", S.platform || ""); } catch (err) { /* ignore */ }
          $$("[data-plat]").forEach((b) => b.classList.toggle("active", (b.dataset.plat || null) === S.platform));
          $("#ka-plat-hint").textContent = S.platform ? `Platform: ${S.platform}` : "Platform: all";
          loadBoard();
          const empty = $(".empty-chat"); if (empty) empty.outerHTML = emptyState();
        } else if (del) {
          await api(`/api/assist/conversations/${encodeURIComponent(del.dataset.del)}`, { method: "DELETE" }).catch((err) => toast(esc(err.message)));
          if (del.dataset.del === S.cid) location.hash = "#/assist"; else loadConvs();
        }
      });
      input.focus();
    }

    // ---------------------------------------------------------------- admin console
    const tag = (ok, yes, no) => `<span class="tag ${ok ? "ok" : "err"}">${ok ? yes : no}</span>`;
    async function pageAdmin(page, route) {
      if (!Auth.user || Auth.user.role !== "admin") { page.innerHTML = `<div class="panel empty"><strong>Administrator only</strong>Sign in with the administrator account to open this page.</div>`; return; }
      const tab = route.params.get("tab") || "overview";
      const TABS = [["overview", "Overview"], ["users", "Users"], ["usage", "Usage and latency"], ["knowledge", "Knowledge base"], ["scanner", "Scanner bot"], ["audit", "Audit log"]];
      page.innerHTML = `<div class="page-head"><div><h1>Admin console</h1><p class="lede">Users, usage, the knowledge base and the scanner bot of Kavach Assist. Only the administrator can open this page.</p></div></div>
        <nav class="tabs" role="tablist">${TABS.map(([k, t]) => `<button role="tab" data-tab="${k}" class="${tab === k ? "active" : ""}">${t}</button>`).join("")}</nav><div id="adm"></div>`;
      $$("[data-tab]", page).forEach((b) => b.addEventListener("click", () => { location.hash = `#/admin?tab=${b.dataset.tab}`; }));
      const box = $("#adm");
      const reload = () => h.render();
      if (tab === "overview") {
        const o = await api("/api/admin/overview");
        const m = o.metrics || {}, lat = m.latency_ms || {};
        const tile = (n, l) => `<div class="tile"><span class="n">${n}</span><span class="l">${l}</span></div>`;
        box.innerHTML = `<div class="tiles">${tile(num(o.users), "users")}${tile(num(o.google_users), "signed in with Google")}${tile(num(o.conversations), "conversations")}${tile(num(o.messages), "messages stored")}
            ${tile(num(m.requests || 0), "chat requests (24 h)")}${tile(lat.p50 != null ? `${num(lat.p50)} ms` : "-", "median latency")}${tile(lat.p95 != null ? `${num(lat.p95)} ms` : "-", "95th percentile")}
            ${tile(`${((m.error_rate || 0) * 100).toFixed(1)}%`, "error rate")}${tile(num((m.tokens || {}).total || 0), "LLM tokens (24 h)")}${tile(num(o.threats), "threats in the catalog")}${tile(num(o.open_findings), "open scanner findings")}</div>
          <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">
            <div class="panel panel-pad"><h3 style="margin-bottom:8px">Sign-up settings</h3>
              <div class="switch-row"><div><b>Allow new accounts</b><small>People can create an account or sign in with Google for the first time.</small></div><input type="checkbox" id="set-open" ${o.settings.signup_open ? "checked" : ""} aria-label="Allow new accounts"></div>
              <div class="switch-row"><div><b>New accounts need my approval</b><small>A new user cannot sign in until you approve the account on the Users tab.</small></div><input type="checkbox" id="set-appr" ${o.settings.signup_needs_approval ? "checked" : ""} aria-label="New accounts need approval"></div>
              <div class="switch-row"><div><b>Google sign-in</b><small>${o.google_configured ? "Configured (GOOGLE_CLIENT_ID is set)." : "Not configured: add GOOGLE_CLIENT_ID to .env and restart (README)."}</small></div>${tag(o.google_configured, "on", "off")}</div></div>
            <div class="panel panel-pad"><h3 style="margin-bottom:8px">Services</h3>
              <div class="switch-row"><div><b>Language model</b><small>${esc(o.llm.provider)} - ${esc(o.llm.model)}${o.llm.note ? ` (${esc(o.llm.note)})` : ""}</small></div>${tag(o.llm.provider !== "builtin", "model", "built-in")}</div>
              <div class="switch-row"><div><b>Scanner bot</b><small>${o.bot.online ? `last scan ${esc(ago(o.bot.last_scan))}, target ${esc(o.bot.target || "")}` : "not running - docker compose up -d scanner-bot"}</small></div>${tag(o.bot.online, "online", "offline")}</div>
              <div class="switch-row"><div><b>Waiting for approval</b><small>Accounts that cannot sign in yet.</small></div><span class="tag ${o.waiting_approval ? "warn" : ""}">${num(o.waiting_approval)}</span></div></div></div>`;
        const save = async () => { try { await api("/api/admin/settings", { method: "POST", body: { signup_open: $("#set-open").checked, signup_needs_approval: $("#set-appr").checked } }); toast("Settings saved"); } catch (e) { toast(esc(e.message)); } };
        $("#set-open").addEventListener("change", save); $("#set-appr").addEventListener("change", save);
      } else if (tab === "users") {
        const r = await api("/api/admin/users");
        box.innerHTML = `<div class="panel panel-pad" style="margin-bottom:16px"><h3 style="margin-bottom:10px">Add a user</h3><form class="form-inline" id="add-user">
            <div class="fld" style="margin:0"><label for="nu-email">Email</label><input id="nu-email" type="email" required></div><div class="fld" style="margin:0"><label for="nu-name">Name</label><input id="nu-name"></div>
            <div class="fld" style="margin:0"><label for="nu-pass">Password</label><input id="nu-pass" type="text" autocomplete="off" required></div><button class="btn primary" type="submit">Add user</button></form></div>
          <div class="panel ka-scroll"><table class="ka-table"><thead><tr><th>User</th><th>Role</th><th>Sign-in</th><th>Status</th><th>Questions</th><th>Last sign-in</th><th>Actions</th></tr></thead><tbody>
          ${r.items.map((u) => `<tr><td><b>${esc(u.name)}</b><br><span class="muted small">${esc(u.email)}</span></td><td>${u.role === "admin" ? '<span class="tag warn">admin</span>' : "user"}</td><td>${esc(u.provider)}</td>
            <td>${!u.approved ? '<span class="tag warn">waiting</span>' : u.active ? '<span class="tag ok">active</span>' : '<span class="tag err">disabled</span>'}</td><td>${num(u.questions)}</td><td>${u.last_login ? esc(fmtTime(u.last_login)) : "never"}</td>
            <td>${u.role === "admin" ? '<span class="muted small">the administrator</span>' : `<div class="row-gap" style="flex-wrap:wrap">
              ${!u.approved ? `<button class="btn small primary" data-act="approve" data-id="${u.id}">Approve</button>` : ""}
              <button class="btn small" data-act="${u.active ? "disable" : "enable"}" data-id="${u.id}">${u.active ? "Disable" : "Enable"}</button>
              ${u.provider === "password" ? `<button class="btn small" data-act="reset" data-id="${u.id}">Reset password</button>` : ""}
              <button class="btn small" data-act="signout" data-id="${u.id}">Sign out</button><button class="btn small" data-act="delete" data-id="${u.id}" data-email="${esc(u.email)}">Delete</button></div>`}</td></tr>`).join("")}</tbody></table></div><div id="adm-msg" style="margin-top:12px"></div>`;
        $("#add-user").addEventListener("submit", async (e) => { e.preventDefault(); try { await api("/api/admin/users", { method: "POST", body: { email: $("#nu-email").value, name: $("#nu-name").value, password: $("#nu-pass").value } }); toast("User added"); reload(); } catch (err) { toast(esc(err.message)); } });
        box.addEventListener("click", async (e) => {
          const b = e.target.closest("[data-act]");
          if (!b) return;
          const id = b.dataset.id, act = b.dataset.act;
          try {
            if (act === "approve") await api(`/api/admin/users/${id}`, { method: "PATCH", body: { approved: true } });
            else if (act === "disable" || act === "enable") await api(`/api/admin/users/${id}`, { method: "PATCH", body: { active: act === "enable" } });
            else if (act === "signout") await api(`/api/admin/users/${id}/signout`, { method: "POST" });
            else if (act === "reset") { const r2 = await api(`/api/admin/users/${id}/reset-password`, { method: "POST" }); $("#adm-msg").innerHTML = `<div class="banner"><div><strong>Temporary password (shown once)</strong><span class="mono">${esc(r2.temporary_password)}</span> - give it to the user; they are signed out everywhere.</div></div>`; return; }
            else if (act === "delete") { if (b.dataset.armed !== "1") { b.dataset.armed = "1"; b.textContent = "Click again to delete"; setTimeout(() => { b.dataset.armed = ""; b.textContent = "Delete"; }, 4000); return; } await api(`/api/admin/users/${id}`, { method: "DELETE" }); }
            toast("Done"); reload();
          } catch (err) { toast(esc(err.message)); }
        });
      } else if (tab === "usage") {
        const m = await api("/api/admin/metrics?hours=24");
        const lat = m.latency_ms || {}, max = Math.max(1, ...m.per_hour.map((x) => x.requests));
        const tbl = (head, rows) => `<div class="panel ka-scroll"><table class="ka-table"><thead><tr>${head.map((x) => `<th>${x}</th>`).join("")}</tr></thead><tbody>${rows.length ? rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${head.length}" class="muted">No requests yet.</td></tr>`}</tbody></table></div>`;
        box.innerHTML = `<div class="tiles"><div class="tile"><span class="n">${num(m.requests)}</span><span class="l">requests in 24 h</span></div><div class="tile"><span class="n">${lat.p50 != null ? num(lat.p50) : "-"} ms</span><span class="l">median latency</span></div>
            <div class="tile"><span class="n">${lat.p95 != null ? num(lat.p95) : "-"} ms</span><span class="l">95th percentile</span></div><div class="tile"><span class="n">${(m.error_rate * 100).toFixed(1)}%</span><span class="l">error rate (${num(m.errors)} errors)</span></div>
            <div class="tile"><span class="n">${num(m.rate_limited)}</span><span class="l">refused by the rate limiter</span></div><div class="tile"><span class="n">${num(m.tokens.total)}</span><span class="l">tokens (${num(m.tokens.prompt)} in, ${num(m.tokens.completion)} out)</span></div>
            <div class="tile"><span class="n">${num(m.llm_fallbacks)}</span><span class="l">answers that fell back to the built-in writer</span></div></div>
          <div class="panel panel-pad" style="margin-bottom:16px"><h3>Requests per hour</h3><div class="bars" role="img" aria-label="Requests per hour for the last 24 hours">${m.per_hour.map((x) => `<div style="height:${Math.max(4, Math.round(100 * x.requests / max))}%" title="${esc(x.hour)}: ${x.requests} requests, ${x.avg_ms} ms average"></div>`).join("") || '<span class="muted small">No requests yet.</span>'}</div></div>
          <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(300px,1fr))">${tbl(["Model", "Requests", "Average", "Tokens"], m.by_provider.map((x) => [esc(x.provider), num(x.requests), `${num(x.avg_ms)} ms`, num(x.tokens)]))}
            ${tbl(["Kind of question", "Requests"], m.by_intent.map((x) => [esc(x.intent), num(x.requests)]))}${tbl(["User", "Requests", "Tokens"], m.by_user.map((x) => [esc(x.email), num(x.requests), num(x.tokens)]))}</div>
          <p class="small muted" style="margin-top:12px">Prometheus format: <a href="/metrics" target="_blank" rel="noopener">/metrics</a> - health check: <a href="/health" target="_blank" rel="noopener">/health</a></p>`;
      } else if (tab === "knowledge") {
        const k = await api("/api/admin/knowledge");
        const last = k.last || {}, idx = k.index || {}, st = k.state || {};
        box.innerHTML = `<div class="tiles"><div class="tile"><span class="n">${num(k.chunks || 0)}</span><span class="l">chunks with embeddings</span></div>${Object.entries(k.by_kind || {}).map(([kk, n]) => `<div class="tile"><span class="n">${num(n)}</span><span class="l">${esc(kk)}</span></div>`).join("")}</div>
          <div class="panel panel-pad"><div class="switch-row"><div><b>Vector store</b><small>${esc(k.vector_store === "atlas-local" ? "MongoDB Atlas Local (mongod + mongot)" : "replica set rs0 (Atlas Local not reachable)")}</small></div>${tag(k.vector_store === "atlas-local", "Atlas Local", "fallback")}</div>
            <div class="switch-row"><div><b>vectorSearch index</b><small>${idx.queryable ? `queryable, ${idx.dims} dimensions, cosine; filters: kind, platforms, status` : `status ${esc(idx.status || "?")} - exact NumPy search is used until it is ready`}</small></div>${tag(idx.queryable, "ready", "not ready")}</div>
            <div class="switch-row"><div><b>Embedding model</b><small>${esc((k.embedder || {}).provider || "")} - ${esc((k.embedder || {}).model || "")} (${(k.embedder || {}).dims || "?"} dimensions)</small></div><span class="tag">${esc((k.embedder || {}).provider || "")}</span></div>
            <div class="switch-row"><div><b>Last ingestion</b><small>${last.at ? `${esc(ago(last.at))}: ${num(last.chunks)} chunks, ${num(last.embedded)} embedded, ${num(last.removed)} removed in ${last.seconds} s` : "not run yet"}${st.running ? ` - running now${st.progress ? ` (${esc(st.progress)})` : ""}` : ""}</small></div><button class="btn small primary" id="reingest" ${st.running ? "disabled" : ""}>Refresh now</button></div>
            <div class="switch-row"><div><b>Threat catalog</b><small>${k.threats_built ? `${num(k.threats_built.vulnerabilities)} vulnerabilities + ${num(k.threats_built.reports)} reports classified ${esc(ago(k.threats_built.at))}` : "not built yet"}</small></div><span></span></div></div>`;
        $("#reingest").addEventListener("click", async () => { await api("/api/admin/knowledge/ingest", { method: "POST" }); toast("Ingestion started - refresh this tab in a minute"); });
      } else if (tab === "scanner") {
        const s = await api("/api/admin/scanner");
        const sevTag = (x) => `<span class="sev sev-${esc(x)}">${esc(x)}</span>`;
        box.innerHTML = `<div class="tiles"><div class="tile"><span class="n">${s.bot.online ? "online" : "offline"}</span><span class="l">scanner bot${s.bot.host ? ` (${esc(s.bot.host)})` : ""}</span></div><div class="tile"><span class="n">${esc(ago(s.bot.last_scan))}</span><span class="l">last scan</span></div>
            <div class="tile"><span class="n">${num(s.findings.filter((f) => f.status === "open").length)}</span><span class="l">open findings</span></div><div class="tile"><span class="n">${num(s.findings.filter((f) => f.status === "fixed").length)}</span><span class="l">fixed after confirmation</span></div></div>
          <h3 style="margin:6px 0 10px">Findings</h3><div class="panel ka-scroll"><table class="ka-table"><thead><tr><th>Severity</th><th>Finding</th><th>Where</th><th>Status</th><th>Fix</th></tr></thead><tbody>
            ${s.findings.map((f) => `<tr><td>${sevTag(f.severity)}</td><td>${esc(f.title)}<br><span class="muted small">${esc((f.detail || "").slice(0, 160))}</span></td><td class="mono small">${esc(f.file || "")}</td>
              <td><span class="tag ${f.status === "open" ? "warn" : f.status === "fixed" || f.status === "resolved" ? "ok" : f.status === "failed" ? "err" : ""}">${esc(f.status)}</span></td><td class="small">${f.fixable ? esc(f.fix_text || "") : `<span class="muted">manual</span>`}${f.fixed_by ? `<br><span class="muted">confirmed by ${esc(f.fixed_by)}</span>` : ""}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">No findings yet.</td></tr>`}</tbody></table></div>
          <h3 style="margin:18px 0 10px">Confirmed jobs</h3><div class="panel ka-scroll"><table class="ka-table"><thead><tr><th>When</th><th>Job</th><th>Confirmed by</th><th>Status</th><th>Result</th></tr></thead><tbody>
            ${s.jobs.map((j) => `<tr><td>${esc(fmtTime(j.created_at))}</td><td>${esc(j.type)}</td><td>${esc(j.requested_by || "")}</td><td><span class="tag ${j.status === "done" ? "ok" : j.status === "failed" ? "err" : "warn"}">${esc(j.status)}</span></td><td class="small">${esc(j.summary || "")}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">Nothing has been fixed yet.</td></tr>`}</tbody></table></div>
          <h3 style="margin:18px 0 10px">Scan history</h3><div class="panel ka-scroll"><table class="ka-table"><thead><tr><th>When</th><th>Why</th><th>Found</th><th>New</th><th>Open</th><th>Seconds</th><th>Notes</th></tr></thead><tbody>
            ${s.runs.map((r) => `<tr><td>${esc(fmtTime(r.at))}</td><td>${esc(r.reason)}</td><td>${num(r.found)}</td><td>${num(r.new)}</td><td>${num(r.open)}</td><td>${r.seconds}</td><td class="small muted">${esc((r.notes || []).join("; "))}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">No scans yet.</td></tr>`}</tbody></table></div>`;
      } else {
        const a = await api("/api/admin/audit?limit=150");
        box.innerHTML = `<div class="panel ka-scroll"><table class="ka-table"><thead><tr><th>When</th><th>Action</th><th>Who</th><th>Details</th></tr></thead><tbody>
          ${a.items.map((x) => { const { ts, action, actor, ...rest } = x; return `<tr><td style="white-space:nowrap">${esc(fmtTime(ts))}</td><td class="mono small">${esc(action)}</td><td>${esc(actor || "")}</td><td class="small muted">${esc(Object.entries(rest).map(([k2, v]) => `${k2}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ").slice(0, 220))}</td></tr>`; }).join("") || `<tr><td colspan="4" class="muted">Nothing logged yet.</td></tr>`}</tbody></table></div>`;
      }
    }

    return { assist: pageChat, admin: pageAdmin };
  };
})();

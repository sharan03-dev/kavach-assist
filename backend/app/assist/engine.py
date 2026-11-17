"""A.3 + A.4 - the RAG chat engine with conversation memory in MongoDB.

One question goes through these steps:
    1. memory      load the last exchanges of this conversation from MongoDB (collection messages)
    2. understand  platform (Linux / Windows / macOS / Android / iOS ...), threat status (Active / Evolving / Anticipated /
                   Resolved), CVE ids, indicators, scanner commands, lab questions; follow-ups reuse the conversation context
    3. facts       exact MongoDB queries on the threat catalog (compound indexes, $facet aggregation)
    4. retrieve    $vectorSearch over the knowledge base, filtered by kind and platform
    5. generate    the language model writes the answer from facts + passages + history (streamed); if no model is
                   available the built-in composer writes it from the same data
    6. remember    the user message and the answer are stored with their sources, tokens and latency

Scanner commands (show findings / fix / undo) never go through the language model: they are executed exactly, and a fix
is only queued for the bot after the user confirmed it with "yes" in the conversation.
"""
from __future__ import annotations

import re
import secrets
import time
from datetime import timedelta

from pymongo.errors import PyMongoError

from .. import ai
from . import knowledge, llm, threats
from . import settings as S
from . import store

CVE_RX = re.compile(r"\b(CVE-\d{4}-\d{4,7})\b", re.I)
IP_RX = re.compile(r"\b((?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d))\b")
HASH_RX = re.compile(r"\b([a-f0-9]{64}|[a-f0-9]{40}|[a-f0-9]{32})\b", re.I)
PLATFORM_WORDS = [
    ("Android", r"\bandroid\b|\bpixel phone\b|\bsamsung phone\b"),
    ("iOS", r"\bios\b|\biphones?\b|\bipads?\b|\bipados\b"),
    ("macOS", r"\bmac ?os\b|\bmacs?\b|\bmacbooks?\b|\bos ?x\b|\bapple (laptop|computer)s?\b"),
    ("Windows", r"\bwindows\b|\bwin ?1[01]\b|\bwindows server\b|\bmicrosoft (windows|pc)\b"),
    ("Linux", r"\blinux\b|\bubuntu\b|\bdebian\b|\bred ?hat\b|\brhel\b|\bcentos\b|\bfedora\b|\bkali\b"),
    ("Network devices", r"\brouters?\b|\bfirewalls?\b|\bvpns?\b|\bnetwork (devices?|appliances?|equipment)\b|\bcisco\b|\bfortinet\b|\bivanti\b|\bpalo alto\b|\bciti?rix\b|\biot\b"),
    ("Cloud & SaaS", r"\bcloud\b|\baws\b|\bazure\b|\bgcp\b|\bkubernetes\b|\bsaas\b|\bcontainers?\b"),
    ("Web & server software", r"\bweb ?(servers?|apps?|applications?|sites?)\b|\bapache\b|\bnginx\b|\bwordpress\b|\bbrowsers?\b|\bchrome\b|\bfirefox\b|\bservers? software\b"),
]
STATUS_WORDS = [
    ("Active", r"\bactive(ly)?\b|\bongoing\b|\bcurrent(ly)?\b|\bbeing exploited\b|\bexploited (now|in the wild)\b|\bright now\b|\bin the wild\b"),
    ("Evolving", r"\bevolv\w+\b|\bemerging\b|\bchanging\b|\brising\b|\btrending\b|\bgrowing\b|\bnew variants?\b|\bdeveloping\b"),
    ("Anticipated", r"\banticipat\w+\b|\bupcoming\b|\bexpected\b|\bpredict\w+\b|\bfuture\b|\bpotential\b|\bforecast\w*\b|\bnot yet exploited\b"),
    ("Resolved", r"\bresolved\b|\bclosed\b|\b(already|been|were|was|got) (fixed|patched|mitigated)\b|\b(fixed|patched|mitigated) (threats?|vulnerabilit\w+|ones|issues)\b|\bno longer\b"),
]
_PLAT = [(p, re.compile(rx, re.I)) for p, rx in PLATFORM_WORDS]
_STAT = [(s, re.compile(rx, re.I)) for s, rx in STATUS_WORDS]
YES = re.compile(r"^\s*(yes|y|yep|yeah|yes please|confirm|confirmed|go ahead|do it|apply|ok|okay|sure|proceed|approve|approved)\b[\s.!]*$", re.I)
NO = re.compile(r"^\s*(no|n|nope|cancel|stop|don'?t|do not|abort|never ?mind)\b", re.I)
ORDINAL = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3, "fifth": 4, "5th": 4, "last": -1}
MITIGATE = re.compile(r"\b(mitigat\w*|fix\w*|protect\w*|prevent\w*|defen[dc]\w*|secur\w+|harden\w*|patch\w*|remediat\w*|what (should|can|do) (i|we) do|"
                      r"how (do|can|should) (i|we)|procedure|steps?|respond|response|recover\w*|stay safe|safe)\b", re.I)
THREAT_WORDS = re.compile(r"\b(threats?|vulnerab\w+|exploit\w*|attack\w*|malware|ransomware|phishing|spyware|trojan|botnet|zero[- ]?days?|cves?|"
                          r"breach\w*|risks?|hack\w*|advisor\w+|kev|infostealer|backdoor|worm|campaigns?)\b", re.I)
LAB_WORDS = re.compile(r"\b(labs?|exercises?|replica ?sets?|replication|shard\w*|write concern|read concern|transactions?|acid|aggregat\w+|index\w*|"
                       r"mongodb|mongo|cassandra|neo4j|cypher|cql|vector search|vectorsearch|rag|hnsw|ivf|nosql|cap theorem|schema|embedd\w+|capstone|"
                       r"tasktracker|billing|oplog|election|primary|secondary|sharding|polyglot|circuit breaker|ttl|collections?|documents?|"
                       r"atlas|compass|pipeline|\$\w+|bson|wiredtiger|multi-?tenan\w+|jwt|llm|slm|ollama|kavach)\b", re.I)
STATS = re.compile(r"\b(how many|count|number of|statistics|stats|summary|summari[sz]e|overview|breakdown|dashboard|totals?)\b", re.I)
GREET = re.compile(r"^\s*(hi|hello|hey|hii+|good (morning|afternoon|evening)|namaste|yo)\b[\s!.,]*$", re.I)
THANKS = re.compile(r"^\s*(thanks?|thank you|thx|great|nice|cool|awesome|ok(ay)? thanks?)\b[\s!.,]*$", re.I)
HELP = re.compile(r"\b(what can you do|help|who are you|what are you|how do (i|you) use|capabilit\w+|features)\b", re.I)
SCAN_NOW = re.compile(r"\b(scan now|run (a |the )?scan|re-?scan|start (a |the )?scan|scan (my|the|this|our) (project|system|code|machine|app|folder|dependenc\w+)|scan again|scan it)\b", re.I)
FINDINGS = re.compile(r"\b(findings?|scan results?|scanner (status|report)|bot status|what did the (bot|scanner) find|(show|list) (the )?(issues|problems)|"
                      r"(vulnerab\w+|issues?|problems?) (in|of|with) (my|the|this|our) (project|system|code|app|application|dependenc\w+|machine)|"
                      r"is (my|the|this|our) (project|system|code|app) (safe|secure|vulnerable))\b", re.I)
FIX = re.compile(r"^\s*(please |can you |could you |kindly |now |then |ok |okay )*(fix|patch|remediate|repair|upgrade|update|apply fix(es)? (for|to)?|resolve)\b", re.I)
UNDO = re.compile(r"\b(undo|roll ?back|revert)\b", re.I)
RESET = re.compile(r"\breset\b.*\bdemo\b|\brestore\b.*\bdemo\b", re.I)
IGNORE = re.compile(r"^\s*(ignore|dismiss|skip)\b", re.I)
FOLLOW = re.compile(r"\b(it|its|they|them|their|this|that|these|those|ones?|same|above|more)\b|^(and|also|what about|how about|ok(ay)?|then|so)\b", re.I)
STATUS_PLAYBOOK = {"Active": "What to do about an actively exploited vulnerability", "Evolving": "What to do about an evolving threat",
                   "Anticipated": "What to do about an anticipated threat", "Resolved": "What to do about a resolved threat"}
SUGGESTIONS = ["What are the active threats for Windows?", "Show evolving threats on Linux and how to mitigate them", "Which Android threats are anticipated?",
               "Give me a summary of all platforms", "What did the scanner bot find?", "Explain what Lab 9 proved about write concern"]


# ---------------------------------------------------------------------------- conversations (A.4)
def conversation(user: dict, conversation_id: str | None, first_text: str = "") -> dict:
    d = store.adb()
    if conversation_id:
        c = d.conversations.find_one({"_id": conversation_id, "user_id": user["_id"]})
        if c:
            return c
    title = re.sub(r"\s+", " ", first_text).strip()[:60] or "New conversation"
    c = {"_id": conversation_id if conversation_id and re.fullmatch(r"[A-Za-z0-9_\-]{6,64}", conversation_id) else "c_" + secrets.token_hex(8),
         "user_id": user["_id"], "title": title, "created_at": store.now(), "updated_at": store.now(), "messages": 0, "ctx": {}, "pending": None}
    if d.conversations.find_one({"_id": c["_id"]}):           # an id that belongs to someone else: never reuse it
        c["_id"] = "c_" + secrets.token_hex(8)
    d.conversations.insert_one(c)
    return c


def history(conversation_id: str, exchanges: int | None = None) -> list[dict]:
    """The most recent exchanges, oldest first - the multi-turn context given to the model (get_history of the assignment)."""
    n = (exchanges or S.HISTORY_EXCHANGES) * 2
    msgs = list(store.adb().messages.find({"conversation_id": conversation_id}, {"role": 1, "content": 1}).sort("ts", -1).limit(n))[::-1]
    return [{"role": m["role"], "content": m["content"]} for m in msgs]


def _save(conv: dict, user: dict, role: str, content: str, meta: dict | None = None) -> dict:
    doc = {"_id": "m_" + secrets.token_hex(8), "conversation_id": conv["_id"], "session_id": conv["_id"], "user_id": user["_id"], "role": role,
           "content": content, "ts": store.now(), "meta": meta or {}}
    store.adb().messages.insert_one(doc)
    return doc


# ---------------------------------------------------------------------------- understanding the question
def detect_platform(text: str) -> str | None:
    for p, rx in _PLAT:
        if rx.search(text):
            return p
    return None


def detect_statuses(text: str) -> list[str]:
    t = re.sub(r"\bactive directory\b", " ", text, flags=re.I)
    return [s for s, rx in _STAT if rx.search(t)]


def understand(text: str, ctx: dict, ui_platform: str | None) -> dict:
    t = text.strip()
    low = t.lower()
    u = {"text": t, "cves": [c.upper() for c in CVE_RX.findall(t)], "ips": IP_RX.findall(t), "hashes": HASH_RX.findall(t),
         "platform": detect_platform(t), "statuses": detect_statuses(t), "mitigate": bool(MITIGATE.search(t)), "ordinals": [], "numbers": []}
    all_platforms = bool(re.search(r"\b(all|every|each|any|across) (the )?(platforms?|systems?|os|operating systems?)\b|\boverall\b", low))
    for w, i in ORDINAL.items():
        if re.search(rf"\b{w}\b", low):
            u["ordinals"].append(i)
    u["numbers"] = [int(n) for n in re.findall(r"(?:#|number |no\.? ?|finding |item )?\b(\d{1,2})\b(?!\.\d)", low) if 0 < int(n) <= 60]
    follow = bool(FOLLOW.search(low)) or len(low.split()) <= 6
    # ---- intent
    if RESET.search(low):
        u["intent"] = "scan_reset"
    elif UNDO.search(low) and re.search(r"\b(fix\w*|change\w*|last|patch\w*|upgrade\w*|that|it)\b", low):
        u["intent"] = "scan_undo"
    elif SCAN_NOW.search(low):
        u["intent"] = "scan_now"
    elif FIX.search(low) and not u["cves"] and (ctx.get("last_list") == "findings" or re.search(r"\b(all|findings?|issues?|everything|them|it|critical|high|medium|low|\d+)\b", low)) \
            and not THREAT_WORDS.search(re.sub(r"\bvulnerab\w+\b", "", low)) and not u["platform"]:
        u["intent"] = "scan_fix"
    elif IGNORE.search(low) and ctx.get("last_list") == "findings":
        u["intent"] = "scan_ignore"
    elif FINDINGS.search(low) or (re.search(r"\b(scanner|bot)\b", low) and re.search(r"\b(f(ind|ound)\w*|report\w*|results?|status|detect\w*|issues?|show|list|say|said)\b", low)) or (ctx.get("last_list") == "findings" and (u["ordinals"] or re.search(r"\b(explain|detail|more about|manual\w*|why)\b", low))
                                  and not THREAT_WORDS.search(low)):
        u["intent"] = "scan_findings"
    elif u["cves"]:
        u["intent"] = "cve"
    elif (u["ips"] or u["hashes"]) and not LAB_WORDS.search(low):
        u["intent"] = "ioc"
    elif GREET.match(low) or THANKS.match(low):
        u["intent"] = "smalltalk"
    elif HELP.search(low) and len(low.split()) <= 9:
        u["intent"] = "help"
    elif ctx.get("last_list") == "threats" and u["ordinals"] and len(low.split()) <= 14:
        u["intent"] = "threat_detail"
    elif re.search(r"\blabs?\s*\d+\b|\bexercises?\s*\d+\b", low) or (LAB_WORDS.search(low) and not THREAT_WORDS.search(low) and not u["platform"] and not u["statuses"]):
        u["intent"] = "lab"
        m = re.search(r"\b(?:lab|exercise)s?\s*(\d+)", low)
        u["lab"] = int(m.group(1)) if m else None
    elif STATS.search(low) and (THREAT_WORDS.search(low) or u["platform"] or u["statuses"] or all_platforms or re.search(r"\bplatforms?\b", low)):
        u["intent"] = "stats"
    elif u["platform"] or u["statuses"] or THREAT_WORDS.search(low):
        u["intent"] = "threats"
    elif follow and ctx.get("intent") in ("threats", "stats", "threat_detail", "cve") and (u["mitigate"] or re.search(r"\b(more|others?|else|next|details?|why)\b", low)):
        u["intent"] = "threats"
    elif u["mitigate"] and ctx.get("intent") in ("threats", "stats", "threat_detail", "cve"):
        u["intent"] = "threats"
    else:
        u["intent"] = "general"
    # ---- context carry-over for follow-up questions ("what about Windows?", "and the resolved ones?")
    if u["intent"] in ("threats", "stats", "threat_detail"):
        if all_platforms:
            u["platform"] = None
        elif not u["platform"]:
            u["platform"] = ui_platform or (ctx.get("platform") if follow else None)
        if not u["statuses"] and follow and ctx.get("statuses") and u["intent"] == "threats" and not re.search(r"\ball\b", low):
            u["statuses"] = ctx["statuses"]
    return u


# ---------------------------------------------------------------------------- formatting helpers
def _d(v) -> str:
    return v.strftime("%d %b %Y") if hasattr(v, "strftime") else ""


def card(t: dict) -> dict:
    """What the browser needs to draw one threat."""
    return {"id": t["_id"].replace("report:", ""), "ref": t["_id"], "kind": t.get("kind"), "title": t.get("title"), "status": t.get("status"),
            "reason": t.get("status_reason"), "severity": t.get("severity"), "platforms": t.get("platforms"), "cvss": t.get("cvss"),
            "epss": t.get("epss"), "vendor": t.get("vendor"), "product": t.get("product"), "ransomware": t.get("ransomware"),
            "due_date": t.get("due_date"), "sightings": (t.get("sightings") or {}).get("total"), "url": t.get("url") or ((t.get("references") or [None])[-1]),
            "published": t.get("published"), "description": (t.get("description") or "")[:320]}


def _line(i: int, t: dict) -> str:
    ident = t["_id"] if t.get("kind") == "vulnerability" else "Report"
    extra = []
    if t.get("cvss"):
        extra.append(f"CVSS {t['cvss']}")
    if t.get("ransomware"):
        extra.append("used by ransomware")
    tail = f" ({', '.join(extra)})" if extra else ""
    return f"{i}. **{ident} - {t.get('title', '')}** - {t.get('severity', '')}, {t.get('status')}: {t.get('status_reason', '')}{tail}"


def _fact(i: int, t: dict) -> str:
    ident = t["_id"] if t.get("kind") == "vulnerability" else f"report \"{t.get('title', '')[:80]}\""
    bits = [f"{i}. {ident}", t.get("title", "")[:110] if t.get("kind") == "vulnerability" else "", f"platform {'/'.join(t.get('platforms') or [])}",
            f"severity {t.get('severity')}", f"status {t.get('status')} because {t.get('status_reason')}"]
    if t.get("vendor") or t.get("product"):
        bits.append(f"affects {t.get('vendor', '')} {t.get('product', '')}".strip())
    if t.get("due_date"):
        bits.append(f"patch deadline {_d(t['due_date'])}")
    if t.get("ransomware"):
        bits.append("known ransomware use")
    if t.get("description"):
        bits.append(f"summary: {t['description'][:230]}")
    return " | ".join(b for b in bits if b)


def _passages(hits: list[dict], limit: int = 3, chars: int = 750) -> tuple[str, list[dict]]:
    used, lines = [], []
    for h in hits[:limit]:
        used.append({"n": len(used) + 1, "title": h.get("title"), "kind": h.get("kind"), "source": h.get("source"), "url": h.get("url"),
                     "score": round(float(h.get("score", 0)), 3)})
        lines.append(f"[{len(used)}] ({h.get('title', '')}) {h.get('text', '')[:chars]}")
    return "\n".join(lines), used


def _relevant(hits: list[dict], question: str = "") -> list[dict]:
    """Hits that really are about the question: a high vector score, or at least two of the question's words in the chunk."""
    floor = 0.60 if ai.embedder().name == "hashing" else 0.66
    q = set(ai._tokens(question))
    out = []
    for h in hits:
        if float(h.get("score", 0)) >= floor and not h.get("lexical"):
            out.append(h)
            continue
        if not q:
            continue
        in_title = q & set(ai._tokens(h.get("title", "")))
        matched = q & set(ai._tokens(f"{h.get('title', '')} {h.get('text', '')}"))
        if (len(matched) >= 2 and len(matched) / len(q) >= 0.6) or (in_title and len(in_title) / len(q) >= 0.5):
            out.append(h)
    return out


def _in_title(hits: list[dict], terms: list[str]) -> list[dict]:
    """Playbook sections whose HEADING names the topic (ransomware -> 'Ransomware response procedure')."""
    t = set(terms)
    return [h for h in hits if t & set(ai._tokens(h.get("title", "")))]


_GENERIC = set(ai._tokens(
    "threat threats vulnerability vulnerabilities active actively evolving anticipated resolved show list give tell mitigate mitigation mitigating fix fixing "
    "protect protection current currently latest new newest top risk risks attack attacks exploit exploited exploits platform platforms system systems device devices "
    "phone phones server servers user users detail details step steps procedure procedures need know security secure safe affect affected affecting issue issues "
    "problem problems patch patches patching update updates updating windows linux macos mac android ios iphone ipad network cloud web cve cves kavach help please "
    "today now right week month status summary most important urgent critical high medium low severity prevent defend harden respond handle deal stop get want like "
    "make see find look check there more about against from wild being known recent recently happening face facing worry worried dangerous danger biggest worst "
    "main major kind kinds type types tell explain describe emerging upcoming expected potential future ongoing rising trending threat-intelligence thing things "
    "ones one first second third them they these those doing done going come coming year years day days time have has had use using used run running"))


def topic_terms(text: str) -> list[str]:
    """The words of a question that name a TOPIC (ransomware, vpn, chrome ...), without platform / status / filler words."""
    return [w for w in dict.fromkeys(ai._tokens(text)) if w not in _GENERIC and not w.isdigit() and len(w) >= 3]


SYSTEM = ("You are Kavach Assist, the assistant of the Kavach cyber threat intelligence platform (a MongoDB course project). "
          "Rules: 1) Use the FACTS and PASSAGES below as your source of truth; never invent CVE ids, dates, version numbers, counts or commands. "
          "2) When threats are listed, say for each one its status (Active, Evolving, Anticipated or Resolved) and why, then give precise, "
          "numbered mitigation steps for the user's platform, with the exact commands that appear in the passages. "
          "3) Cite passages as [1], [2]. 4) If the facts and passages do not answer the question but you know the answer from general knowledge, "
          "answer briefly and start with 'General knowledge (not from Kavach data):'. If you do not know, say so. "
          "5) Be concise: at most 230 words, short markdown, no preamble.")


# ---------------------------------------------------------------------------- intent handlers -> a "plan"
def plan_threats(u: dict) -> dict:
    platform, statuses = u["platform"], u["statuses"]
    b = threats.board(platform)
    items: list = []
    terms = topic_terms(u["text"])
    note = ""
    if terms:                                           # a topic was named: "ransomware", "vpn", "chrome", "fortinet" ...
        items = threats.search(" ".join(terms), platform, statuses or None, limit=6)
        if not items:
            note = f"No catalog entry mentions \"{' '.join(terms)}\"" + (f" for {platform}" if platform else "") + "; these are the most important threats instead."
    if items:
        pass
    elif len(statuses) == 1:
        items = threats.find(platform, statuses[0], limit=6)
    elif statuses:
        for s in statuses:
            items += threats.find(platform, s, limit=3)
    else:                                               # no status named: the most important of each open status
        for s, n in (("Active", 3), ("Evolving", 2), ("Anticipated", 2)):
            items += threats.find(platform, s, limit=n)
    topic = re.sub(r"\s+", " ", u["text"])
    hits = knowledge.retrieve(f"{topic} {platform or ''} mitigation: patch, update, harden", kinds=["playbook"], platform=platform, k=2, strict_platform=bool(platform))
    pb = hits["hits"][:1 if statuses or not platform else 2]
    if terms:                                           # the procedure for the topic itself (e.g. "Ransomware response procedure")
        th = knowledge.retrieve(f"{' '.join(terms)} response procedure", kinds=["playbook"], platform=platform, k=2)["hits"]
        pb = _in_title(th, terms)[:1] + pb
    for s in (statuses or sorted({t["status"] for t in items} & {"Active", "Evolving"}) or ["Active"])[:2]:   # the fixed procedure for that status
        h = knowledge.by_title(STATUS_PLAYBOOK[s])
        if h:
            pb.append(h)
    extra = knowledge.retrieve(topic, kinds=["threat", "cti"], platform=platform, status=statuses[0] if len(statuses) == 1 else None, k=3)
    scope = platform or "all platforms"
    counts = b["by_status"]
    head = f"**{scope} - {', '.join(statuses) if statuses else 'threat picture'}**: " + ", ".join(f"{counts[s]['total']:,} {s}" for s in S.STATUSES)
    lines = [head, ""]
    if items:
        lines += [_line(i, t) for i, t in enumerate(items, 1)]
    else:
        lines.append(f"The catalog has no {' / '.join(statuses) if statuses else ''} threats for {scope} right now.")
    if note:
        lines.insert(1, f"_{note}_")
    if pb:
        lines += ["", "**What to do**"]
        for h in pb:
            body = h["text"].split(". ", 1)[1] if ". " in h["text"] else h["text"]
            lines.append(f"- *{h['title']}*: {body}")
    if items:
        lines += ["", "Ask \"tell me more about the first one\" or \"how do I fix the second one\" for details."]
    passages, used = _passages(pb + _relevant(extra["hits"], topic)[:2])
    facts = (f"Platform: {scope}. Statuses asked: {', '.join(statuses) or 'all'}. Catalog counts for {scope}: "
             + ", ".join(f"{s} {counts[s]['total']}" for s in S.STATUSES) + ".\nThreats (most important first):\n" + "\n".join(_fact(i, t) for i, t in enumerate(items, 1)))
    return {"intent": "threats", "builtin": "\n".join(lines), "facts": facts, "passages": passages, "sources": used, "cards": [card(t) for t in items],
            "board": b, "use_llm": bool(items), "retrieval": hits["mode"],
            "ctx": {"platform": platform, "statuses": statuses, "last_ids": [t["_id"] for t in items], "last_list": "threats"}}


def plan_stats(u: dict) -> dict:
    b = threats.board(u["platform"])
    scope = u["platform"] or "all platforms"
    c = b["by_status"]
    lines = [f"**Threat summary for {scope}** (from the Kavach catalog in MongoDB)", ""]
    for s in S.STATUSES:
        lines.append(f"- **{s}**: {c[s]['total']:,} ({c[s]['vulnerability']:,} vulnerabilit{'y' if c[s]['vulnerability'] == 1 else 'ies'}, "
                     f"{c[s]['report']:,} threat report{'' if c[s]['report'] == 1 else 's'})")
    if not u["platform"]:
        lines += ["", "| Platform | Active | Evolving | Anticipated | Resolved |", "|---|---|---|---|---|"]
        for p in S.PLATFORMS:
            r = b["by_platform"][p]
            if sum(r.values()):
                lines.append(f"| {p} | {r['Active']:,} | {r['Evolving']:,} | {r['Anticipated']:,} | {r['Resolved']:,} |")
    top = threats.find(u["platform"], "Active", limit=3)
    if top:
        lines += ["", "Most urgent right now:"] + [_line(i, t) for i, t in enumerate(top, 1)]
    return {"intent": "stats", "builtin": "\n".join(lines), "cards": [card(t) for t in top], "board": b, "use_llm": False, "sources": [],
            "ctx": {"platform": u["platform"], "statuses": [], "last_ids": [t["_id"] for t in top], "last_list": "threats"}}


def _dossier(t: dict, platform_hint: str | None) -> tuple[str, list[dict], str, str]:
    plat = platform_hint if platform_hint in (t.get("platforms") or []) else (t.get("platforms") or [None])[0]
    kind = "vulnerability" if t.get("kind") == "vulnerability" else "threat report"
    lines = [f"**{t['_id'].replace('report:', 'Report ')} - {t.get('title', '')}**", "",
             f"- **Status**: {t.get('status')} - {t.get('status_reason')}", f"- **Severity**: {t.get('severity')}" + (f" (CVSS {t['cvss']})" if t.get("cvss") else "")
             + (f", EPSS {t['epss']:.1%}" if t.get("epss") else ""), f"- **Platforms**: {', '.join(t.get('platforms') or [])}"]
    if t.get("vendor") or t.get("product"):
        lines.append(f"- **Affects**: {t.get('vendor', '')} {t.get('product', '')}".rstrip())
    if t.get("kev_added"):
        lines.append(f"- **Listed as exploited**: {_d(t['kev_added'])}" + (f"; patch deadline {_d(t['due_date'])}" if t.get("due_date") else ""))
    sg = t.get("sightings") or {}
    if sg.get("total"):
        lines.append(f"- **Sightings (30 days)**: {sg['total']} total, {sg.get('exploited', 0)} exploitation, {sg.get('poc', 0)} proof-of-concept; last 7 days {sg.get('last7', 0)} vs {sg.get('prev7', 0)} the week before")
    if t.get("ransomware"):
        lines.append("- **Ransomware**: known to be used in ransomware campaigns")
    if t.get("description"):
        lines += ["", t["description"][:600]]
    q = f"{t.get('title', '')} {t.get('vendor', '')} {t.get('product', '')} {plat or ''} mitigation patch"
    q += " " + (t.get("description") or "")[:200]
    hits = knowledge.retrieve(q, kinds=["playbook"], platform=plat if plat not in (None, "Other") else None, k=3, strict_platform=plat not in (None, "Other"))["hits"]
    status_hit = knowledge.by_title(STATUS_PLAYBOOK.get(t.get("status"), ""))
    pb = hits[:2] + ([status_hit] if status_hit else [])
    if pb:
        lines += ["", "**Mitigation procedure**"]
        for h in pb:
            body = h["text"].split(". ", 1)[1] if ". " in h["text"] else h["text"]
            lines.append(f"- *{h['title']}*: {body}")
    refs = [r for r in (t.get("references") or []) if r][:3]
    if refs:
        lines += ["", "References: " + " , ".join(refs)]
    passages, used = _passages(pb)
    return "\n".join(lines), used, _fact(1, t) + f"\nThis is a {kind}.", passages


def plan_cve(u: dict, ctx: dict) -> dict:
    found, missing = [], []
    for c in u["cves"][:3]:
        t = threats.get(c)
        (found if t else missing).append(t or c)
    if not found:
        txt = (f"**{', '.join(missing)}** is not in the Kavach catalog. Kavach tracks vulnerabilities that are in a known-exploited catalog, have recent "
               f"sightings or are named in threat reports. It may be new, or not exploited. You can open the full dossier with the search box at the top "
               f"(it queries the GCVE database live).")
        return {"intent": "cve", "builtin": txt, "use_llm": False, "sources": [], "cards": [], "ctx": {**ctx, "intent": "cve"}}
    parts, used_all, facts, passages = [], [], [], ""
    for t in found:
        text, used, fact, passages = _dossier(t, u["platform"] or ctx.get("platform"))
        parts.append(text)
        used_all = used
        facts.append(fact)
    if missing:
        parts.append(f"Not in the catalog: {', '.join(missing)}.")
    return {"intent": "cve", "builtin": "\n\n---\n\n".join(parts), "facts": "\n".join(facts), "passages": passages, "sources": used_all,
            "cards": [card(t) for t in found], "use_llm": True,
            "ctx": {"platform": u["platform"] or ctx.get("platform"), "statuses": [], "last_ids": [t["_id"] for t in found], "last_list": "threats"}}


def plan_threat_detail(u: dict, ctx: dict) -> dict:
    ids = ctx.get("last_ids") or []
    idx = u["ordinals"][0]
    if not ids or (idx >= len(ids) and idx != -1):
        return {"intent": "threat_detail", "builtin": "I do not have a list to pick from yet. Ask for threats first, for example \"active threats for Linux\".",
                "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
    t = threats.get(ids[idx])
    if not t:
        return {"intent": "threat_detail", "builtin": "That threat is no longer in the catalog.", "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
    text, used, fact, passages = _dossier(t, ctx.get("platform"))
    return {"intent": "threat_detail", "builtin": text, "facts": fact, "passages": passages, "sources": used, "cards": [card(t)], "use_llm": True,
            "ctx": {**ctx, "last_list": "threats"}}


def plan_ioc(u: dict) -> dict:
    db = store.cti()
    lines = []
    for value in (u["ips"] + u["hashes"])[:3]:
        ind = db.indicators.find_one({"value": value}) or db.indicators.find_one({"_id": {"$in": [f"ipv4:{value}", f"sha256:{value}", f"md5:{value}", f"sha1:{value}"]}})
        if not ind:
            lines.append(f"**{value}** is not in Kavach's indicator database (no feed lists it). That does not prove it is safe - it only means none of "
                         f"the collected blocklists and reports mention it. Use the IP intelligence page for a live reputation check.")
            continue
        n = db.sightings.count_documents({"indicator_id": ind["_id"]})
        srcs = db.sightings.distinct("source_id", {"indicator_id": ind["_id"]})
        fp = db.fp_verdicts.find_one({"_id": value})
        lines.append(f"**{value}** is a known malicious indicator ({ind.get('type')}): listed by {len(srcs)} source(s) ({', '.join(map(str, srcs[:5]))}), "
                     f"{n} sighting(s), first seen {_d(ind.get('first_seen'))}, last seen {_d(ind.get('last_seen'))}"
                     + (f", tags: {', '.join(ind.get('tags') or [])}" if ind.get("tags") else "") + "."
                     + (f" Note: the Lab 18 false-positive detector flagged it as **{fp['verdict']}** ({fp.get('reason')})." if fp else "")
                     + " Recommended: block it at the firewall / proxy, search your logs for connections to it, and isolate any machine that contacted it.")
    return {"intent": "ioc", "builtin": "\n\n".join(lines), "use_llm": False, "sources": [], "cards": [], "ctx": {"intent": "ioc"}}


def plan_knowledge(u: dict, kinds: list[str] | None, intent: str) -> dict:
    q = u["text"]
    r = knowledge.retrieve(q, kinds=kinds, k=6)
    hits = r["hits"]
    if u.get("lab"):                                    # "what did lab 13 prove": its guide entry + latest results first
        own = knowledge.of_lab(u["lab"])
        seen = {h["_id"] for h in own}
        hits = own + [h for h in hits if h["_id"] not in seen and h.get("lab") in (u["lab"], None)][:2]
        good = own + _relevant(hits[len(own):], q)[:1] if own else _relevant(hits, q)
    else:
        good = sorted(_relevant(hits, q), key=lambda h: not str(h["_id"]).startswith("kb:"))     # the curated guide / FAQ entries first
    passages, used = _passages(good or hits[:2], limit=4)
    if good:
        top = good[0]
        body = top["text"].split(". ", 1)[1] if ". " in top["text"][:200] else top["text"]
        lines = [f"**{top['title']}**", "", body]
        for h in good[1:3]:
            b2 = h["text"].split(". ", 1)[1] if ". " in h["text"][:200] else h["text"]
            lines += ["", f"**{h['title']}**", "", b2[:520] + ("..." if len(b2) > 520 else "")]
        builtin = "\n".join(lines)
    else:
        builtin = ("I could not find this in Kavach's knowledge base (threat data, mitigation playbooks and the course labs). "
                   "The local language model answers general questions like this one - it is not running right now, so I can only answer from stored data. "
                   "Try asking about threats for a platform, a CVE id, the scanner bot, or one of the labs.")
    return {"intent": intent, "builtin": builtin, "facts": "", "passages": passages if good else "", "sources": used if good else [], "cards": [],
            "use_llm": True, "retrieval": r["mode"], "grounded": bool(good), "ctx": {"intent": intent}}


def plan_labs_overview() -> dict:
    """"Did all labs pass?" - the live status of the lab chain plus the headline of every lab's latest results."""
    from .. import chain
    rows = {r["lab"]: r["title"] for r in knowledge.lab_results()}
    try:
        snap = {d["lab"]: d for d in chain.snapshot(store.cti(), tail=0)["labs"]}
    except Exception:                                  # noqa: BLE001
        snap = {}
    word = {"ok": "Active (passed)", "failed": "FAILED", "running": "running now", "pending": "waiting", "skipped": "skipped", "idle": "not started"}
    lines = ["**Labs 7 to 18 - latest status** (lab control centre + the newest result files)", ""]
    for n in range(7, 19):
        st = word.get((snap.get(n) or {}).get("status", "idle"), (snap.get(n) or {}).get("status", ""))
        res = rows.get(n, "").replace(f"Lab {n} latest results: ", "")
        status = (snap.get(n) or {}).get("status", "idle")
        lines.append(f"- **Lab {n} - {chain.SHORT.get(n, '')}**: " + (res if status == "idle" and res else st + (f"; {res}" if res else "")))
    bad = [n for n in range(7, 19) if (snap.get(n) or {}).get("status") == "failed" or "FAILED" in rows.get(n, "")]
    lines += ["", "All labs that ran are passing." if not bad else f"Needs attention: {', '.join(f'Lab {n}' for n in bad)}.",
              "Ask \"what did Lab 13 prove\" for the details of one lab."]
    return {"intent": "lab", "builtin": "\n".join(lines), "use_llm": False, "sources": [], "cards": [], "ctx": {"intent": "lab"}}


def plan_smalltalk(u: dict, user: dict) -> dict:
    if THANKS.match(u["text"].lower()):
        txt = "You're welcome. Ask me anything else about threats, a CVE, the scanner bot or the labs."
    else:
        txt = (f"Hello {user.get('name', '').split(' ')[0] or 'there'}. I am Kavach Assist. Pick a platform above or just ask, for example:\n"
               + "\n".join(f"- {s}" for s in SUGGESTIONS[:4]))
    return {"intent": "smalltalk", "builtin": txt, "use_llm": False, "sources": [], "cards": [], "ctx": None}


def plan_help() -> dict:
    txt = ("I am **Kavach Assist**, a chat assistant on top of the Kavach threat intelligence database (MongoDB).\n\n"
           "- **Threats by platform and status** - \"active threats for Windows\", \"what is evolving on Android\", \"anticipated iOS threats\", \"resolved Linux threats\"\n"
           "- **Mitigation procedures** - \"how do I protect Linux servers from the first one\", \"what should I do about ransomware on Windows\"\n"
           "- **Look-ups** - a CVE id such as CVE-2024-3400, or an IP address / file hash\n"
           "- **Summaries** - \"give me a summary of all platforms\"\n"
           "- **Scanner bot** - \"what did the scanner find\", \"scan now\", \"fix all\" (I always ask for confirmation), \"undo last fix\"\n"
           "- **The course labs** - \"what did Lab 13 prove\", \"explain write concern\", \"HNSW vs IVF\"\n\n"
           "Facts come from MongoDB; a local language model writes the explanation.")
    return {"intent": "help", "builtin": txt, "use_llm": False, "sources": [], "cards": [], "ctx": None}


# ---------------------------------------------------------------------------- scanner conversation
def bot_state() -> dict:
    st = store.adb().scanner_state.find_one({"_id": "bot"}) or {}
    beat = st.get("last_beat")
    online = bool(beat and (store.now() - beat).total_seconds() < 45)
    return {"online": online, "last_beat": beat, "last_scan": st.get("last_scan"), "target": st.get("target"), "interval_s": st.get("interval_s"),
            "open": st.get("open"), "host": st.get("host")}


def _open_findings(limit: int = 40) -> list[dict]:
    return list(store.adb().scan_findings.find({"status": {"$in": ["open", "failed"]}}).sort([("severity_rank", -1), ("scope", 1), ("_id", 1)]).limit(limit))


def _ago(dt) -> str:
    if not dt:
        return "never"
    s = (store.now() - dt).total_seconds()
    return "just now" if s < 60 else f"{int(s // 60)} min ago" if s < 3600 else f"{int(s // 3600)} h ago"


OFFLINE = ("The scanner bot is not running, so I cannot scan or fix anything right now. Start it with `docker compose up -d scanner-bot` and ask again.")


def _finding_line(i: int, f: dict) -> str:
    how = f"fix: {f['fix_text']}" if f.get("fixable") and f.get("fix_text") else "manual fix needed"
    hot = " **exploited in the wild (Kavach)**" if f.get("exploited") else ""
    return f"{i}. **[{f['severity']}]** {f['title']}{hot} - {how}"


def plan_findings(u: dict, ctx: dict) -> dict:
    st = bot_state()
    fs = _open_findings()
    if ctx.get("last_list") == "findings" and (u["ordinals"] or (u["numbers"] and re.search(r"\b(explain|detail|more|manual\w*|why|how)\b", u["text"].lower()))):
        ids = ctx.get("last_ids") or []
        idx = u["ordinals"][0] if u["ordinals"] else u["numbers"][0] - 1
        f = store.adb().scan_findings.find_one({"_id": ids[idx]}) if ids and -1 <= idx < len(ids) else None
        if f:
            lines = [f"**{f['title']}** ({f['severity']}, {f['scope']} scope, {f.get('file')})", "", f.get("detail") or ""]
            if f.get("cves"):
                lines.append(f"Vulnerabilities: {', '.join(f['cves'][:8])}" + (f" - exploited in the wild according to Kavach: {', '.join(f['exploited'])}" if f.get("exploited") else ""))
            lines.append(f"Automatic fix: {f['fix_text']}. Say **fix {idx + 1}** and I will ask you to confirm." if f.get("fixable") else f"Manual fix: {f.get('manual')}")
            return {"intent": "scan_findings", "builtin": "\n".join(lines), "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
    if not st["last_scan"]:
        txt = OFFLINE if not st["online"] else "The scanner bot is running but has not finished its first scan yet. Ask again in a minute."
        return {"intent": "scan_findings", "builtin": txt, "use_llm": False, "sources": [], "cards": [], "ctx": {"intent": "scan_findings"}}
    head = (f"The scanner bot ({'online' if st['online'] else 'OFFLINE'}) last scanned `{st.get('target')}` and its Linux system {_ago(st['last_scan'])}: "
            f"**{len(fs)} open issue(s)**")
    if not fs:
        return {"intent": "scan_findings", "builtin": head + ". Nothing needs fixing.", "use_llm": False, "sources": [], "cards": [],
                "findings": [], "ctx": {"last_list": "findings", "last_ids": [], "intent": "scan_findings"}}
    by = {}
    for f in fs:
        by[f["severity"]] = by.get(f["severity"], 0) + 1
    head += " (" + ", ".join(f"{by[s]} {s}" for s in ("Critical", "High", "Medium", "Low") if by.get(s)) + ")."
    lines = [head, ""] + [_finding_line(i, f) for i, f in enumerate(fs[:20], 1)]
    if len(fs) > 20:
        lines.append(f"... and {len(fs) - 20} more.")
    fixable = sum(1 for f in fs if f.get("fixable"))
    lines += ["", f"{fixable} can be fixed automatically. Say **fix all**, **fix 1 and 3** or **fix the critical ones** - I will show the exact changes and ask "
                  f"for your confirmation before anything is touched. \"explain the second one\" gives details."]
    return {"intent": "scan_findings", "builtin": "\n".join(lines), "use_llm": False, "sources": [], "cards": [], "findings": [_fpub(f) for f in fs[:20]],
            "ctx": {"last_list": "findings", "last_ids": [f["_id"] for f in fs[:40]], "intent": "scan_findings"}}


def _fpub(f: dict) -> dict:
    return {k: f.get(k) for k in ("_id", "title", "severity", "scope", "kind", "file", "detail", "fixable", "fix_text", "manual", "status", "cves", "exploited")}


def _select(u: dict, ctx: dict, fs: list[dict]) -> list[dict]:
    low = u["text"].lower()
    ids = ctx.get("last_ids") if ctx.get("last_list") == "findings" else [f["_id"] for f in fs]
    by_id = {f["_id"]: f for f in fs}
    picked = []
    for n in u["numbers"]:
        if ids and n - 1 < len(ids) and ids[n - 1] in by_id:
            picked.append(by_id[ids[n - 1]])
    for i in u["ordinals"]:
        if ids and -1 <= i < len(ids) and ids[i] in by_id and by_id[ids[i]] not in picked:
            picked.append(by_id[ids[i]])
    if picked:
        return picked
    sev = [s for s in ("Critical", "High", "Medium", "Low") if re.search(rf"\b{s.lower()}\b", low)]
    if sev:
        return [f for f in fs if f["severity"] in sev]
    words = set(re.findall(r"[a-z0-9_.\-]{3,}", low)) - {"fix", "the", "all", "please", "issue", "issues", "finding", "findings", "problem", "package",
                                                           "update", "upgrade", "patch", "and", "for", "with", "them", "everything", "ones", "one", "now"}
    named = [f for f in fs if (f.get("package") or "").lower() in words or (f.get("file") or "").lower() in words or f.get("kind") in words
             or (f.get("kind") == "dependency" and words & {"dependencies", "dependency", "packages", "libraries"})
             or (f.get("kind") == "os-package" and words & {"system", "os", "operating"})
             or (f.get("scope") == "project" and "project" in words)]
    if named:
        return named
    if re.search(r"\b(all|everything|every|them|these|those|it)\b", low) or low.strip() in ("fix", "fix."):
        return fs
    return []


def plan_fix(u: dict, ctx: dict, conv: dict) -> dict:
    st = bot_state()
    if not st["online"]:
        return {"intent": "scan_fix", "builtin": OFFLINE, "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
    fs = _open_findings()
    chosen = _select(u, ctx, fs)
    if not chosen:
        txt = ("Tell me what to fix: **fix all**, **fix 1 and 3**, **fix the high ones** or **fix requests**. Say \"show findings\" to see the numbered list."
               if fs else "There is nothing open to fix. Say \"scan now\" to check again.")
        return {"intent": "scan_fix", "builtin": txt, "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
    auto = [f for f in chosen if f.get("fixable")]
    manual = [f for f in chosen if not f.get("fixable")]
    lines = []
    if auto:
        lines += [f"I am ready to make **{len(auto)} change(s)** on the scanned system. **Nothing has been changed yet.**", ""]
        lines += [f"{i}. {f['fix_text']}  \n   _{f['title']}_" + (f" - fixes {', '.join(f['cves'][:3])}" if f.get("cves") else "") for i, f in enumerate(auto, 1)]
        lines += ["", "A backup of every changed file is kept, so **undo last fix** restores it (operating-system package upgrades cannot be undone).",
                  "", "Reply **yes** to apply these changes, or **no** to cancel."]
    if manual:
        lines += ["", f"{len(manual)} selected issue(s) need a manual fix, I will not touch them:"] + [f"- {f['title']}: {f.get('manual')}" for f in manual[:6]]
    if not auto:
        return {"intent": "scan_fix", "builtin": "\n".join(lines).strip(), "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
    pending = {"type": "fix", "finding_ids": [f["_id"] for f in auto], "created_at": store.now(), "summary": f"{len(auto)} fix(es)"}
    return {"intent": "scan_fix", "builtin": "\n".join(lines), "use_llm": False, "sources": [], "cards": [], "ctx": ctx, "pending": pending, "confirm": True}


def plan_undo(ctx: dict) -> dict:
    if not bot_state()["online"]:
        return {"intent": "scan_undo", "builtin": OFFLINE, "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
    job = store.adb().scan_jobs.find_one({"type": "fix", "status": "done", "rolled_back": {"$ne": True}}, sort=[("finished_at", -1)])
    if not job:
        return {"intent": "scan_undo", "builtin": "There is no applied fix to undo.", "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
    files = sorted({r.get("backup") for r in job.get("results", []) if r.get("ok") and r.get("backup")})
    txt = (f"The last fix ({_ago(job.get('finished_at'))}, by {job.get('requested_by')}) changed {len(files)} file(s). Undoing it restores them from the backup, "
           f"so the vulnerabilities come back.\n\nReply **yes** to undo, or **no** to keep the fix.")
    return {"intent": "scan_undo", "builtin": txt, "use_llm": False, "sources": [], "cards": [], "ctx": ctx, "confirm": True,
            "pending": {"type": "rollback", "rollback_of": job["_id"], "created_at": store.now(), "summary": "undo last fix"}}


def plan_reset(ctx: dict) -> dict:
    if not bot_state()["online"]:
        return {"intent": "scan_reset", "builtin": OFFLINE, "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
    return {"intent": "scan_reset", "builtin": "This restores the demo project in the scan folder to its original vulnerable state (your fixes there are lost).\n\n"
                                               "Reply **yes** to reset, or **no** to cancel.", "use_llm": False, "sources": [], "cards": [], "ctx": ctx,
            "confirm": True, "pending": {"type": "reset", "created_at": store.now(), "summary": "reset the demo project"}}


def _queue(job_type: str, user: dict, conv: dict, **fields) -> str:
    job = {"_id": "job_" + secrets.token_hex(6), "type": job_type, "status": "queued", "requested_by": user["email"], "conversation_id": conv["_id"],
           "created_at": store.now(), **fields}
    store.adb().scan_jobs.insert_one(job)
    store.audit(f"scanner.{job_type}.confirmed", actor=user["email"], job=job["_id"], findings=len(fields.get("finding_ids", [])))
    return job["_id"]


def _wait(job_id: str, seconds: float) -> dict:
    t0 = time.time()
    while time.time() - t0 < seconds:
        j = store.adb().scan_jobs.find_one({"_id": job_id})
        if j and j["status"] in ("done", "failed"):
            return j
        time.sleep(0.6)
    return store.adb().scan_jobs.find_one({"_id": job_id}) or {}


def run_pending(pending: dict, user: dict, conv: dict) -> dict:
    """The user said yes: hand the confirmed work to the bot and report what it did."""
    if not bot_state()["online"]:
        return {"intent": "scan_confirmed", "builtin": OFFLINE, "use_llm": False, "sources": [], "cards": []}
    if pending["type"] == "fix":
        job = _wait(_queue("fix", user, conv, finding_ids=pending["finding_ids"]), 90)
        if job.get("status") not in ("done", "failed"):
            return {"intent": "scan_confirmed", "builtin": "Confirmed. The bot is still working on it - ask \"show findings\" in a moment.", "use_llm": False, "sources": [], "cards": []}
        lines = [f"**Done.** {job.get('summary', '')}", ""]
        for r in job.get("results", []):
            mark = "fixed and verified" if r.get("verified") else "applied" if r.get("ok") else "FAILED"
            lines.append(f"- {mark}: {r.get('message')}" + (f" ({r.get('title')})" if not r.get("ok") else ""))
        lines += ["", "Say **undo last fix** to restore the files, or **show findings** for what is left."]
        return {"intent": "scan_confirmed", "builtin": "\n".join(lines), "use_llm": False, "sources": [], "cards": [], "job": job.get("_id")}
    if pending["type"] == "rollback":
        job = _wait(_queue("rollback", user, conv, rollback_of=pending["rollback_of"]), 60)
        res = (job.get("results") or [{}])[0]
        txt = f"**Undone.** {job.get('summary', '')}: {', '.join(res.get('restored', []))}. The findings are open again." if job.get("status") == "done" and res.get("ok") \
            else f"The undo did not complete: {job.get('summary') or 'the bot is still working'}."
        return {"intent": "scan_confirmed", "builtin": txt, "use_llm": False, "sources": [], "cards": []}
    job = _wait(_queue("reset", user, conv), 60)
    return {"intent": "scan_confirmed", "builtin": f"**Done.** {job.get('summary') or 'The reset is running.'} Say \"show findings\" to see them.",
            "use_llm": False, "sources": [], "cards": []}


def plan_scan_now(user: dict, conv: dict, u: dict) -> dict:
    if not bot_state()["online"]:
        return {"intent": "scan_now", "builtin": OFFLINE, "use_llm": False, "sources": [], "cards": [], "ctx": {}}
    job = _wait(_queue("scan", user, conv), 75)
    p = plan_findings({**u, "ordinals": [], "numbers": []}, {})
    p["builtin"] = (f"Scan finished ({job.get('summary', '')}).\n\n" if job.get("status") == "done" else "The scan is still running; this is the last known state.\n\n") + p["builtin"]
    p["intent"] = "scan_now"
    return p


def plan_ignore(u: dict, ctx: dict, user: dict) -> dict:
    chosen = _select(u, ctx, _open_findings())
    if not chosen or len(chosen) > 5:
        return {"intent": "scan_ignore", "builtin": "Tell me which one to ignore by number, for example \"ignore 4\".", "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
    store.adb().scan_findings.update_many({"_id": {"$in": [f["_id"] for f in chosen]}}, {"$set": {"status": "ignored", "ignored_by": user["email"], "ignored_at": store.now()}})
    store.audit("scanner.ignored", actor=user["email"], findings=[f["title"] for f in chosen])
    return {"intent": "scan_ignore", "builtin": "Ignored: " + "; ".join(f["title"] for f in chosen) + ". It will not be reported again.", "use_llm": False,
            "sources": [], "cards": [], "ctx": ctx}


# ---------------------------------------------------------------------------- the chat turn
def make_plan(user: dict, conv: dict, text: str, ui_platform: str | None) -> dict:
    ctx = conv.get("ctx") or {}
    pending = conv.get("pending")
    notice = ""
    if pending:
        fresh = (store.now() - pending["created_at"]) < timedelta(minutes=10)
        store.adb().conversations.update_one({"_id": conv["_id"]}, {"$set": {"pending": None}})
        if YES.match(text) and fresh:
            p = run_pending(pending, user, conv)
            p["ctx"] = {**ctx, "last_list": "findings"}
            return p
        if YES.match(text):
            return {"intent": "scan_confirmed", "builtin": "That confirmation request expired (10 minutes). Ask again and confirm.", "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
        if NO.match(text):
            return {"intent": "scan_cancelled", "builtin": f"Cancelled - nothing was changed ({pending.get('summary')}).", "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
        notice = f"_(The pending request \"{pending.get('summary')}\" was cancelled - nothing was changed.)_\n\n"
    elif YES.match(text) or NO.match(text):
        return {"intent": "smalltalk", "builtin": "There is nothing waiting for a confirmation. What would you like to know?", "use_llm": False, "sources": [], "cards": [], "ctx": ctx}
    u = understand(text, ctx, ui_platform)
    i = u["intent"]
    if i == "threats":
        p = plan_threats(u)
    elif i == "stats":
        p = plan_stats(u)
    elif i == "cve":
        p = plan_cve(u, ctx)
    elif i == "threat_detail":
        p = plan_threat_detail(u, ctx)
    elif i == "ioc":
        p = plan_ioc(u)
    elif i == "lab" and not u.get("lab") and re.search(r"\b(all|every|which|each)\b.*\blabs?\b.*\b(pass\w*|fail\w*|results?|status|done|work\w*|complet\w*|ok|correct\w*|verif\w+)\b|"
                                                       r"\blabs?\b.*\b(all|every)\b.*\b(pass\w*|fail\w*|work\w*|complet\w*|correct\w*)\b|\b(status|results?) of (all |the )?labs\b",
                                                       text.lower()):
        p = plan_labs_overview()
    elif i == "lab":
        p = plan_knowledge(u, ["lab", "faq", "about"], "lab")
    elif i == "smalltalk":
        p = plan_smalltalk(u, user)
    elif i == "help":
        p = plan_help()
    elif i == "scan_findings":
        p = plan_findings(u, ctx)
    elif i == "scan_fix":
        p = plan_fix(u, ctx, conv)
    elif i == "scan_undo":
        p = plan_undo(ctx)
    elif i == "scan_reset":
        p = plan_reset(ctx)
    elif i == "scan_now":
        p = plan_scan_now(user, conv, u)
    elif i == "scan_ignore":
        p = plan_ignore(u, ctx, user)
    else:
        p = plan_knowledge(u, None, "general")
    if p.get("ctx") is None:
        p["ctx"] = ctx
    p["ctx"]["intent"] = p["intent"]
    if notice:
        p["builtin"] = notice + p["builtin"]
        p["notice"] = notice
    p["understood"] = {"platform": u.get("platform"), "statuses": u.get("statuses"), "intent": p["intent"]}
    return p


def chat_events(user: dict, text: str, conversation_id: str | None = None, platform: str | None = None):
    """Generator of events for one chat turn: meta -> token* -> done. Used by the streaming and the plain /chat endpoint."""
    t0 = time.perf_counter()
    text = (text or "").strip()[:2000]
    platform = platform if platform in S.PLATFORMS else None
    conv = conversation(user, conversation_id, text)
    hist = history(conv["_id"])
    _save(conv, user, "user", text)
    plan = make_plan(user, conv, text, platform)
    backend = llm.backend()
    use_llm = plan.get("use_llm") and backend["provider"] != "builtin"
    yield {"type": "meta", "conversation_id": conv["_id"], "intent": plan["intent"], "understood": plan.get("understood"), "cards": plan.get("cards") or [],
           "sources": plan.get("sources") or [], "board": plan.get("board"), "findings": plan.get("findings"), "confirm": bool(plan.get("confirm")),
           "provider": backend["provider"] if use_llm else "builtin", "model": backend["model"] if use_llm else "built-in answer composer"}
    answer, usage, error = "", {}, None
    prompt_text = ""
    if use_llm:
        content = ((f"FACTS (from MongoDB):\n{plan['facts']}\n\n" if plan.get("facts") else "") + (f"PASSAGES:\n{plan['passages']}\n\n" if plan.get("passages") else "")
                   + f"QUESTION: {text}")
        seq = [{"role": m["role"], "content": m["content"][:500]} for m in hist[-(S.HISTORY_EXCHANGES * 2):]] + [{"role": "user", "content": content}]
        msgs: list = []
        for m in seq:                                   # never two turns of the same role in a row (an earlier turn may have failed)
            if msgs and msgs[-1]["role"] == m["role"]:
                msgs[-1] = m
            else:
                msgs.append(m)
        if msgs[0]["role"] == "assistant":
            msgs = msgs[1:]
        prompt_text = SYSTEM + "\n".join(m["content"] for m in msgs)
        try:
            for piece in llm.stream(SYSTEM, msgs, usage):
                answer += piece
                yield {"type": "token", "text": piece}
            if not answer.strip():
                raise RuntimeError("the model returned an empty answer")
            llm.report_success()
        except Exception as exc:                       # noqa: BLE001 - model crashed / too slow / out of memory -> built-in composer
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
            llm.report_failure(error)
            if answer:
                yield {"type": "reset"}
            answer, use_llm = "", False
    if not use_llm:
        answer = plan["builtin"]
        usage = {"provider": "builtin", "model": "built-in answer composer"}
        for i in range(0, len(answer), 220):            # sent in pieces so the browser shows it the same way as a model's answer
            yield {"type": "token", "text": answer[i:i + 220]}
    elif plan.get("notice"):
        answer = plan["notice"] + answer
    llm.finish_usage(usage, prompt_text, answer) if use_llm else usage.update(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    ms = round((time.perf_counter() - t0) * 1000)
    meta = {"intent": plan["intent"], "provider": usage.get("provider"), "model": usage.get("model"), "ms": ms, "tokens": usage.get("total_tokens", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0), "sources": plan.get("sources") or [],
            "cards": plan.get("cards") or [], "understood": plan.get("understood"), "confirm": bool(plan.get("confirm")), "retrieval": plan.get("retrieval"),
            "llm_error": error, "findings": plan.get("findings")}
    msg = _save(conv, user, "assistant", answer, meta)
    try:
        store.adb().conversations.update_one({"_id": conv["_id"]}, {"$set": {"ctx": plan["ctx"], "pending": plan.get("pending"), "updated_at": store.now()},
                                                                    "$inc": {"messages": 2}})
    except PyMongoError:
        pass
    yield {"type": "done", "conversation_id": conv["_id"], "message_id": msg["_id"], "answer": answer, **meta}


def chat(user: dict, text: str, conversation_id: str | None = None, platform: str | None = None) -> dict:
    """Non-streaming variant: returns the final event (the /chat endpoint of the assignment)."""
    last = {}
    for ev in chat_events(user, text, conversation_id, platform):
        if ev["type"] == "done":
            last = ev
    return last

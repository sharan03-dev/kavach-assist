"""The threat catalog: every Kavach vulnerability and threat report gets a PLATFORM and one of four STATUSES.

    Active        exploitation is confirmed (a KEV catalog or "exploited"/"confirmed" sightings) and it is happening now
    Evolving      it is changing: a public proof-of-concept exists or sightings are rising, exploitation not confirmed yet
    Anticipated   no exploitation seen yet, but it is being discussed / is severe enough that exploitation is expected
    Resolved      a fix exists and the deadline to apply it has passed, with no sightings in the current window
                  (or the record was rejected / the campaign was taken down)

The rules are deterministic and every document stores the reason (status_reason), so the chatbot can explain itself.
Source: database cti_platform (Lab 7) - collections vulnerabilities, gcve_sightings and reports. Result: kavach_assist.threats.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from pymongo import UpdateOne

from . import settings as S
from . import store

UTC = timezone.utc

# platform -> words that identify it in vendor / product / title / description
PLATFORM_RULES = [
    ("Android", r"\bandroid\b|\bpixel\b|samsung mobile|\bqualcomm\b|\bmediatek\b|\bgoogle play\b|\bapk\b|\bexynos\b"),
    ("iOS", r"\bios\b|\bipados\b|\biphone\b|\bipad\b|\bwatchos\b|\btvos\b|\bvisionos\b"),
    ("macOS", r"\bmac ?os\b|\bos x\b|\bmacbook\b|\bapple silicon\b|\bsafari\b|\bwebkit\b|\bxprotect\b|\bgatekeeper\b"),
    ("Windows", r"\bwindows\b|\bwin32k?\b|\bmicrosoft (office|exchange|outlook|edge|defender|sharepoint|word|excel)\b|\bactive directory\b"
                r"|\bntlm\b|\bsmbv?\d?\b|\bpowershell\b|\binternet explorer\b|\b\.net framework\b|\biis\b|\bmsdt\b|\bmshtml\b|\bkerberos\b"),
    ("Linux", r"\blinux\b|\bubuntu\b|\bdebian\b|\bred ?hat\b|\brhel\b|\bcentos\b|\bfedora\b|\bsuse\b|\bkernel\b|\bglibc\b|\bsudo\b|\bopenssh\b"
              r"|\bsystemd\b|\bpolkit\b|\bbash\b|\bxz utils\b|\bcups\b|\bsamba\b|\bselinux\b"),
    ("Network devices", r"\bcisco\b|\bfortinet\b|\bforti(os|gate|manager|web|proxy)\b|\bpalo alto\b|\bpan-os\b|\bivanti\b|\bpulse secure\b"
                        r"|\bjuniper\b|\bjunos\b|\bsonicwall\b|\bf5\b|\bbig-ip\b|\bcitrix\b|\bnetscaler\b|\bzyxel\b|\bd-link\b|\btp-link\b"
                        r"|\bnetgear\b|\bmikrotik\b|\brouter\b|\bfirewall\b|\bvpn\b|\bsophos\b|\bcheck point\b|\bqnap\b|\bsynology\b|\bnas\b"
                        r"|\bzimbra\b|\bubiquiti\b|\baruba\b|\bdraytek\b|\bcamera\b|\bdvr\b|\biot\b"),
    ("Cloud & SaaS", r"\baws\b|\bamazon web services\b|\bazure\b|\bgoogle cloud\b|\bgcp\b|\bkubernetes\b|\bdocker\b|\bcontainer\b|\bsalesforce\b"
                     r"|\bokta\b|\bsnowflake\b|\bmicrosoft 365\b|\boffice 365\b|\bentra\b|\bs3 bucket\b|\bsaas\b|\bcloud\b"),
    ("Web & server software", r"\bapache\b|\bnginx\b|\btomcat\b|\bwordpress\b|\bplugin\b|\bdrupal\b|\bjoomla\b|\bphp\b|\bjava\b|\blog4j\b|\bspring\b"
                              r"|\bstruts\b|\bconfluence\b|\bjira\b|\batlassian\b|\bvmware\b|\bvcenter\b|\besxi\b|\boracle\b|\bweblogic\b|\bsap\b"
                              r"|\bjenkins\b|\bgitlab\b|\bgithub\b|\bnode\.?js\b|\bnpm\b|\bpypi\b|\bpython\b|\bchrome\b|\bchromium\b|\bfirefox\b"
                              r"|\bmozilla\b|\bbrowser\b|\badobe\b|\bcoldfusion\b|\bmongodb\b|\bmysql\b|\bpostgres|\bredis\b|\belasticsearch\b"
                              r"|\bopenssl\b|\bcurl\b|\bveeam\b|\bsolarwinds\b|\bmoveit\b|\bprogress\b|\bmanageengine\b|\bzoho\b|\bcrushftp\b"
                              r"|\bsharepoint\b|\bexchange server\b|\bweb ?server\b|\bcms\b|\bsql injection\b|\bxss\b"),
]
_RULES = [(name, re.compile(rx, re.I)) for name, rx in PLATFORM_RULES]
# products that run on several systems: a browser bug matters on every desktop / phone that runs it
_MULTI = [(re.compile(r"\bchrome\b|\bchromium\b|\bfirefox\b|\bmozilla\b", re.I), ["Windows", "macOS", "Linux", "Android"]),
          (re.compile(r"\bwebkit\b|\bsafari\b", re.I), ["macOS", "iOS"]),
          (re.compile(r"\bopenssl\b|\bcurl\b|\blog4j\b|\bopenssh\b", re.I), ["Linux"])]
RESOLVED_WORDS = re.compile(r"\b(patched|patch (is )?available|fixed|fixes|takedown|taken down|dismantl\w+|seized|arrest\w*|sentenced|"
                            r"disrupt\w+|shut ?down|decryptor)\b", re.I)
EVOLVING_WORDS = re.compile(r"\b(new variant|variant|evolv\w+|resurfac\w+|returns|updated|upgraded|new version|now target\w*|expands?|"
                            r"new campaign|shifts?|adopts?|rebrand\w*)\b", re.I)
ANTICIPATED_WORDS = re.compile(r"\b(warns?|warning|could|may allow|expected|advisory|urges?|proof[- ]of[- ]concept|poc|zero[- ]day|"
                               r"unpatched|no patch|researchers (found|discover\w*)|predict\w*)\b", re.I)
SEV_RANK = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}


def platforms_of(text: str) -> list[str]:
    found = [name for name, rx in _RULES if rx.search(text or "")]
    for rx, extra in _MULTI:
        if rx.search(text or ""):
            found += [p for p in extra if p not in found]
    if not found and re.search(r"\bapple\b", text or "", re.I):
        found = ["macOS", "iOS"]
    return found or ["Other"]


def _dt(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, str) and v:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _band(score) -> str | None:
    if score is None:
        return None
    return "Critical" if score >= 9 else "High" if score >= 7 else "Medium" if score >= 4 else "Low"


def classify_vuln(v: dict, trend: dict, now: datetime) -> tuple[str, str]:
    """(status, reason) of one vulnerability document of cti_platform.vulnerabilities."""
    kev, cats, g = v.get("kev") or {}, v.get("kev_catalogs") or [], v.get("gcve") or {}
    ss = v.get("sighting_summary") or {}
    exploited_s = (ss.get("exploited") or 0) + (ss.get("confirmed") or 0)
    poc, seen, total = ss.get("poc") or 0, ss.get("seen") or 0, ss.get("total") or 0
    listed = [d for d in [_dt(kev.get("date_added"))] + [_dt(c.get("listed_at")) for c in cats] if d]
    first_listed = min(listed) if listed else None
    in_kev = bool(kev or cats)
    due = _dt(kev.get("due_date"))
    last7, prev7 = trend.get("last7", 0), trend.get("prev7", 0)
    rising = last7 >= 3 and last7 > prev7 * 1.5
    cat_names = ", ".join(sorted({c.get("catalog") or "GCVE" for c in cats} | ({"CISA"} if kev else set()))) or "KEV"
    if (g.get("state") or "").upper() == "REJECTED":
        return "Resolved", "the CVE record was rejected by its numbering authority"
    if in_kev or exploited_s:
        recent_listing = first_listed and (now - first_listed).days <= 45
        if total > 0 or recent_listing or (due and due >= now):
            why = []
            if in_kev:
                why.append(f"listed as exploited in the wild ({cat_names}" + (f", since {first_listed:%d %b %Y})" if first_listed else ")"))
            if exploited_s:
                why.append(f"{exploited_s} exploitation sighting(s) in the last {ss.get('window_days', 30)} days")
            elif total:
                why.append(f"{total} sighting(s) in the last {ss.get('window_days', 30)} days")
            if rising:
                why.append(f"sightings rising ({prev7} -> {last7} week over week)")
            if due and due >= now:
                why.append(f"patch deadline {due:%d %b %Y} not reached yet")
            return "Active", "; ".join(why)
        why = "exploited in the past" + (f" ({cat_names}, listed {first_listed:%d %b %Y})" if first_listed else "")
        why += f"; a fix exists" + (f" (deadline {due:%d %b %Y} has passed)" if due else "") + "; no sightings in the current 30-day window"
        return "Resolved", why
    if poc or rising:
        why = []
        if poc:
            why.append(f"{poc} public proof-of-concept sighting(s)")
        if rising:
            why.append(f"sightings rising ({prev7} -> {last7} week over week)")
        why.append("exploitation not confirmed yet")
        return "Evolving", "; ".join(why)
    cvss = ((g.get("cvss") or {}).get("score"))
    epss = ((g.get("epss") or {}).get("score"))
    why = []
    if seen or total:
        why.append(f"{seen or total} mention(s) in the last {ss.get('window_days', 30)} days")
    if cvss is not None and cvss >= 7:
        why.append(f"CVSS {cvss}")
    if epss is not None and epss >= 0.05:
        why.append(f"EPSS {epss:.0%} chance of exploitation in 30 days")
    if v.get("report_count"):
        why.append(f"named in {v['report_count']} threat report(s)")
    why.append("no exploitation or proof-of-concept seen yet")
    return "Anticipated", "; ".join(why)


def classify_report(r: dict, now: datetime) -> tuple[str, str] | None:
    """(status, reason) of one threat report, or None when it is not a threat (plain news)."""
    c = r.get("classification") or {}
    verdict = c.get("verdict")
    pub = _dt(r.get("published_at")) or now
    age = (now - pub).days
    text = f"{r.get('title', '')}. {r.get('summary', '')}"
    if verdict not in ("Actual threat", "Potential threat"):
        return None
    if RESOLVED_WORDS.search(r.get("title", "")) and verdict != "Actual threat":
        return "Resolved", "the report describes a fix, takedown or arrest"
    if age > 60:
        return "Resolved", f"reported {age} days ago with no newer report on it"
    if verdict == "Actual threat":
        if age <= 21:
            if EVOLVING_WORDS.search(text):
                return "Evolving", f"confirmed threat reported {age} day(s) ago that describes a new variant or changed behaviour"
            return "Active", f"confirmed threat reported {age} day(s) ago"
        return "Evolving", f"confirmed threat first reported {age} days ago, still inside the 60-day watch window"
    if ANTICIPATED_WORDS.search(text):
        return "Anticipated", f"a warning / research finding from {age} day(s) ago; no confirmed attacks in the report"
    return ("Evolving", f"potential threat reported {age} day(s) ago") if age <= 21 else ("Anticipated", f"potential threat, {age} days old, unconfirmed")


def _trends(db, now: datetime) -> dict:
    """Week-over-week sightings per vulnerability from the gcve_sightings time series (one $group aggregation)."""
    d7, d14 = now - timedelta(days=7), now - timedelta(days=14)
    out: dict = defaultdict(dict)
    for row in db.gcve_sightings.aggregate([
            {"$match": {"date": {"$gte": d14}}},
            {"$group": {"_id": "$vulnerability",
                        "last7": {"$sum": {"$cond": [{"$gte": ["$date", d7]}, "$count", 0]}},
                        "prev7": {"$sum": {"$cond": [{"$lt": ["$date", d7]}, "$count", 0]}}}}], allowDiskUse=True):
        out[row["_id"]] = {"last7": row["last7"], "prev7": row["prev7"]}
    return out


def _vuln_doc(v: dict, trend: dict, now: datetime) -> dict:
    kev, g, ss = v.get("kev") or {}, v.get("gcve") or {}, v.get("sighting_summary") or {}
    affected = g.get("affected") or []
    vendor = kev.get("vendor") or (affected[0].get("vendor") if affected else "") or ""
    product = kev.get("product") or (affected[0].get("product") if affected else "") or ""
    title = kev.get("name") or g.get("title") or f"{vendor} {product}".strip() or v["_id"]
    desc = kev.get("description") or g.get("description") or ""
    text = " ".join([vendor, product, title, desc] + [f"{a.get('vendor', '')} {a.get('product', '')}" for a in affected[:6]])
    status, reason = classify_vuln(v, trend, now)
    cvss = (g.get("cvss") or {}).get("score")
    sev = (g.get("cvss") or {}).get("severity") or _band(cvss)
    if not sev:
        sev = "Critical" if (kev.get("ransomware_use") or "").lower() == "known" else "High" if (kev or v.get("kev_catalogs")) else "Medium"
    sev = sev.capitalize()
    exploited = bool(kev or v.get("kev_catalogs") or (ss.get("exploited") or 0) + (ss.get("confirmed") or 0))
    priority = ({"Active": 400, "Evolving": 300, "Anticipated": 200, "Resolved": 0}[status] + SEV_RANK.get(sev, 2) * 20
                + min(ss.get("total") or 0, 60) + (25 if (kev.get("ransomware_use") or "").lower() == "known" else 0)
                + min((v.get("report_count") or 0) * 3, 30) + (10 if exploited else 0))
    return {"_id": v["_id"], "kind": "vulnerability", "title": title[:240], "description": desc[:1500], "vendor": vendor, "product": product,
            "platforms": platforms_of(text), "status": status, "status_reason": reason, "severity": sev, "cvss": cvss,
            "epss": (g.get("epss") or {}).get("score"), "exploited": exploited,
            "ransomware": (kev.get("ransomware_use") or "").lower() == "known",
            "kev_added": _dt(kev.get("date_added")), "due_date": _dt(kev.get("due_date")),
            "catalogs": sorted({c.get("catalog") or "GCVE" for c in v.get("kev_catalogs") or []} | ({"CISA"} if kev else set())),
            "sightings": {"total": ss.get("total") or 0, "exploited": (ss.get("exploited") or 0) + (ss.get("confirmed") or 0),
                          "poc": ss.get("poc") or 0, "last7": trend.get("last7", 0), "prev7": trend.get("prev7", 0),
                          "last_sighted": _dt(ss.get("last_sighted"))},
            "report_count": v.get("report_count") or 0, "published": _dt(g.get("published")) or _dt(v.get("first_seen")),
            "references": (g.get("references") or [])[:5] + [f"https://nvd.nist.gov/vuln/detail/{v['_id']}"] if v["_id"].startswith("CVE-") else (g.get("references") or [])[:5],
            "cwes": (kev.get("cwes") or g.get("cwes") or [])[:4], "priority": priority}


def _report_doc(r: dict, now: datetime) -> dict | None:
    res = classify_report(r, now)
    if not res:
        return None
    status, reason = res
    c = r.get("classification") or {}
    text = f"{r.get('title', '')}. {r.get('summary', '')} {' '.join(r.get('tags') or [])}"
    sev = (c.get("severity") or "Medium").capitalize()
    pub = _dt(r.get("published_at")) or now
    age = max(0, (now - pub).days)
    priority = ({"Active": 380, "Evolving": 280, "Anticipated": 180, "Resolved": 0}[status] + SEV_RANK.get(sev, 2) * 20 + max(0, 30 - age)
                + min(len(r.get("cve_ids") or []) * 4, 20))
    return {"_id": f"report:{r['_id']}", "kind": "report", "title": (r.get("title") or "")[:240], "description": (r.get("summary") or "")[:1500],
            "vendor": "", "product": "", "platforms": platforms_of(text), "status": status, "status_reason": reason, "severity": sev,
            "category": c.get("category"), "verdict": c.get("verdict"), "cve_ids": (r.get("cve_ids") or [])[:8],
            "actors": (r.get("actor_ids") or [])[:4], "url": r.get("url"), "source": r.get("source_id"), "published": pub,
            "references": [r["url"]] if r.get("url") else [], "priority": priority, "exploited": c.get("verdict") == "Actual threat"}


def rebuild(log=print, max_reports: int = 6000) -> dict:
    """Recompute the whole catalog (idempotent: bulk upserts, then documents that disappeared are removed)."""
    db, out, now = store.cti(), store.adb().threats, store.now()
    trends = _trends(db, now)
    ops, n_v, n_r = [], 0, 0
    counts: dict = defaultdict(int)

    def flush():
        if ops:
            out.bulk_write(ops, ordered=False)
            ops.clear()

    for v in db.vulnerabilities.find({}):
        d = _vuln_doc(v, trends.get(v["_id"], {}), now)
        d["updated_at"] = now
        counts[d["status"]] += 1
        ops.append(UpdateOne({"_id": d["_id"]}, {"$set": d}, upsert=True))
        n_v += 1
        if len(ops) >= 1000:
            flush()
    proj = {"title": 1, "summary": 1, "url": 1, "published_at": 1, "source_id": 1, "classification": 1, "tags": 1, "cve_ids": 1, "actor_ids": 1}
    for r in db.reports.find({"classification.verdict": {"$in": ["Actual threat", "Potential threat"]}}, proj).sort("published_at", -1).limit(max_reports):
        d = _report_doc(r, now)
        if d:
            d["updated_at"] = now
            counts[d["status"]] += 1
            ops.append(UpdateOne({"_id": d["_id"]}, {"$set": d}, upsert=True))
            n_r += 1
            if len(ops) >= 1000:
                flush()
    flush()
    removed = out.delete_many({"updated_at": {"$lt": now}}).deleted_count
    res = {"vulnerabilities": n_v, "reports": n_r, "removed": removed, "by_status": dict(counts), "at": now}
    store.set_setting("threats_built", res)
    log(f"[assist] threat catalog: {n_v:,} vulnerabilities + {n_r:,} reports -> {dict(counts)}")
    return res


# ---------------------------------------------------------------------------- queries used by the chatbot
CARD = {"title": 1, "kind": 1, "platforms": 1, "status": 1, "status_reason": 1, "severity": 1, "cvss": 1, "epss": 1, "vendor": 1, "product": 1,
        "description": 1, "exploited": 1, "ransomware": 1, "due_date": 1, "kev_added": 1, "sightings": 1, "url": 1, "published": 1,
        "references": 1, "cve_ids": 1, "catalogs": 1, "category": 1, "priority": 1}


def find(platform: str | None = None, status: str | None = None, kind: str | None = None, limit: int = 6, text: str | None = None) -> list[dict]:
    q: dict = {}
    if platform:
        q["platforms"] = platform
    if status:
        q["status"] = status
    if kind:
        q["kind"] = kind
    if text:
        q["$or"] = [{"title": {"$regex": re.escape(text), "$options": "i"}}, {"product": {"$regex": re.escape(text), "$options": "i"}},
                    {"vendor": {"$regex": re.escape(text), "$options": "i"}}]
    return list(store.adb().threats.find(q, CARD).sort([("priority", -1), ("_id", 1)]).limit(limit))


def board(platform: str | None = None) -> dict:
    """Counts per status (and per platform) with one $facet aggregation - the numbers on top of the chat page."""
    match = {"platforms": platform} if platform else {}
    f = list(store.adb().threats.aggregate([{"$match": match}, {"$facet": {
        "by_status": [{"$group": {"_id": {"s": "$status", "k": "$kind"}, "n": {"$sum": 1}}}],
        "by_platform": [{"$unwind": "$platforms"}, {"$group": {"_id": {"p": "$platforms", "s": "$status"}, "n": {"$sum": 1}}}],
        "by_severity": [{"$match": {"status": {"$ne": "Resolved"}}}, {"$group": {"_id": "$severity", "n": {"$sum": 1}}}],
    }}]))[0]
    by_status = {s: {"total": 0, "vulnerability": 0, "report": 0} for s in S.STATUSES}
    for x in f["by_status"]:
        s = x["_id"].get("s")
        if s in by_status:
            by_status[s][x["_id"].get("k") or "vulnerability"] = x["n"]
            by_status[s]["total"] += x["n"]
    by_platform = {p: {s: 0 for s in S.STATUSES} for p in S.PLATFORMS}
    for x in f["by_platform"]:
        p, s = x["_id"].get("p"), x["_id"].get("s")
        if p in by_platform and s in by_platform[p]:
            by_platform[p][s] = x["n"]
    return {"platform": platform, "by_status": by_status, "by_platform": by_platform,
            "by_severity": {x["_id"]: x["n"] for x in f["by_severity"] if x["_id"]}, "built": store.setting("threats_built")}


def get(threat_id: str) -> dict | None:
    return store.adb().threats.find_one({"_id": threat_id})


def search(text: str, platform: str | None = None, statuses: list[str] | None = None, limit: int = 6) -> list[dict]:
    """Threats that mention the topic of the question ($text index on title / product / vendor / description)."""
    q: dict = {"$text": {"$search": text[:200]}}
    if platform:
        q["platforms"] = platform
    if statuses:
        q["status"] = {"$in": statuses}
    try:
        rows = list(store.adb().threats.find(q, {**CARD, "ts": {"$meta": "textScore"}}).sort([("ts", {"$meta": "textScore"})]).limit(limit * 3))
    except Exception:                                  # noqa: BLE001
        return []
    rows.sort(key=lambda t: (-(t.get("ts") or 0) - (2 if t.get("status") != "Resolved" else 0), -(t.get("priority") or 0)))
    return rows[:limit]

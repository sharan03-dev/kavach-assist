"""CTI Platform - FastAPI backend.

Run from the project root:   python -m uvicorn backend.app.main:app --reload --port 8000
Then open                   http://127.0.0.1:8000
"""
import asyncio
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import OperationFailure, PyMongoError, ServerSelectionTimeoutError

from . import config, gcve, ingest, ipintel, labs
from .classifier import ALL_CATEGORIES, classify
from .db import client, ensure_indexes, get_db, get_lab_db, ping
from .ioc import CVE_RX, extract, indicator_id, parse_ip
from .labs_api import router as labs_router

UTC = timezone.utc
J = labs.to_jsonable
STATE = {"ingest": {"running": False, "started_at": None, "finished_at": None, "result": None, "error": None}}
KEV_ANY = {"$or": [{"kev": {"$exists": True}}, {"kev_catalogs.0": {"$exists": True}}]}
_cache = {}


def cached(key, seconds, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


@asynccontextmanager
async def lifespan(app):
    if await run_in_threadpool(ping):
        try:
            await run_in_threadpool(ensure_indexes)
        except Exception as exc:  # never block the website because of an index problem
            print("[warn] could not create indexes:", exc)
    else:
        print("\n[!] MongoDB is not reachable at", config.MONGO_URI,
              "\n    Start it with:  brew services start mongodb-community@8.0\n")
    if _assist is not None:
        _assist.startup()                 # background: indexes, administrator account, knowledge base + threat catalog
    yield
    if _assist is not None:
        _assist.shutdown()                # graceful shutdown: close HTTP + MongoDB connection pools


app = FastAPI(title="Kavach CTI + Labs 7-18 + Kavach Assist API", version="3.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=800)
# Final assessment: Kavach Assist (login, RAG chat, scanner bot). Its gate asks for a signed-in user on every /api route.
try:
    from .assist import api as _assist
    app.middleware("http")(_assist.gate)
    app.include_router(_assist.root)
except Exception as _exc:                            # noqa: BLE001 - the labs website must start even if this fails
    _assist = None
    print(f"[assist] Kavach Assist not loaded: {type(_exc).__name__}: {_exc}")


@app.exception_handler(ServerSelectionTimeoutError)
async def mongo_down(_req, _exc):
    return JSONResponse(status_code=503, content={
        "detail": "MongoDB is not running. In Terminal run: brew services start mongodb-community@8.0"})


def _sources_map(db):
    return cached("sources_map", 30, lambda: {s["_id"]: {"name": s["name"], "reliability": s.get("reliability"),
                                                          "homepage": s.get("homepage")}
                                              for s in db.sources.find({}, {"name": 1, "reliability": 1, "homepage": 1})})


def _report_card(r, smap):
    c = r.get("classification", {})
    return {"id": str(r["_id"]), "title": r["title"], "url": r["url"], "summary": r.get("summary", "")[:420],
            "published_at": r.get("published_at"), "source_id": r.get("source_id"),
            "source": smap.get(r.get("source_id"), {}).get("name", r.get("source_id")),
            "category": c.get("category"), "severity": c.get("severity"), "verdict": c.get("verdict"),
            "confidence": c.get("confidence"), "admiralty": c.get("admiralty"), "tags": r.get("tags", []),
            "cve_ids": r.get("cve_ids", [])[:6], "actor_ids": r.get("actor_ids", [])[:4],
            "ioc_count": len(r.get("indicator_ids", [])), "score": r.get("score")}


CARD_FIELDS = {"title": 1, "url": 1, "summary": 1, "published_at": 1, "source_id": 1, "classification.category": 1,
               "classification.severity": 1, "classification.verdict": 1, "classification.confidence": 1,
               "classification.admiralty": 1, "tags": 1, "cve_ids": 1, "actor_ids": 1, "indicator_ids": 1}


# ======================================================================== health / overview
@app.get("/api/health")
def health():
    ok = ping()
    out = {"mongodb": ok, "uri": config.MONGO_URI, "db": config.DB_NAME, "time": datetime.now(UTC)}
    if ok:
        db = get_db()
        out["version"] = client().server_info().get("version")
        out["reports"] = db.reports.estimated_document_count()
        out["indicators"] = db.indicators.estimated_document_count()
        out["needs_setup"] = out["reports"] == 0 and out["indicators"] == 0
    return out


@app.get("/api/overview")
def overview(days: int = 30):
    db = get_db()

    def build():
        since = datetime.now(UTC) - timedelta(days=days)
        f = list(db.reports.aggregate([{"$facet": {
            "by_category": [{"$group": {"_id": "$classification.category", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}],
            "by_severity": [{"$group": {"_id": "$classification.severity", "n": {"$sum": 1}}}],
            "by_verdict": [{"$group": {"_id": "$classification.verdict", "n": {"$sum": 1}}}],
            "per_day": [{"$match": {"published_at": {"$gte": since}}},
                        {"$group": {"_id": {"d": {"$dateToString": {"format": "%Y-%m-%d", "date": "$published_at"}},
                                            "s": "$classification.severity"}, "n": {"$sum": 1}}}],
            "top_cves": [{"$unwind": "$cve_ids"}, {"$group": {"_id": "$cve_ids", "n": {"$sum": 1}}},
                         {"$sort": {"n": -1, "_id": -1}}, {"$limit": 8}],
            "top_actors": [{"$unwind": "$actor_ids"}, {"$group": {"_id": "$actor_ids", "n": {"$sum": 1}}},
                           {"$sort": {"n": -1}}, {"$limit": 8},
                           {"$lookup": {"from": "threat_actors", "localField": "_id", "foreignField": "_id",
                                        "as": "a"}}],
            "latest_threats": [{"$match": {"classification.verdict": "Actual threat"}},
                               {"$sort": {"published_at": -1}}, {"$limit": 7}, {"$project": CARD_FIELDS}],
        }}], allowDiskUse=True))[0]
        smap = _sources_map(db)
        kev_cves = {v["_id"]: {**(v.get("kev") or {}), "catalogs": [k["catalog"] for k in v.get("kev_catalogs", [])]}
                    for v in db.vulnerabilities.find({"_id": {"$in": [c["_id"] for c in f["top_cves"]]}, **KEV_ANY},
                                                     {"kev.product": 1, "kev.vendor": 1, "kev_catalogs.catalog": 1})}
        src = list(db.sources.find({}, {"name": 1, "kind": 1, "reliability": 1, "stats": 1, "homepage": 1}))
        return {
            "counts": {
                "reports": db.reports.estimated_document_count(),
                "indicators": db.indicators.estimated_document_count(),
                "sightings": db.sightings.estimated_document_count(),
                "kev": db.vulnerabilities.count_documents(KEV_ANY),
                "gcve_sighted": db.vulnerabilities.count_documents({"sighting_summary": {"$exists": True}}),
                "lookups": db.lookup_history.estimated_document_count(),
                "annual_reports": db.annual_reports.estimated_document_count(),
                "sources_ok": sum(1 for s in src if (s.get("stats") or {}).get("last_status") == "ok"),
                "sources_total": len(src),
            },
            "by_category": [{"name": x["_id"] or "General security", "n": x["n"]} for x in f["by_category"]],
            "by_severity": {x["_id"]: x["n"] for x in f["by_severity"] if x["_id"]},
            "by_verdict": {x["_id"]: x["n"] for x in f["by_verdict"] if x["_id"]},
            "per_day": [{"day": x["_id"]["d"], "severity": x["_id"]["s"], "n": x["n"]} for x in f["per_day"]],
            "top_cves": [{"cve": x["_id"], "n": x["n"], "kev": kev_cves.get(x["_id"])} for x in f["top_cves"]],
            "top_actors": [{"id": x["_id"], "n": x["n"], "name": (x["a"][0]["name"] if x["a"] else x["_id"]),
                            "type": (x["a"][0].get("type") if x["a"] else None),
                            "origin": (x["a"][0].get("origin") if x["a"] else None)} for x in f["top_actors"]],
            "latest_threats": [_report_card(r, smap) for r in f["latest_threats"]],
            "sources": [{"id": s["_id"], "name": s["name"], "kind": s.get("kind"), "reliability": s.get("reliability"),
                         "status": (s.get("stats") or {}).get("last_status"), "items": (s.get("stats") or {}).get("items"),
                         "last_fetch": (s.get("stats") or {}).get("last_fetch"),
                         "error": (s.get("stats") or {}).get("last_error")} for s in src],
            "recent_lookups": ipintel.recent_lookups(db, 8),
            "trending_vulns": _trending(db, 7, 8),
            "last_ingest": J(db.ingest_runs.find_one({}, {"results": 0}, sort=[("started_at", -1)])),
        }

    return J(cached(f"overview:{days}", 15, build))


# ======================================================================== reports / search
def _build_filter(q, category, severity, verdict, source, days):
    filt, mode = {}, "all"
    if q and q.strip():
        qs = q.strip()
        if CVE_RX.fullmatch(qs):
            filt["cve_ids"], mode = qs.upper(), "cve"
        elif parse_ip(qs):
            ip = parse_ip(qs)
            filt["indicator_ids"], mode = indicator_id(f"ipv{ip.version}", str(ip)), "ip"
        else:
            filt["$text"], mode = {"$search": qs}, "text"
    if category:
        filt["classification.category"] = {"$in": category.split("|")}
    if severity:
        filt["classification.severity"] = {"$in": severity.split("|")}
    if verdict:
        filt["classification.verdict"] = {"$in": verdict.split("|")}
    if source:
        filt["source_id"] = {"$in": source.split("|")}
    if days:
        filt["published_at"] = {"$gte": datetime.now(UTC) - timedelta(days=days)}
    return filt, mode


@app.get("/api/reports")
def reports(q: str = "", category: str = "", severity: str = "", verdict: str = "", source: str = "",
            days: int = 0, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100), sort: str = "relevance"):
    db = get_db()
    filt, mode = _build_filter(q, category, severity, verdict, source, days)
    proj = dict(CARD_FIELDS)
    t = time.perf_counter()
    try:
        if mode == "text":
            proj["score"] = {"$meta": "textScore"}
            order = [("score", {"$meta": "textScore"}), ("published_at", -1)] if sort == "relevance" else [("published_at", -1)]
        else:
            order = [("published_at", -1 if sort != "oldest" else 1)]
        total = db.reports.count_documents(filt)
        rows = list(db.reports.find(filt, proj).sort(order).skip((page - 1) * limit).limit(limit))
    except OperationFailure:  # text index missing -> fall back to a regex search
        filt.pop("$text", None)
        filt["$or"] = [{"title": {"$regex": re.escape(q), "$options": "i"}},
                       {"summary": {"$regex": re.escape(q), "$options": "i"}}]
        total = db.reports.count_documents(filt)
        rows = list(db.reports.find(filt, CARD_FIELDS).sort("published_at", -1).skip((page - 1) * limit).limit(limit))
        mode = "regex"
    smap = _sources_map(db)
    return J({"total": total, "page": page, "limit": limit, "mode": mode,
              "took_ms": round((time.perf_counter() - t) * 1000, 1),
              "results": [_report_card(r, smap) for r in rows]})


@app.get("/api/facets")
def facets():
    db = get_db()

    def build():
        f = list(db.reports.aggregate([{"$facet": {
            "category": [{"$group": {"_id": "$classification.category", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}],
            "severity": [{"$group": {"_id": "$classification.severity", "n": {"$sum": 1}}}],
            "verdict": [{"$group": {"_id": "$classification.verdict", "n": {"$sum": 1}}}],
            "source": [{"$group": {"_id": "$source_id", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}],
        }}]))[0]
        smap = _sources_map(db)
        return {k: [{"value": x["_id"], "n": x["n"],
                     **({"label": smap.get(x["_id"], {}).get("name", x["_id"])} if k == "source" else {})}
                    for x in v if x["_id"]] for k, v in f.items()}

    return cached("facets", 20, build)


@app.get("/api/reports/{rid}")
def report_detail(rid: str):
    db = get_db()
    try:
        oid = ObjectId(rid)
    except InvalidId:
        raise HTTPException(404, "Report not found")
    rows = list(db.reports.aggregate([
        {"$match": {"_id": oid}},
        {"$lookup": {"from": "sources", "localField": "source_id", "foreignField": "_id", "as": "source"}},
        {"$lookup": {"from": "vulnerabilities", "localField": "cve_ids", "foreignField": "_id", "as": "cves"}},
        {"$lookup": {"from": "threat_actors", "localField": "actor_ids", "foreignField": "_id", "as": "actors"}},
        {"$lookup": {"from": "indicators", "localField": "indicator_ids", "foreignField": "_id", "as": "indicators"}},
        {"$set": {"source": {"$first": "$source"}}},
    ]))
    if not rows:
        raise HTTPException(404, "Report not found")
    r = rows[0]
    related = list(db.reports.find({"_id": {"$ne": oid}, "$or": [
        {"cve_ids": {"$in": r.get("cve_ids", [])}} if r.get("cve_ids") else {"_id": None},
        {"actor_ids": {"$in": r.get("actor_ids", [])}} if r.get("actor_ids") else {"_id": None},
    ]}, CARD_FIELDS).sort("published_at", -1).limit(5))
    smap = _sources_map(db)
    r["related"] = [_report_card(x, smap) for x in related]
    r["id"] = str(r.pop("_id"))
    return J(r)


# ======================================================================== classification
@app.post("/api/classify")
def classify_text(payload: dict = Body(...)):
    """Lab 7 classifier behind the Lab 10 paywall: 3 free uses per user, then Rs 100 per month.
    The classification runs INSIDE the billing transaction, so a use is only counted if it succeeded."""
    text = str(payload.get("text", ""))[:20000]
    if len(text.strip()) < 8:
        raise HTTPException(400, "Paste at least a sentence of text to classify.")
    user = str(payload.get("user", "")).strip().lower()
    if not user:
        raise HTTPException(401, "Sign in to use the classifier: 3 free classifications, then Rs 100 per month.")
    from . import billing
    from .billing_api import ready as billing_ready
    try:
        billing_ready()
        out = billing.consume(user, lambda: _classify_now(text, payload.get("reliability", "C")), note=text[:60])
    except billing.BillingError as exc:
        raise HTTPException(exc.status, str(exc))
    return J({**out["result"], "billing": {"kind": out["kind"], "trial": out["trial"], "account": out["account"],
                                           "attempts": out["attempts"], "backend": billing.backend()["label"]}})


def _classify_now(text: str, rel: str) -> dict:
    db = get_db()
    iocs = extract(text)
    exploited = ingest.exploited_cves(db, iocs["cves"])
    kev = [c for c in iocs["cves"] if c in exploited]
    actors = [aid for aid, rx in ingest.build_actor_matchers(db) if rx.search(text)]
    names = {a["_id"]: a for a in db.threat_actors.find({"_id": {"$in": actors}}, {"name": 1, "origin": 1, "type": 1})}
    n_ioc = len(iocs["ips"]) + len(iocs["domains"]) + len(iocs["hashes"]) + len(iocs["urls"])
    cls = classify(text, reliability=rel if rel in "ABCDEF" else "C", ioc_count=n_ioc,
                   cve_count=len(iocs["cves"]), kev_count=len(kev), actor_count=len(actors))
    return J({**cls, "iocs": {**iocs, "hashes": [{"type": t, "value": v} for t, v in iocs["hashes"]]},
              "kev": kev, "actors": [{"id": a, **names.get(a, {})} for a in actors],
              "all_categories": ALL_CATEGORIES})


# ======================================================================== IP intelligence
@app.get("/api/ip/{ip:path}")
async def ip_lookup(ip: str, refresh: bool = False):
    try:
        return J(await ipintel.lookup(get_db(), ip, refresh=refresh))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/ip-examples")
def ip_examples():
    """Real IPs from the imported feeds so the demo always has something interesting to check."""
    db = get_db()

    def build():
        out = []

        def first(source, label, sort=None):
            s = db.sightings.find_one({"source_id": source, "report_id": {"$exists": False}}, sort=sort)
            if s:
                kind, value = s["indicator_id"].split(":", 1)
                if kind == "cidr":
                    import ipaddress
                    value = str(ipaddress.ip_network(value).network_address + 1)
                out.append({"ip": value, "label": label})

        first("feodo", "botnet C2")
        first("ipsum", "on many blacklists", sort=[("detail.hits", -1)])
        first("spamhaus_drop", "hijacked network")
        first("tor_exits", "Tor exit")
        first("sample-data", "demo")
        out += [{"ip": "8.8.8.8", "label": "Google DNS"}, {"ip": "192.168.1.10", "label": "private"}]
        return out

    return cached("ip_examples", 300, build)


@app.get("/api/lookups/recent")
def recent(limit: int = 12):
    return J(ipintel.recent_lookups(get_db(), limit))


@app.get("/api/indicators")
def indicators(q: str = "", type: str = "", limit: int = Query(25, le=100)):
    db = get_db()
    filt = {}
    if type:
        filt["type"] = type
    if q:
        filt["value"] = {"$regex": "^" + re.escape(q.strip())}
    rows = list(db.indicators.find(filt, {"type": 1, "value": 1, "tags": 1, "first_seen": 1, "last_seen": 1})
                .sort("last_seen", -1).limit(limit))
    return J({"results": [{"id": r["_id"], **{k: r.get(k) for k in ("type", "value", "tags", "first_seen", "last_seen")}}
                          for r in rows]})


# ======================================================================== sources / ingest
@app.get("/api/sources")
def sources():
    return J(list(get_db().sources.find({}).sort([("kind", 1), ("name", 1)])))


async def _run_ingest(what):
    st = STATE["ingest"]
    st.update(running=True, started_at=datetime.now(UTC), error=None, result=None)
    db = get_db()
    started = datetime.now(UTC)
    logs = []
    try:
        result = {}
        if what in ("gcve", "all"):      # first, so new reports are classified with fresh exploitation data
            result["gcve"] = await ingest.import_gcve(db, log=logs.append)
        if what in ("news", "all"):
            result["news"] = await ingest.ingest_news(db, log=logs.append)
            await run_in_threadpool(ingest.log_run, db, "news", started, result["news"])
        if what in ("feeds", "all"):
            result["feeds"] = await run_in_threadpool(ingest.import_ioc_feeds, db, None, logs.append)
        if what in ("annual", "all"):
            r = await run_in_threadpool(ingest.import_annual_reports, db, None, logs.append)
            result["annual"] = {"status": r["status"], "items": r.get("items"), "new": len(r.get("new", []))}
        result["log"] = logs
        st["result"] = result
        _cache.clear()
    except Exception as exc:
        st["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        st.update(running=False, finished_at=datetime.now(UTC))


@app.post("/api/ingest")
async def start_ingest(payload: dict = Body(default={})):
    what = payload.get("what", "news")
    if STATE["ingest"]["running"]:
        return {"started": False, "message": "An update is already running."}
    asyncio.create_task(_run_ingest(what))
    return {"started": True, "what": what}


@app.get("/api/ingest/status")
def ingest_status():
    return J(STATE["ingest"])


@app.get("/api/annual-reports")
def annual_reports(q: str = "", year: int = 0, limit: int = Query(30, le=200)):
    filt = {}
    if q:
        filt["$or"] = [{"title": {"$regex": re.escape(q), "$options": "i"}},
                       {"organization": {"$regex": re.escape(q), "$options": "i"}}]
    if year:
        filt["year"] = year
    rows = list(get_db().annual_reports.find(filt).sort([("year", -1), ("organization", 1)]).limit(limit))
    return J({"results": rows, "total": get_db().annual_reports.count_documents(filt)})


# ======================================================================== GCVE vulnerability intelligence
SIGHT_W = {"exploited": 6, "confirmed": 4, "published-proof-of-concept": 3, "seen": 1}
VULN_CARD = {"gcve.title": 1, "gcve.cvss": 1, "gcve.epss": 1, "gcve.cwes": 1, "gcve.affected": {"$slice": 3},
             "gcve.published": 1, "kev_catalogs.catalog": 1, "kev.date_added": 1, "sighting_summary": 1,
             "report_count": 1}


def _since(days):
    d = datetime.now(UTC) - timedelta(days=max(1, days) - 1)
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _vuln_card(v):
    g = v.get("gcve") or {}
    return {"id": v["_id"], "title": g.get("title"), "cvss": g.get("cvss"), "epss": g.get("epss"),
            "cwes": g.get("cwes", []), "affected": g.get("affected", []), "published": g.get("published"),
            "kev": sorted({k["catalog"] for k in v.get("kev_catalogs", [])} | ({"CISA"} if v.get("kev") else set())),
            "summary": v.get("sighting_summary"), "report_count": v.get("report_count", 0)}


def _trending(db, days=7, limit=10, stype=None, min_total=1, boost=False):
    """Like the db.gcve.eu 'Trending' tab: vulnerabilities ranked by weighted sightings in the window
    (exploited > confirmed > PoC > seen), optional type filter, minimum sightings and 'boost recent'."""
    since = _since(days)
    match = {"date": {"$gte": since}}
    if stype in SIGHT_W:
        match["type"] = stype
    rows = list(db.gcve_sightings.aggregate([
        {"$match": match},
        {"$group": {"_id": "$vulnerability", "total": {"$sum": "$count"}, "last": {"$max": "$date"},
                    "rows": {"$push": {"d": "$date", "t": "$type", "n": "$count"}}}},
        {"$match": {"total": {"$gte": max(1, min_total)}}},
        {"$sort": {"total": -1, "last": -1}}, {"$limit": 400},
    ], allowDiskUse=True))
    today = _since(1)
    for r in rows:
        score = 0.0
        for p in r["rows"]:
            w = SIGHT_W.get(p["t"], 1) * p["n"]
            d = p["d"] if p["d"].tzinfo else p["d"].replace(tzinfo=UTC)
            if boost:   # the last 3 days count up to 3x, fading with age
                age = (today - d).days
                w *= 1 + max(0, 3 - age) * 2 / 3
            score += w
        r["score"] = round(score, 1)
    rows.sort(key=lambda r: (-r["score"], -r["total"]))
    rows = rows[:limit]
    info = {v["_id"]: v for v in db.vulnerabilities.find({"_id": {"$in": [r["_id"] for r in rows]}}, VULN_CARD)}
    out = []
    for r in rows:
        series, counts = defaultdict(int), {k: 0 for k in gcve.SUMMARY_KEY.values()}
        for p in r["rows"]:
            series[p["d"].strftime("%Y-%m-%d")] += p["n"]
            counts[gcve.SUMMARY_KEY.get(p["t"], "seen")] += p["n"]
        card = _vuln_card(info.get(r["_id"], {"_id": r["_id"]}))
        card.update(score=r["score"], total=r["total"], last=r["last"], series=dict(series), counts=counts)
        out.append(card)
    return out


@app.get("/api/vulns/trending")
def vuln_trending(days: int = Query(7, ge=1, le=90), type: str = "", min: int = Query(1, ge=1, le=50),
                  boost: bool = False, limit: int = Query(30, ge=1, le=100)):
    db = get_db()
    return J(cached(f"trend:{days}:{type}:{min}:{boost}:{limit}", 30,
                    lambda: _trending(db, days, limit, type or None, min, boost)))


@app.get("/api/vulns/charts")
def vuln_charts(days: int = Query(30, ge=1, le=365)):
    """The db.gcve.eu 'Charts' tab rebuilt from MongoDB: top sighted per type, daily volume, KEV mix."""
    db = get_db()

    def build():
        since = _since(days)
        f = list(db.gcve_sightings.aggregate([{"$match": {"date": {"$gte": since}}}, {"$facet": {
            **{f"top_{k}": [{"$match": {"type": t}}, {"$group": {"_id": "$vulnerability", "n": {"$sum": "$count"}}},
                            {"$sort": {"n": -1, "_id": -1}}, {"$limit": 10}] for t, k in gcve.SUMMARY_KEY.items()},
            "per_day": [{"$group": {"_id": {"d": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}},
                                            "t": "$type"}, "n": {"$sum": "$count"}}}],
            "totals": [{"$group": {"_id": "$type", "n": {"$sum": "$count"}}}],
            "distinct": [{"$group": {"_id": "$vulnerability"}}, {"$count": "n"}],
        }}], allowDiskUse=True))
        f = f[0] if f else {}
        top_ids = {x["_id"] for k in gcve.SUMMARY_KEY.values() for x in f.get(f"top_{k}", [])}
        info = {v["_id"]: v for v in db.vulnerabilities.find({"_id": {"$in": list(top_ids)}}, VULN_CARD)}
        sighted = {"sighting_summary": {"$exists": True}}
        v = list(db.vulnerabilities.aggregate([{"$facet": {
            "kev_by_catalog": [{"$unwind": "$kev_catalogs"}, {"$group": {"_id": "$kev_catalogs.catalog", "n": {"$sum": 1}}},
                               {"$sort": {"n": -1}}],
            "kev_total": [{"$match": KEV_ANY}, {"$count": "n"}],
            "severity": [{"$match": {**sighted, "gcve.cvss.severity": {"$exists": True}}},
                         {"$group": {"_id": "$gcve.cvss.severity", "n": {"$sum": 1}}}],
            "cwes": [{"$match": sighted}, {"$unwind": "$gcve.cwes"}, {"$group": {"_id": "$gcve.cwes", "n": {"$sum": 1}}},
                     {"$sort": {"n": -1}}, {"$limit": 10}],
            "vendors": [{"$match": sighted}, {"$unwind": "$gcve.affected"},
                        {"$match": {"gcve.affected.vendor": {"$nin": ["", "n/a", "unknown"]}}},
                        {"$group": {"_id": {"$toLower": "$gcve.affected.vendor"}, "name": {"$first": "$gcve.affected.vendor"},
                                    "ids": {"$addToSet": "$_id"}}},
                        {"$project": {"name": 1, "n": {"$size": "$ids"}}}, {"$sort": {"n": -1}}, {"$limit": 10}],
            "enriched": [{"$match": {"gcve": {"$exists": True}}}, {"$count": "n"}],
        }}]))[0]
        src = db.sources.find_one({"_id": "gcve"}, {"stats": 1, "name": 1, "reliability": 1, "homepage": 1}) or {}
        top = lambda k: [{"id": x["_id"], "n": x["n"], **{kk: vv for kk, vv in _vuln_card(info.get(x["_id"], {"_id": x["_id"]})).items()
                                                          if kk in ("title", "cvss", "kev", "epss")}} for x in f.get(f"top_{k}", [])]
        return {
            "days": days, "since": since,
            "totals": {x["_id"]: x["n"] for x in f.get("totals", [])},
            "distinct_vulns": (f.get("distinct") or [{"n": 0}])[0]["n"] if f.get("distinct") else 0,
            "top": {t: top(k) for t, k in gcve.SUMMARY_KEY.items()},
            "per_day": [{"day": x["_id"]["d"], "type": x["_id"]["t"], "n": x["n"]} for x in f.get("per_day", [])],
            "kev_by_catalog": [{"catalog": x["_id"], "n": x["n"]} for x in v["kev_by_catalog"]],
            "kev_total": (v["kev_total"] or [{"n": 0}])[0]["n"],
            "severity": {x["_id"]: x["n"] for x in v["severity"] if x["_id"]},
            "cwes": [{"cwe": x["_id"], "n": x["n"]} for x in v["cwes"]],
            "vendors": [{"vendor": x["name"], "n": x["n"]} for x in v["vendors"]],
            "enriched": (v["enriched"] or [{"n": 0}])[0]["n"],
            "source": {"name": src.get("name"), "reliability": src.get("reliability"), "homepage": src.get("homepage"),
                       **(src.get("stats") or {})},
            "labels": gcve.SIGHTING_LABELS,
        }

    return J(cached(f"vuln_charts:{days}", 30, build))


@app.get("/api/vulns")
def vuln_list(q: str = "", filter: str = "", sort: str = "sightings", page: int = Query(1, ge=1),
              limit: int = Query(25, ge=1, le=100)):
    """Browse the vulnerabilities collection (exploited / KEV / critical filters)."""
    db = get_db()
    filt = {"$or": [{"gcve": {"$exists": True}}, {"sighting_summary": {"$exists": True}}, KEV_ANY]}
    conds = [filt]
    if q.strip():
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        conds.append({"$or": [{"_id": rx}, {"gcve.title": rx}, {"gcve.affected.vendor": rx}, {"gcve.affected.product": rx}]})
    if filter == "exploited":
        conds.append({"$or": [{"sighting_summary.exploited": {"$gt": 0}}, {"sighting_summary.confirmed": {"$gt": 0}}]})
    elif filter == "kev":
        conds.append(KEV_ANY)
    elif filter == "poc":
        conds.append({"sighting_summary.poc": {"$gt": 0}})
    elif filter == "critical":
        conds.append({"gcve.cvss.score": {"$gte": 9}})
    query = {"$and": conds}
    order = {"sightings": [("sighting_summary.total", -1), ("_id", -1)], "cvss": [("gcve.cvss.score", -1), ("_id", -1)],
             "epss": [("gcve.epss.score", -1), ("_id", -1)], "newest": [("gcve.published", -1), ("_id", -1)]}.get(sort)
    order = order or [("sighting_summary.total", -1), ("_id", -1)]
    total = db.vulnerabilities.count_documents(query)
    rows = list(db.vulnerabilities.find(query, VULN_CARD).sort(order).skip((page - 1) * limit).limit(limit))
    return J({"total": total, "page": page, "limit": limit, "results": [_vuln_card(v) for v in rows]})


@app.get("/api/vulns/{vid}")
async def vuln_detail(vid: str, refresh: bool = False):  # refresh kept for API compatibility
    """Vulnerability dossier: MongoDB (sightings series, reports, KEV) + a live db.gcve.eu lookup."""
    vid = gcve.norm_id(vid)
    if not gcve.VULN_ID_RX.match(vid):
        raise HTTPException(400, "Use an id like CVE-2024-3400, GCVE-1-2026-20117 or a GHSA id.")
    db = get_db()
    doc = await run_in_threadpool(db.vulnerabilities.find_one, {"_id": vid}) or {"_id": vid}
    g = doc.get("gcve") or {}
    live = None
    if True:   # always ask db.gcve.eu live (record, sightings, KEV, EPSS); MongoDB is the fallback
        try:
            live = await gcve.live_vulnerability(vid)
        except Exception as exc:  # the dossier still works from MongoDB alone
            live = {"record": None, "sightings": [], "kev": [], "errors": [f"{type(exc).__name__}: {str(exc)[:120]}"]}
        upd = {}
        if live.get("record"):
            g = {**live["record"], "fetched_at": datetime.now(UTC)}
            upd["gcve"] = g
        if live.get("kev"):
            seen, entries = set(), []
            for e in live["kev"]:
                if e["catalog"] not in seen:
                    seen.add(e["catalog"])
                    entries.append(e)
            upd["kev_catalogs"] = entries
            doc["kev_catalogs"] = entries
        if upd:
            await run_in_threadpool(db.vulnerabilities.update_one, {"_id": vid},
                                    {"$set": upd, "$setOnInsert": {"first_seen": datetime.now(UTC), "report_count": 0}}, True)

    def local():
        series = list(db.gcve_sightings.aggregate([
            {"$match": {"vulnerability": vid}},
            {"$group": {"_id": {"d": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date"}}, "t": "$type"},
                        "n": {"$sum": "$count"}}}, {"$sort": {"_id.d": 1}}]))
        smap = _sources_map(db)
        reps = list(db.reports.find({"cve_ids": vid}, CARD_FIELDS).sort("published_at", -1).limit(12))
        return series, [_report_card(r, smap) for r in reps], db.reports.count_documents({"cve_ids": vid})

    series, reps, n_reps = await run_in_threadpool(local)
    sightings = (live or {}).get("sightings") or []
    sightings.sort(key=lambda s: s.get("at") or datetime.min.replace(tzinfo=UTC), reverse=True)
    exploited = bool(doc.get("kev") or doc.get("kev_catalogs") or (doc.get("sighting_summary") or {}).get("exploited")
                     or any(s.get("type") in ("exploited", "confirmed") for s in sightings))
    return J({
        "id": vid, "gcve": g or None, "found": bool(g), "kev": doc.get("kev"), "kev_catalogs": doc.get("kev_catalogs", []),
        "summary": doc.get("sighting_summary"), "series": [{"day": x["_id"]["d"], "type": x["_id"]["t"], "n": x["n"]} for x in series],
        "sightings": sightings[:100], "reports": reps, "report_count": n_reps, "exploited": exploited,
        "live": bool(live and live.get("record")), "errors": (live or {}).get("errors", []),
        "gcve_url": gcve.vuln_url(vid), "labels": gcve.SIGHTING_LABELS,
    })


# ======================================================================== Lab 7.1
@app.get("/api/lab71/schema")
def lab71_schema(fresh: bool = False):
    db = get_db()

    def build():
        names = set(db.list_collection_names())
        counts = {c: db[c].estimated_document_count() for c in labs.PLATFORM_COLLECTIONS if c in names}
        return {"relationships": labs.relationships(), "detected": labs.detect_relationships(db, sample=80),
                "bson": labs.bson_stats(db), "counts": counts, "limit_bytes": config.BSON_LIMIT,
                "computed_at": datetime.now(UTC)}

    if fresh:
        _cache.pop("lab71", None)
    return J(cached("lab71", 120, build))


@app.get("/api/lab71/activity")
def lab71_activity():
    return J(get_db().lab_results.find_one({"_id": "activity_7_1"}) or {})


@app.post("/api/lab71/activity/run")
async def lab71_run():
    return J(await run_in_threadpool(labs.run_activity_71, get_db(), 150, True))


# ======================================================================== Lab 7.2
@app.get("/api/lab72/cache")
def lab72_cache():
    return J(labs.cache_snapshot(client(), get_lab_db()))


@app.get("/api/lab72/metrics")
def lab72_metrics(limit: int = Query(180, le=2000)):
    rows = list(get_db().cache_metrics.find({}, {"_id": 0}).sort("$natural", -1).limit(limit))
    return J(list(reversed(rows)))


@app.get("/api/lab72/results")
def lab72_results():
    return J(get_db().lab_results.find_one({"_id": "lab_7_2"}) or {})


@app.post("/api/lab72/random-reads")
async def lab72_reads(payload: dict = Body(default={})):
    seconds = max(4, min(int(payload.get("seconds", 16)), 60))
    return J(await run_in_threadpool(labs.random_reads, client(), get_lab_db(), "ws_reports", seconds, 2.0))


app.include_router(labs_router)


# ======================================================================== frontend (must be last)
# Lab 15: the TaskTracker SaaS product (its own FastAPI app, Swagger UI at /tasktracker/docs)
try:
    from .lab15 import TT as _tasktracker
    app.mount("/tasktracker", _tasktracker.app)
except Exception as _exc:                            # noqa: BLE001 - the website must start even without Lab 15
    print(f"[lab15] TaskTracker not mounted: {_exc}")

app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIR), html=True), name="frontend")

"""Lab 18 - Comparative capstone: identifying false positives from the THREAT INTELLIGENCE service in MongoDB, Cassandra
and Neo4j.

    Data       100,000 recent threats from many sources: every real sighting of the Lab 7 Kavach platform (indicators x
               feeds) first, topped up with generated sightings from 12 simulated feeds (known-good infrastructure reported
               by sloppy feeds, uncorroborated noise, coordinated campaigns) so the false-positive detector can be scored.
    Schemas    MongoDB   sources {recent_threats: last 50 embedded} + full_threats, index (from_source, timestamp)
               Cassandra threats_by_source + threats_by_type (the mentor's two tables) + the tables the other queries need
               Neo4j     (:Source)-[:REPORTED]->(:Indicator)-[:OF_FAMILY]->(:Family), SUSPICIOUS between feeds that share FPs
    Lab 18.1   the same system in all three, 4 benchmark queries (threat history, target and spread, daily volume
               analytics, real-time insert throughput) x 10 runs, median reported; the false-positive detector in each.
    Activity   communities of threats with unusually high inter-connectivity in Neo4j (Louvain), cross-referenced with
               the MongoDB documents.
    Lab 18.2   the comparative report (labs/lab18/make_report.py): schemas, latency table, scalability 1M/10M/100M,
               ergonomics, operations, recommendation per access pattern.
    Applied    (mentor's rule) the verdicts go back to the Lab 7 platform: cti_platform.fp_verdicts.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import threading
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymongo import ASCENDING, DESCENDING, UpdateOne

from . import polyglot as PG

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "labs" / "lab18"
RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
HEAVY = threading.Lock()
DB, KS = "lab18", "lab18"
DAYS = 30

ALLOWLIST = {  # known-good infrastructure that feeds keep reporting by mistake
    "8.8.8.8": "Google public DNS", "8.8.4.4": "Google public DNS", "1.1.1.1": "Cloudflare DNS", "1.0.0.1": "Cloudflare DNS",
    "9.9.9.9": "Quad9 DNS", "208.67.222.222": "OpenDNS", "google.com": "top domain", "microsoft.com": "top domain",
    "windowsupdate.com": "OS updates", "github.com": "code hosting", "cloudflare.com": "CDN", "amazonaws.com": "cloud provider",
    "office.com": "productivity suite", "apple.com": "top domain", "akamaiedge.net": "CDN", "googleapis.com": "cloud APIs",
    "s3.amazonaws.com": "cloud storage", "login.microsoftonline.com": "identity provider", "ocsp.digicert.com": "certificate status",
    "time.windows.com": "NTP", "pool.ntp.org": "NTP", "update.googleapis.com": "software updates", "dropbox.com": "file sharing",
    "zoom.us": "video calls"}
FAMILIES = ["Emotet", "QakBot", "AgentTesla", "RedLine", "Lumma", "AsyncRAT", "Remcos", "CobaltStrike", "Mirai", "Gafgyt",
            "LockBit", "BlackCat", "IcedID", "Formbook", "NanoCore", "SmokeLoader", "Vidar", "Raccoon", "Sliver", "Pikabot"]
TYPES = {"botnet_cc": ["Mirai", "Gafgyt", "Emotet", "QakBot"], "malware_download": ["SmokeLoader", "Formbook", "AgentTesla", "Pikabot"],
         "phishing": ["Lumma", "RedLine", "Vidar"], "c2": ["CobaltStrike", "Sliver", "AsyncRAT", "Remcos", "NanoCore"],
         "ransomware": ["LockBit", "BlackCat"], "stealer": ["Raccoon", "RedLine", "Vidar", "Lumma"], "scanner": ["IcedID"]}
FAMILY_TYPE = {f: t for t, fs in TYPES.items() for f in fs}
SECTORS = ["finance", "healthcare", "government", "telecom", "energy", "education", "retail", "manufacturing"]
COUNTRIES = ["US", "RU", "CN", "NL", "DE", "IN", "BR", "SG", "UA", "FR", "GB", "VN"]
# simulated feeds: (name, reliability = probability that a sighting is a real threat, allowlist-mistake rate, noise rate)
FEEDS = [("abuse-collective", .97, .000, .01), ("spamhaus-like", .96, .000, .01), ("cert-in-advisories", .95, .001, .01),
         ("honeypot-net", .90, .004, .05), ("sandbox-detonations", .92, .006, .03), ("osint-pastebin", .70, .03, .25),
         ("community-misp", .88, .006, .06), ("vendor-telemetry", .93, .002, .03), ("isac-finance", .94, .001, .02),
         ("free-blocklist-x", .60, .05, .30), ("free-blocklist-y", .62, .05, .28), ("dark-web-scraper", .75, .01, .20)]


def _jd(x):
    return json.loads(json.dumps(x, default=str))


def _statements(text: str, comment: str) -> list[str]:
    # remove comment lines FIRST (a comment may contain ';'), then split into statements
    code = "\n".join(l for l in text.splitlines() if l.strip() and not l.strip().startswith(comment))
    return [c.strip() for c in code.split(";") if c.strip()]


# ============================================================================ the 100,000 threats
def _rand_ip(rnd):
    while True:
        ip = ipaddress.IPv4Address(rnd.randint(0x0B000000, 0xDF000000))
        if ip.is_global:
            return str(ip)


def _rand_domain(rnd):
    syl = ["ka", "zu", "mo", "ri", "te", "xo", "lan", "per", "vix", "qua", "dor", "sen", "tal", "bri", "nox", "fy"]
    return "".join(rnd.choice(syl) for _ in range(rnd.randint(2, 4))) + rnd.choice([".com", ".net", ".xyz", ".top", ".ru", ".info", ".online", ".site"])


def real_sightings(limit: int) -> list[dict]:
    """Every sighting of the Lab 7 platform: (indicator, feed, observed time, confidence, threat label)."""
    try:
        db = PG.mongo()["cti_platform"]
        pipe = [{"$sort": {"observed_at": -1}}, {"$limit": limit},
                {"$lookup": {"from": "indicators", "localField": "indicator_id", "foreignField": "_id", "as": "i"}},
                {"$unwind": "$i"}, {"$project": {"_id": 0, "source": "$source_id", "value": "$i.value", "type": "$i.type",
                                                 "confidence": 1, "observed_at": 1, "threat": "$detail.threat", "tags": "$i.tags"}}]
        return [r for r in db.sightings.aggregate(pipe, allowDiskUse=True) if r.get("value") and r.get("type") in
                ("ipv4", "ipv6", "domain", "url", "sha256", "md5", "sha1", "cidr")]
    except Exception:                                 # noqa: BLE001
        return []


def generate(n: int = 100_000, seed: int = 18) -> dict:
    rnd = random.Random(seed)
    now = datetime.now(UTC).replace(microsecond=0)
    out, truth = [], {}
    asn_of = {}

    fam_of: dict = {}

    def rec(ind, ityp, src, ts, fam, conf, real=False, label="malicious", ttype=None):
        fam = fam_of.setdefault(ind, fam)              # an indicator belongs to one family (its first attribution)
        if ind not in asn_of:
            asn_of[ind] = (f"AS{rnd.choice([13335, 16509, 14061, 24940, 9009, 51167, 49981, 20473, 45102, 4134, 12389, 8075] + list(range(60000, 60040)))}",
                           rnd.choice(COUNTRIES))
        asn, ctry = asn_of[ind]
        out.append({"threat_id": str(uuid.UUID(int=rnd.getrandbits(128), version=4)), "indicator": ind, "ind_type": ityp,
                    "from_source": src, "threat_type": ttype or FAMILY_TYPE.get(fam, "malware_download"), "family": fam, "asn": asn,
                    "country": ctry, "sector": rnd.choice(SECTORS), "confidence": int(conf), "timestamp": ts, "real": real})
        truth.setdefault(ind, label)
    # 1. real sightings of the Kavach platform (Lab 7)
    real = real_sightings(min(n // 2, 40_000))
    for r in real:
        ts = r.get("observed_at")
        ts = ts if isinstance(ts, datetime) and (now - ts.replace(tzinfo=UTC)).days < DAYS else now - timedelta(seconds=rnd.randint(0, DAYS * 86400))
        thr = str(r.get("threat") or "").lower()
        fam = next((f for f in FAMILIES if f.lower() in thr or f.lower() in " ".join(r.get("tags") or []).lower()), rnd.choice(FAMILIES))
        rec(r["value"], {"ipv4": "ip", "ipv6": "ip"}.get(r["type"], r["type"]), f"kavach:{r['source']}", ts.replace(tzinfo=UTC),
            fam, r.get("confidence") or 60, real=True, label="unlabelled")
    # 2. generated sightings from the 12 simulated feeds
    pool = []
    for i in range(22000):
        t = rnd.random()
        ind, it = (_rand_ip(rnd), "ip") if t < .55 else (_rand_domain(rnd), "domain") if t < .80 else \
            (f"http://{_rand_domain(rnd)}/{rnd.choice(['login', 'invoice', 'update', 'gate.php', 'dl'])}", "url") if t < .92 else \
            (f"{rnd.getrandbits(256):064x}", "sha256")
        pool.append((ind, it, rnd.choice(FAMILIES)))
    cum, acc = [], 0.0
    for k in range(len(pool)):                         # a few indicators are reported by many feeds (Zipf-like)
        acc += 1 / (k + 1) ** 0.75
        cum.append(acc)
    fcum = [sum(f[1] for f in FEEDS[:k + 1]) for k in range(len(FEEDS))]
    campaigns = []
    for c in range(8):                                # coordinated campaigns: same family, many feeds, the same hour
        fam = rnd.choice(FAMILIES)
        members = [(_rand_domain(rnd) if rnd.random() < .5 else _rand_ip(rnd)) for _ in range(rnd.randint(12, 30))]
        campaigns.append({"family": fam, "members": members, "start": now - timedelta(hours=rnd.randint(2, 70))})
    for c in campaigns:
        feeds = rnd.sample([f[0] for f in FEEDS[:9]], rnd.randint(3, 6))
        for f in feeds:
            for m in c["members"]:
                rec(m, "domain" if "." in m and not m[0].isdigit() else "ip", f, c["start"] + timedelta(minutes=rnd.randint(0, 55), seconds=rnd.randint(0, 59)),
                    c["family"], rnd.randint(70, 95), label="malicious")
    allow = list(ALLOWLIST)
    while len(out) < n:
        f = rnd.choices(FEEDS, cum_weights=fcum)[0]
        name, rel, mistake, noise = f
        ts = now - timedelta(seconds=rnd.randint(0, DAYS * 86400))
        u = rnd.random()
        if u < mistake * 6:                            # known-good infrastructure reported by mistake
            ind = rnd.choice(allow)
            rec(ind, "ip" if ind[0].isdigit() else "domain", name, ts, rnd.choice(FAMILIES), rnd.randint(30, 70), label="benign")
        elif u < mistake * 6 + noise:                  # uncorroborated noise: seen by this feed only, low confidence
            ind = _rand_ip(rnd) if rnd.random() < .7 else _rand_domain(rnd)
            rec(ind, "ip" if ind[0].isdigit() else "domain", name, ts, rnd.choice(FAMILIES), rnd.randint(20, 55), label="noise")
        else:
            ind, it, fam = rnd.choices(pool, cum_weights=cum)[0]
            rec(ind, it, name, ts, fam, rnd.randint(55, 98) if rel > .85 else rnd.randint(40, 80), label="malicious")
    out = out[:n]
    labels = Counter(truth[o["indicator"]] for o in out)
    return {"threats": out, "truth": truth, "real": sum(o["real"] for o in out), "generated": sum(not o["real"] for o in out),
            "indicators": len({o["indicator"] for o in out}), "sources": sorted({o["from_source"] for o in out}),
            "labels_by_sighting": dict(labels), "campaigns": [{"family": c["family"], "size": len(c["members"]), "members": c["members"]} for c in campaigns]}


# ============================================================================ schemas + loading
def ensure_schema(log=print):
    s = PG.cassandra()
    for st in _statements((LAB / "schema.cql").read_text(), "--"):
        s.execute(st)
    for st in _statements((LAB / "schema.cypher").read_text(), "//"):
        PG.cypher_write(st)
    db = PG.mongo()[DB]
    db.full_threats.create_index([("from_source", ASCENDING), ("timestamp", DESCENDING)])
    db.full_threats.create_index([("indicator", ASCENDING), ("timestamp", DESCENDING)])
    db.full_threats.create_index([("family", ASCENDING), ("timestamp", DESCENDING)])
    db.full_threats.create_index([("timestamp", DESCENDING), ("threat_type", ASCENDING)])
    log("   schemas ready (MongoDB indexes, Cassandra keyspace lab18 with 7 tables, Neo4j constraints + relationship indexes)")


def _since(days: int) -> datetime:
    """Midnight UTC `days` days ago - every database uses the same whole-day window, so the answers can be compared."""
    d = (datetime.now(UTC) - timedelta(days=days)).date()
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _month(ts: datetime) -> str:
    return ts.strftime("%Y-%m")


def load_mongo(threats: list[dict]) -> dict:
    db = PG.mongo()[DB]
    t0 = time.perf_counter()
    db.full_threats.delete_many({}); db.sources.delete_many({}); db.allowlist.delete_many({})
    for i in range(0, len(threats), 10000):
        db.full_threats.insert_many([{**t, "_id": t["threat_id"]} for t in threats[i:i + 10000]], ordered=False)
    db.allowlist.insert_many([{"_id": k, "reason": v} for k, v in ALLOWLIST.items()])
    rebuild_recent()      # sources with the embedded recent_threats array (last 50), built by the server ($topN, MongoDB 5.2+)
    return {"seconds": round(time.perf_counter() - t0, 2), "documents": db.full_threats.estimated_document_count(),
            "sources": db.sources.count_documents({})}


def _cass_rows(t):
    ts = t["timestamp"]
    tid = uuid.UUID(t["threat_id"])
    d = ts.date()
    return ((t["from_source"], _month(ts), ts, tid, t["indicator"], t["ind_type"], t["threat_type"], t["family"], t["confidence"]),
            (t["threat_type"], d, ts, tid, t["from_source"], t["indicator"], t["family"], t["confidence"]),
            (t["indicator"], ts, tid, t["from_source"], t["family"], t["threat_type"], t["confidence"]),
            (t["family"], d, ts, tid, t["from_source"], t["indicator"], t["confidence"]))


CQL_INS = [f"INSERT INTO {KS}.threats_by_source (source, month, ts, threat_id, indicator, ind_type, threat_type, family, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
           f"INSERT INTO {KS}.threats_by_type (threat_type, day, ts, threat_id, source, indicator, family, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
           f"INSERT INTO {KS}.threats_by_indicator (indicator, ts, threat_id, source, family, threat_type, confidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
           f"INSERT INTO {KS}.threats_by_family (family, day, ts, threat_id, source, indicator, confidence) VALUES (?, ?, ?, ?, ?, ?, ?)"]
CQL_COUNTER = f"UPDATE {KS}.daily_volume SET n = n + ? WHERE month = ? AND day = ? AND threat_type = ?"
CQL_INDSRC = f"UPDATE {KS}.indicator_sources SET sources = sources + ?, max_conf = ?, ind_type = ?, family = ? WHERE indicator = ?"


def load_cassandra(threats: list[dict]) -> dict:
    s = PG.cassandra()
    t0 = time.perf_counter()
    for tbl in ("threats_by_source", "threats_by_type", "threats_by_indicator", "threats_by_family", "daily_volume", "indicator_sources", "allowlist"):
        s.execute(f"TRUNCATE {KS}.{tbl}", timeout=120)
    _docker(["exec", "cassandra", "nodetool", "clearsnapshot", "--all"])      # TRUNCATE keeps a snapshot (auto_snapshot)
    rows = [_cass_rows(t) for t in threats]
    n = 0
    for k, cql in enumerate(CQL_INS):
        n += PG.bulk(cql, [r[k] for r in rows], concurrency=48)
    vol = Counter((_month(t["timestamp"]), t["timestamp"].date(), t["threat_type"]) for t in threats)
    PG.bulk(CQL_COUNTER, [(c, m, d, ty) for (m, d, ty), c in vol.items()], concurrency=32)
    agg: dict = {}
    for t in threats:
        a = agg.setdefault(t["indicator"], [set(), 0, t["ind_type"], t["family"]])
        a[0].add(t["from_source"]); a[1] = max(a[1], t["confidence"])
    PG.bulk(CQL_INDSRC, [(v[0], v[1], v[2], v[3], k) for k, v in agg.items()], concurrency=48)
    PG.bulk(f"INSERT INTO {KS}.allowlist (indicator, reason) VALUES (?, ?)", list(ALLOWLIST.items()))
    return {"seconds": round(time.perf_counter() - t0, 2), "rows": n, "counters": len(vol), "indicator_rows": len(agg)}


def _neo_rows(t):
    return {"id": t["threat_id"], "source": t["from_source"], "indicator": t["indicator"], "ts": t["timestamp"].isoformat(),
            "type": t["threat_type"], "conf": t["confidence"]}


NEO_INS = ("UNWIND $rows AS r MATCH (s:Source {name: r.source}), (i:Indicator {value: r.indicator}) "
           "CREATE (s)-[:REPORTED {threat_id: r.id, ts: datetime(r.ts), day: date(datetime(r.ts)), type: r.type, confidence: r.conf}]->(i)")
NEO_IND = ("UNWIND $rows AS r MERGE (i:Indicator {value: r.v}) ON CREATE SET i.type = r.t, i.family = r.f, i.asn = r.a, i.country = r.c, "
           "i.allowlisted = r.al WITH i, r MERGE (f:Family {name: r.f}) MERGE (i)-[:OF_FAMILY]->(f) MERGE (a:ASN {asn: r.a}) MERGE (i)-[:IN_ASN]->(a)")


def load_neo4j(threats: list[dict]) -> dict:
    t0 = time.perf_counter()
    while PG.cypher("MATCH ()-[r:REPORTED]->() WITH r LIMIT 20000 DELETE r RETURN count(*) AS c")[0]["c"]:
        pass
    for lbl in ("Source", "Indicator", "Family", "ASN"):                 # delete in chunks (big transactions need memory)
        while PG.cypher(f"MATCH (n:{lbl}) WITH n LIMIT 5000 DETACH DELETE n RETURN count(*) AS c")[0]["c"]:
            pass
    srcs = sorted({t["from_source"] for t in threats})
    PG.cypher_write("UNWIND $s AS name CREATE (:Source {name: name})", s=srcs)
    inds = {}
    for t in threats:
        inds.setdefault(t["indicator"], {"v": t["indicator"], "t": t["ind_type"], "f": t["family"], "a": t["asn"], "c": t["country"],
                                         "al": t["indicator"] in ALLOWLIST})
    il = list(inds.values())
    for i in range(0, len(il), 5000):
        PG.cypher_write(NEO_IND, rows=il[i:i + 5000])
    for i in range(0, len(threats), 5000):
        PG.cypher_write(NEO_INS, rows=[_neo_rows(t) for t in threats[i:i + 5000]])
    return {"seconds": round(time.perf_counter() - t0, 2), "sources": len(srcs), "indicators": len(il), "relationships": len(threats)}


def load_all(n: int, log=print) -> dict:
    ensure_schema(log)
    g = generate(n)
    log(f"   {len(g['threats']):,} threats ({g['real']:,} real Kavach sightings + {g['generated']:,} generated), "
        f"{g['indicators']:,} indicators, {len(g['sources'])} sources")
    out = {"n": len(g["threats"]), "real": g["real"], "generated": g["generated"], "indicators": g["indicators"], "sources": g["sources"],
           "labels_by_sighting": g["labels_by_sighting"], "campaigns": [{k: c[k] for k in ("family", "size")} for c in g["campaigns"]]}
    out["mongodb"] = load_mongo(g["threats"]); log(f"   MongoDB   loaded in {out['mongodb']['seconds']} s")
    out["cassandra"] = load_cassandra(g["threats"]); log(f"   Cassandra loaded in {out['cassandra']['seconds']} s ({out['cassandra']['rows']:,} rows in 4 tables + counters)")
    _docker(["exec", "cassandra", "nodetool", "flush", KS])                  # let the node settle before measuring
    PG.cassandra_reset(); _PS.clear()
    PG.wait_for(("cassandra",), 300, log=log)
    out["neo4j"] = load_neo4j(g["threats"]); log(f"   Neo4j     loaded in {out['neo4j']['seconds']} s")
    PG.mongo()[DB].meta.replace_one({"_id": "truth"}, {"_id": "truth", "labels": [[k, v] for k, v in g["truth"].items() if v != "malicious"],
                                                        "campaigns": g["campaigns"], "at": datetime.now(UTC)}, upsert=True)
    return out


# ============================================================================ the 4 benchmark queries
def _tsk(x) -> str:
    """One timestamp format for all three drivers (UTC, milliseconds) - the Q1 answers are compared by time."""
    if hasattr(x, "to_native"):
        x = x.to_native()
    if x.tzinfo is not None:
        x = x.astimezone(UTC).replace(tzinfo=None)
    return x.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def q1_mongo(src):
    d = PG.mongo()[DB].sources.find_one({"_id": src}, {"recent_threats": 1})
    return [_tsk(t["timestamp"]) for t in (d or {}).get("recent_threats", [])]


def q1_mongo_full(src):
    return [_tsk(d["timestamp"]) for d in PG.mongo()[DB].full_threats.find({"from_source": src}, {"timestamp": 1}).sort("timestamp", -1).limit(50)]


_PS: dict = {}


def _resilient(fn):
    """Cassandra calls survive a node that was busy (GC / compaction after the 400,000-row load) or restarted:
    on NoHostAvailable / timeout -> new session, wait until the node answers again, retry (up to 3 times)."""
    import functools

    @functools.wraps(fn)
    def run(*a, **kw):
        from cassandra import OperationTimedOut, ReadTimeout, WriteTimeout
        from cassandra.cluster import NoHostAvailable
        for attempt in range(3):
            try:
                return fn(*a, **kw)
            except (NoHostAvailable, OperationTimedOut, ReadTimeout, WriteTimeout) as exc:
                if attempt == 2:
                    raise
                print(f"   (Cassandra not answering: {type(exc).__name__} - reconnecting and retrying)", flush=True)
                _PS.clear()
                PG.cassandra_reset()
                time.sleep(10)
                PG.wait_for(("cassandra",), 300, log=lambda *x: None)
    return run


def _ps(cql):
    if cql not in _PS:
        _PS[cql] = PG.cassandra().prepare(cql)
    return _PS[cql]


@_resilient
def q1_cass(src):
    s, now = PG.cassandra(), datetime.now(UTC)
    out = []
    for m in (_month(now), _month(now.replace(day=1) - timedelta(days=1))):
        out += [_tsk(r["ts"]) for r in s.execute(_ps(f"SELECT threat_id, ts FROM {KS}.threats_by_source WHERE source = ? AND month = ? LIMIT 50"), (src, m))]
        if len(out) >= 50:
            break
    return out[:50]


def q1_neo(src):
    return [_tsk(r["ts"]) for r in PG.cypher("MATCH (:Source {name: $s})-[r:REPORTED]->() RETURN r.threat_id AS id, r.ts AS ts ORDER BY r.ts DESC LIMIT 50", s=src)]


def q2_mongo(ind):
    db, since = PG.mongo()[DB], _since(7)
    hits = list(db.full_threats.find({"indicator": ind}, {"from_source": 1, "timestamp": 1, "family": 1}))
    if not hits:
        return {"sources": [], "spread": 0}
    srcs, fam = sorted({h["from_source"] for h in hits}), hits[0]["family"]
    r = list(db.full_threats.aggregate([{"$match": {"family": fam, "timestamp": {"$gte": since}, "from_source": {"$in": srcs}, "indicator": {"$ne": ind}}},
                                        {"$group": {"_id": "$indicator"}}, {"$count": "n"}]))
    return {"sources": srcs, "first": min(h["timestamp"] for h in hits), "spread": r[0]["n"] if r else 0}


@_resilient
def q2_cass(ind):
    s = PG.cassandra()
    hits = list(s.execute(_ps(f"SELECT source, family, ts FROM {KS}.threats_by_indicator WHERE indicator = ?"), (ind,)))
    if not hits:
        return {"sources": [], "spread": 0}
    srcs, fam = {h["source"] for h in hits}, hits[0]["family"]
    since = _since(7)
    days = [(datetime.now(UTC) - timedelta(days=k)).date() for k in range(8)]          # since.date() .. today
    futs = [s.execute_async(_ps(f"SELECT source, indicator, ts FROM {KS}.threats_by_family WHERE family = ? AND day = ?"), (fam, d)) for d in days]
    spread = set()
    for f in futs:
        for r in f.result():
            if r["source"] in srcs and r["indicator"] != ind and r["ts"].replace(tzinfo=UTC) >= since:
                spread.add(r["indicator"])
    return {"sources": sorted(srcs), "first": min(h["ts"] for h in hits), "spread": len(spread)}


Q2_CYPHER = ("MATCH (x:Indicator {value: $v})<-[r:REPORTED]-(s:Source) "
             "WITH x, collect(DISTINCT s.name) AS srcs, min(r.ts) AS first_seen "
             "OPTIONAL MATCH (x)-[:OF_FAMILY]->(:Family)<-[:OF_FAMILY]-(o:Indicator)<-[r2:REPORTED]-(s2:Source) "
             "WHERE o <> x AND s2.name IN srcs AND r2.ts >= datetime($since) "
             "RETURN srcs, first_seen, count(DISTINCT o) AS spread")


def q2_neo(ind):
    r = PG.cypher(Q2_CYPHER, v=ind, since=_since(7).isoformat())
    if not r:
        return {"sources": [], "spread": 0}
    return {"sources": sorted(r[0]["srcs"]), "first": r[0]["first_seen"], "spread": r[0]["spread"]}


def q3_mongo():
    since = _since(DAYS)
    rows = PG.mongo()[DB].full_threats.aggregate([
        {"$match": {"timestamp": {"$gte": since}}},
        {"$group": {"_id": {"day": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}, "type": "$threat_type"}, "n": {"$sum": 1}}}])
    return {(r["_id"]["day"], r["_id"]["type"]): r["n"] for r in rows}


@_resilient
def q3_cass():
    now = datetime.now(UTC)
    since = _since(DAYS).date()
    out = {}
    for m in sorted({_month(now), _month(now - timedelta(days=DAYS))}):
        for r in PG.cassandra().execute(_ps(f"SELECT day, threat_type, n FROM {KS}.daily_volume WHERE month = ?"), (m,)):
            d = r["day"].date() if hasattr(r["day"], "date") else r["day"]
            if d >= since:
                out[(str(d), r["threat_type"])] = r["n"]
    return out


def q3_neo():
    rows = PG.cypher("MATCH ()-[r:REPORTED]->() WHERE r.day >= date($since) RETURN toString(r.day) AS day, r.type AS type, count(*) AS n",
                     since=_since(DAYS).date().isoformat())
    return {(r["day"], r["type"]): r["n"] for r in rows}


def fresh_threats(n: int, seed: int) -> list[dict]:
    rnd = random.Random(seed)
    now = datetime.now(UTC).replace(microsecond=0)
    return [{"threat_id": str(uuid.UUID(int=rnd.getrandbits(128), version=4)), "indicator": f"bench-{seed}-{i}.example", "ind_type": "domain",
             "from_source": rnd.choice(FEEDS)[0], "threat_type": "phishing", "family": "Lumma", "asn": "AS13335", "country": "US",
             "sector": "finance", "confidence": rnd.randint(50, 95), "timestamp": now, "real": False, "bench": True} for i in range(n)]


def q4_mongo(batch):
    db = PG.mongo()[DB]
    db.full_threats.insert_many([{**t, "_id": t["threat_id"]} for t in batch], ordered=False)
    by = defaultdict(list)
    for t in batch:
        by[t["from_source"]].append({"threat_id": t["threat_id"], "indicator": t["indicator"], "threat_type": t["threat_type"],
                                     "family": t["family"], "confidence": t["confidence"], "timestamp": t["timestamp"]})
    db.sources.bulk_write([UpdateOne({"_id": s}, {"$push": {"recent_threats": {"$each": v, "$sort": {"timestamp": -1}, "$slice": 50}},
                                                  "$inc": {"total": len(v)}}, upsert=True) for s, v in by.items()], ordered=False)
    return len(batch)


@_resilient
def q4_cass(batch):
    from cassandra.concurrent import execute_concurrent
    s = PG.cassandra()
    stmts = []
    for t in batch:
        for k, row in enumerate(_cass_rows(t)):
            stmts.append((_ps(CQL_INS[k]), row))
        stmts.append((_ps(CQL_COUNTER), (1, _month(t["timestamp"]), t["timestamp"].date(), t["threat_type"])))
        stmts.append((_ps(CQL_INDSRC), ({t["from_source"]}, t["confidence"], t["ind_type"], t["family"], t["indicator"])))
    execute_concurrent(s, stmts, concurrency=64, raise_on_first_error=True)
    return len(batch)


def q4_neo(batch):
    PG.cypher_write(NEO_IND, rows=[{"v": t["indicator"], "t": t["ind_type"], "f": t["family"], "a": t["asn"], "c": t["country"], "al": False} for t in batch])
    PG.cypher_write(NEO_INS, rows=[_neo_rows(t) for t in batch])
    return len(batch)


def cleanup_bench(cass_batches: list[list[dict]]):
    """Remove the benchmark threats again so that every run starts from the same 100,000."""
    allb = [t for b in cass_batches for t in b]
    db = PG.mongo()[DB]
    db.full_threats.delete_many({"bench": True})
    rebuild_recent()
    PG.cypher_write("MATCH (i:Indicator) WHERE i.value STARTS WITH 'bench-' DETACH DELETE i")
    from cassandra.concurrent import execute_concurrent
    keys = [("threats_by_source", "source = ? AND month = ?"), ("threats_by_type", "threat_type = ? AND day = ?"),
            ("threats_by_indicator", "indicator = ?"), ("threats_by_family", "family = ? AND day = ?")]
    stmts = []
    for t in allb:
        ts, tid = t["timestamp"], uuid.UUID(t["threat_id"])
        pk = [(t["from_source"], _month(ts)), (t["threat_type"], ts.date()), (t["indicator"],), (t["family"], ts.date())]
        for (tbl, where), p in zip(keys, pk):
            stmts.append((_ps(f"DELETE FROM {KS}.{tbl} WHERE {where} AND ts = ? AND threat_id = ?"), (*p, ts, tid)))
        stmts.append((_ps(f"DELETE FROM {KS}.indicator_sources WHERE indicator = ?"), (t["indicator"],)))
    for (m, d, ty), c in Counter((_month(t["timestamp"]), t["timestamp"].date(), t["threat_type"]) for t in allb).items():
        stmts.append((_ps(f"UPDATE {KS}.daily_volume SET n = n - ? WHERE month = ? AND day = ? AND threat_type = ?"), (c, m, d, ty)))
    execute_concurrent(PG.cassandra(), stmts, concurrency=128, raise_on_first_error=False)


def rebuild_recent():
    PG.mongo()[DB].full_threats.aggregate([
        {"$group": {"_id": "$from_source", "total": {"$sum": 1},
                    "recent_threats": {"$topN": {"n": 50, "sortBy": {"timestamp": -1}, "output": {
                        "threat_id": "$_id", "indicator": "$indicator", "threat_type": "$threat_type", "family": "$family",
                        "confidence": "$confidence", "timestamp": "$timestamp"}}}}},
        {"$merge": {"into": "sources", "whenMatched": "replace"}}])


QUERY_TEXT = {
    "q1": {"title": "Threat history - latest 50 threats of a source",
           "mongodb": 'db.sources.find_one({"_id": src}, {"recent_threats": 1})          # embedded array, 1 document',
           "cassandra": "SELECT threat_id, ts FROM lab18.threats_by_source\n WHERE source = ? AND month = ? LIMIT 50;   -- this month (+ last month if < 50)",
           "neo4j": "MATCH (:Source {name: $s})-[r:REPORTED]->()\nRETURN r.threat_id ORDER BY r.ts DESC LIMIT 50"},
    "q2": {"title": "Target and spread - who reported an indicator, and how many related threats those feeds saw in 7 days",
           "mongodb": 'hits = db.full_threats.find({"indicator": ind})\ndb.full_threats.aggregate([{"$match": {"family": fam, "timestamp": {"$gte": since},\n'
                      '  "from_source": {"$in": srcs}, "indicator": {"$ne": ind}}}, {"$group": {"_id": "$indicator"}}, {"$count": "n"}])',
           "cassandra": "SELECT source, family, ts FROM lab18.threats_by_indicator WHERE indicator = ?;\n"
                        "SELECT source, indicator, ts FROM lab18.threats_by_family WHERE family = ? AND day = ?;  -- x 8 days, async\n"
                        "-- filter by source + distinct: in Python",
           "neo4j": Q2_CYPHER.replace(" WITH", "\nWITH").replace(" OPTIONAL", "\nOPTIONAL").replace(" WHERE", "\nWHERE").replace(" RETURN", "\nRETURN")},
    "q3": {"title": "Daily volume analytics - threats per day and type, last 30 days",
           "mongodb": 'db.full_threats.aggregate([{"$match": {"timestamp": {"$gte": since}}},\n  {"$group": {"_id": {"day": {"$dateToString": ...}, "type": "$threat_type"}, "n": {"$sum": 1}}}])',
           "cassandra": "SELECT day, threat_type, n FROM lab18.daily_volume WHERE month = ?;   -- counters kept up to date on every write",
           "neo4j": "MATCH ()-[r:REPORTED]->() WHERE r.day >= date($since)   // relationship index on day\nRETURN toString(r.day), r.type, count(*)"},
    "q4": {"title": "Real-time insert throughput - 1,000 new threats per run",
           "mongodb": "full_threats.insert_many(1000 docs)\nsources.bulk_write($push {$each, $sort, $slice: 50})   -- keep recent_threats current",
           "cassandra": "4 INSERTs (one per query table) + 1 counter UPDATE + 1 set UPDATE per threat\n-- execute_concurrent, 128 in flight",
           "neo4j": "UNWIND $rows MERGE (:Indicator) + Family/ASN links\nUNWIND $rows MATCH (s), (i) CREATE (s)-[:REPORTED]->(i)"}}


def benchmark(runs: int = 10, log=print) -> dict:
    """10 runs per query and database, a different parameter each run (the same 10 parameters for every database)."""
    db = PG.mongo()[DB]
    srcs = [d["_id"] for d in db.sources.find({}, {"_id": 1}).sort("total", -1).limit(runs)]
    while len(srcs) < runs:
        srcs += srcs
    inds = [d["_id"] for d in db.full_threats.aggregate([{"$group": {"_id": "$indicator", "n": {"$addToSet": "$from_source"}}},
                                                         {"$match": {"n.1": {"$exists": True}}}, {"$sample": {"size": runs}}])]
    res, check = {}, {}

    def measure(fn, params):
        fn(params[0])                                 # warm-up (connection, plan cache)
        ms, outs = [], []
        for p in params:
            t0 = time.perf_counter()
            outs.append(fn(p))
            ms.append((time.perf_counter() - t0) * 1000)
        return ms, outs

    def stat(ms):
        s = sorted(ms)
        return {"median_ms": round(statistics.median(s), 3), "p95_ms": round(s[-1] if len(s) < 20 else s[int(0.95 * (len(s) - 1))], 3),
                "min_ms": round(s[0], 3), "max_ms": round(s[-1], 3), "runs": len(s), "all_ms": [round(x, 3) for x in ms]}
    for key, fns, params in (("q1", (q1_mongo, q1_cass, q1_neo), srcs[:runs]), ("q2", (q2_mongo, q2_cass, q2_neo), inds[:runs]),
                             ("q3", (lambda _: q3_mongo(), lambda _: q3_cass(), lambda _: q3_neo()), [None] * runs)):
        res[key] = {}
        outs = {}
        for name, fn in zip(("mongodb", "cassandra", "neo4j"), fns):
            ms, o = measure(fn, params)
            res[key][name] = stat(ms)
            outs[name] = o
        if key == "q1":                                # same 50 newest threats (compared by their timestamps)?
            check[key] = all(sorted(outs["mongodb"][i]) == sorted(outs["cassandra"][i]) == sorted(outs["neo4j"][i]) for i in range(runs))
            res[key]["mongodb_full_collection"] = stat(measure(q1_mongo_full, params)[0])
        elif key == "q2":
            check[key] = all(outs["mongodb"][i]["sources"] == outs["cassandra"][i]["sources"] == outs["neo4j"][i]["sources"] and
                             outs["mongodb"][i]["spread"] == outs["cassandra"][i]["spread"] == outs["neo4j"][i]["spread"] for i in range(runs))
            res[key]["example"] = {"indicator": params[0], **_jd(outs["neo4j"][0])}
        else:
            a, b, c = outs["mongodb"][0], outs["cassandra"][0], outs["neo4j"][0]
            check[key] = sum(a.values()) == sum(b.values()) == sum(c.values())
            res[key]["totals"] = {"mongodb": sum(a.values()), "cassandra": sum(b.values()), "neo4j": sum(c.values()), "cells": len(a)}
        log(f"   {QUERY_TEXT[key]['title'][:52]:<52}" + "".join(f"  {n} {res[key][n]['median_ms']:>8.2f} ms" for n in ("mongodb", "cassandra", "neo4j"))
            + f"   same answer: {check[key]}")
    res["q4"] = {}
    batches = []
    for k, (name, fn) in enumerate((("mongodb", q4_mongo), ("cassandra", q4_cass), ("neo4j", q4_neo))):
        bl = [fresh_threats(1000, 9000 + 100 * k + i) for i in range(runs + 1)]    # +1: the warm-up batch
        batches.append(bl)
        fn(bl[-1])
        ms = []
        for b in bl[:runs]:
            t0 = time.perf_counter()
            fn(b)
            ms.append((time.perf_counter() - t0) * 1000)
        st = stat(ms)
        st["throughput_per_s"] = round(1000 / (st["median_ms"] / 1000), 1)
        res["q4"][name] = st
    log(f"   {QUERY_TEXT['q4']['title'][:52]:<52}" + "".join(f"  {n} {res['q4'][n]['throughput_per_s']:>8.0f}/s" for n in ("mongodb", "cassandra", "neo4j")))
    cleanup_bench(batches[1])                         # MongoDB/Neo4j by flag; Cassandra by primary key of ITS batches
    return {"results": res, "same_answer": check, "params": {"sources": srcs[:runs], "indicators": inds[:runs]}, "runs": runs, "text": QUERY_TEXT}


# ============================================================================ the use case: false-positive detection
def _fp_rows_mongo():
    db = PG.mongo()[DB]
    allow = {d["_id"] for d in db.allowlist.find({}, {"_id": 1})}
    rows = db.full_threats.aggregate([{"$group": {"_id": "$indicator", "sources": {"$addToSet": "$from_source"}, "max_conf": {"$max": "$confidence"}}}],
                                     allowDiskUse=True)
    return [(r["_id"], set(r["sources"]), r["max_conf"], r["_id"] in allow) for r in rows]


@_resilient
def _fp_rows_cass():
    s = PG.cassandra()
    allow = {r["indicator"] for r in s.execute(f"SELECT indicator FROM {KS}.allowlist")}
    from cassandra.query import SimpleStatement
    st = SimpleStatement(f"SELECT indicator, sources, max_conf FROM {KS}.indicator_sources", fetch_size=5000)
    return [(r["indicator"], set(r["sources"] or ()), r["max_conf"], r["indicator"] in allow) for r in s.execute(st)]


def _fp_rows_neo():
    rows = PG.cypher("MATCH (s:Source)-[r:REPORTED]->(i:Indicator) RETURN i.value AS v, collect(DISTINCT s.name) AS srcs, "
                     "max(r.confidence) AS mc, i.allowlisted AS al")
    return [(r["v"], set(r["srcs"]), r["mc"], bool(r["al"])) for r in rows]


def score_fp(rows) -> dict:
    """known-benign  : on the allowlist of known-good infrastructure.
       uncorroborated: reported by ONE source only, with confidence < 56, by a feed whose corroboration rate (share of its
                       indicators that another feed also reports) is below the median of all feeds."""
    per_src = defaultdict(lambda: [0, 0])
    for v, srcs, mc, al in rows:
        for s in srcs:
            per_src[s][1] += 1
            per_src[s][0] += len(srcs) >= 2
    rate = {s: c / t for s, (c, t) in per_src.items() if t}
    med = statistics.median(rate.values()) if rate else 0
    verdict = {}
    for v, srcs, mc, al in rows:
        if al:
            verdict[v] = "known-benign"
        elif len(srcs) == 1 and rate[next(iter(srcs))] < med and (mc or 0) < 56:
            verdict[v] = "uncorroborated"
    return {"verdict": verdict, "source_rate": rate, "median_rate": med}


def detect_fp(log=print) -> dict:
    out = {}
    verdicts = {}
    for name, fn in (("mongodb", _fp_rows_mongo), ("cassandra", _fp_rows_cass), ("neo4j", _fp_rows_neo)):
        t0 = time.perf_counter()
        rows = fn()
        t1 = time.perf_counter()
        sc = score_fp(rows)
        out[name] = {"fetch_ms": round((t1 - t0) * 1000, 1), "score_ms": round((time.perf_counter() - t1) * 1000, 1), "indicators": len(rows),
                     "flagged": len(sc["verdict"]), "by_reason": dict(Counter(sc["verdict"].values()))}
        verdicts[name] = sc
        log(f"   FP detection on {name:<9}: {len(rows):,} indicators read in {out[name]['fetch_ms']:,.0f} ms -> {len(sc['verdict']):,} flagged {out[name]['by_reason']}")
    same = verdicts["mongodb"]["verdict"] == verdicts["cassandra"]["verdict"] == verdicts["neo4j"]["verdict"]
    # graph only: feeds that share false positives (SUSPICIOUS) - a pattern match the other two cannot express
    t0 = time.perf_counter()
    PG.cypher_write("MATCH ()-[r:SUSPICIOUS]->() DELETE r")
    PG.cypher_write("MATCH (i:Indicator {allowlisted: true})<-[:REPORTED]-(s:Source) WITH i, collect(DISTINCT s) AS ss "
                    "UNWIND ss AS s1 UNWIND ss AS s2 WITH s1, s2, i WHERE s1.name < s2.name "
                    "WITH s1, s2, count(i) AS shared WHERE shared >= 3 MERGE (s1)-[r:SUSPICIOUS]-(s2) SET r.shared_benign = shared")
    sus = PG.cypher("MATCH (a:Source)-[r:SUSPICIOUS]-(b:Source) WHERE a.name < b.name RETURN a.name AS a, b.name AS b, r.shared_benign AS shared ORDER BY shared DESC")
    t_sus = round((time.perf_counter() - t0) * 1000, 1)
    deg = Counter()
    for r in sus:
        deg[r["a"]] += 1; deg[r["b"]] += 1
    # accuracy against the generator's ground truth (generated sightings only; the real ones are unlabelled)
    truth = {k: v for k, v in (PG.mongo()[DB].meta.find_one({"_id": "truth"}) or {}).get("labels", [])}
    v = verdicts["neo4j"]["verdict"]
    flagged = set(v)
    fp_true = {k for k, lab in truth.items() if lab in ("benign", "noise")}
    labelled_flagged = {k for k in flagged if truth.get(k) != "unlabelled"}      # real Kavach sightings carry no label
    tp = len(flagged & fp_true)
    benign = {k for k, lab in truth.items() if lab == "benign"}
    acc = {"true_false_positives": len(fp_true), "flagged": len(flagged), "flagged_labelled": len(labelled_flagged), "correct": tp,
           "precision": round(tp / max(1, len(labelled_flagged)), 3), "recall": round(tp / max(1, len(fp_true)), 3),
           "benign_recall": round(len(flagged & benign) / max(1, len(benign)), 3),
           "real_kavach_flagged": len(flagged) - len(labelled_flagged)}
    rates = verdicts["mongodb"]["source_rate"]
    feeds = sorted([{"source": s, "corroboration": round(r, 3), "suspicious_links": deg.get(s, 0)} for s, r in rates.items()],
                   key=lambda x: x["corroboration"])
    sample = sorted(flagged, key=lambda k: (v[k] != "known-benign", k))[:25]
    log(f"   identical verdicts in all three: {same}; precision {acc['precision']}, recall {acc['recall']} on the labelled sightings; "
        f"{len(sus)} SUSPICIOUS feed pairs (Neo4j, {t_sus} ms)")
    return {"per_db": out, "identical": same, "accuracy": acc, "feeds": feeds, "suspicious": sus, "suspicious_ms": t_sus,
            "sample": [{"indicator": k, "verdict": v[k], "reason": ALLOWLIST.get(k, "single low-quality source, low confidence")} for k in sample],
            "rules": score_fp.__doc__, "_verdict": v}


# ============================================================================ Activity: communities (Louvain) x MongoDB details
def communities(log=print) -> dict:
    import networkx as nx
    since = (datetime.now(UTC) - timedelta(days=4)).isoformat()
    t0 = time.perf_counter()
    # the Kavach (Lab 7) feeds are downloaded as snapshots: their timestamp is the download time, not the time the threat
    # was seen, so co-timing means nothing there -> the activity uses the feeds that report observation times
    rows = PG.cypher("MATCH (s:Source)-[r:REPORTED]->(i:Indicator) WHERE r.ts >= datetime($since) AND NOT s.name STARTS WITH 'kavach:' "
                     "RETURN s.name AS source, i.value AS v, i.family AS family, r.ts.epochSeconds AS t", since=since)
    by_src = defaultdict(list)
    for r in rows:
        by_src[r["source"]].append((r["t"], r["v"], r["family"]))
    w = Counter()
    for src, items in by_src.items():              # link = the same feed reports both indicators (same family) within 60 min
        items.sort()
        pairs = set()
        j = 0
        for i in range(len(items)):
            while items[i][0] - items[j][0] > 3600:
                j += 1
            if i - j > 300:                        # a burst of > 300 in one hour = a bulk import, not co-reporting
                continue
            for k in range(j, i):
                if items[k][2] == items[i][2] and items[k][1] != items[i][1]:
                    pairs.add(tuple(sorted((items[k][1], items[i][1]))))
        for p in pairs:                            # weight = number of DIFFERENT feeds that made this link
            w[p] += 1
    g = nx.Graph()
    for (a, b), c in w.items():
        if c >= 2:                                    # random co-occurrence happens once; campaigns repeat across feeds
            g.add_edge(a, b, weight=c)
    t_fetch = time.perf_counter() - t0
    gds = False
    try:
        PG.cypher("RETURN gds.version() AS v"); gds = True
    except Exception:                                 # noqa: BLE001
        pass
    comms = nx.community.louvain_communities(g, weight="weight", seed=18) if g.number_of_nodes() else []
    stats = []
    for k, c in enumerate(comms):
        if len(c) < 3:
            continue
        sg = g.subgraph(c)
        e = sg.number_of_edges()
        dens = nx.density(sg)
        freq = sum(d["weight"] for _, _, d in sg.edges(data=True)) / max(1, e)
        stats.append({"community": k, "size": len(c), "edges": e, "density": round(dens, 3), "avg_link_frequency": round(freq, 2),
                      "members": sorted(c)})
    if stats:
        md = statistics.mean(s["density"] * s["avg_link_frequency"] for s in stats)
        sd = statistics.pstdev(s["density"] * s["avg_link_frequency"] for s in stats) or 1
        for s in stats:
            s["z"] = round((s["density"] * s["avg_link_frequency"] - md) / sd, 2)
            # unusually high inter-connectivity: almost every pair linked (density >= 0.5) and each link repeated by
            # ~3 feeds on average (random co-reporting happens once) - an absolute rule, not "the top few of the list"
            s["flagged"] = s["density"] >= 0.5 and s["avg_link_frequency"] >= 2.5
    stats.sort(key=lambda s: (-s.get("z", 0), -s["size"]))
    PG.cypher_write("MATCH (i:Indicator) WHERE i.community IS NOT NULL REMOVE i.community")
    PG.cypher_write("UNWIND $rows AS r MATCH (i:Indicator {value: r.v}) SET i.community = r.c",
                    rows=[{"v": m, "c": s["community"]} for s in stats if s.get("flagged") for m in s["members"]])
    db = PG.mongo()[DB]
    camp = (db.meta.find_one({"_id": "truth"}) or {}).get("campaigns", [])
    flagged = [s for s in stats if s.get("flagged")][:20]
    for s in flagged:                                 # cross-reference with the MongoDB documents (the "transaction details")
        d = list(db.full_threats.aggregate([{"$match": {"indicator": {"$in": s["members"]}}},
                                            {"$group": {"_id": None, "sightings": {"$sum": 1}, "sources": {"$addToSet": "$from_source"},
                                                        "families": {"$addToSet": "$family"}, "types": {"$addToSet": "$threat_type"},
                                                        "first": {"$min": "$timestamp"}, "last": {"$max": "$timestamp"},
                                                        "avg_conf": {"$avg": "$confidence"}, "countries": {"$addToSet": "$country"}}}]))
        s["mongodb"] = _jd(d[0]) if d else {}
        if d:
            s["mongodb"]["avg_conf"] = round(d[0]["avg_conf"], 1)
            s["mongodb"]["window_minutes"] = round((d[0]["last"] - d[0]["first"]).total_seconds() / 60, 1)
        best = max(camp, key=lambda c: len(set(c["members"]) & set(s["members"])), default=None)
        s["matches_campaign"] = round(len(set(best["members"]) & set(s["members"])) / len(s["members"]), 2) if best else 0
        s["campaign_family"] = best["family"] if best else None
    found = sum(1 for c in camp if any(len(set(c["members"]) & set(s["members"])) >= 0.8 * len(c["members"]) for s in flagged))
    log(f"   co-reporting graph: {g.number_of_nodes():,} indicators, {g.number_of_edges():,} links (weight >= 2); Louvain -> {len(stats)} communities, "
        f"{len(flagged)} flagged; planted campaigns recovered: {found}/{len(camp)} (GDS plugin: {gds})")
    return {"nodes": g.number_of_nodes(), "edges": g.number_of_edges(), "communities": len(stats), "flagged": [{k: v for k, v in s.items() if k != "members"} |
            {"members": s["members"][:12]} for s in flagged], "campaigns_planted": len(camp), "campaigns_found": found, "gds": gds,
            "fetch_s": round(t_fetch, 2), "method": "Louvain (networkx) on the co-reporting graph read from Neo4j; ids written back as Indicator.community",
            "pass": found >= max(1, len(camp) - 2)}


# ============================================================================ Lab 18.2 inputs: sizes, scaling, ergonomics
def _docker(cmd: list[str]) -> str | None:
    if not shutil.which("docker"):
        return None
    try:
        p = subprocess.run(["docker"] + cmd, capture_output=True, text=True, timeout=120)
        return p.stdout if p.returncode == 0 else None
    except Exception:                                 # noqa: BLE001
        return None


def sizes(n: int) -> dict:
    out = {}
    st = PG.mongo()[DB].command("collStats", "full_threats")
    src = PG.mongo()[DB].command("collStats", "sources")
    out["mongodb"] = {"bytes": st["storageSize"] + st["totalIndexSize"] + src["storageSize"], "data": st["size"], "storage": st["storageSize"],
                      "indexes": st["totalIndexSize"], "avg_doc": st.get("avgObjSize"), "method": "collStats (compressed WiredTiger files)"}
    _docker(["exec", "cassandra", "nodetool", "flush", KS])
    t = _docker(["exec", "cassandra", "nodetool", "tablestats", KS])
    if t:
        live = sum(int(m) for m in re.findall(r"Space used \(live\): (\d+)", t))
        out["cassandra"] = {"bytes": live, "method": "nodetool tablestats - Space used (live), all lab18 tables (compressed SSTables)"}
    else:
        est = PG.cql_all(f"SELECT table_name, mean_partition_size, partitions_count FROM system.size_estimates WHERE keyspace_name = '{KS}'")
        b = sum(r["mean_partition_size"] * r["partitions_count"] for r in est)
        out["cassandra"] = {"bytes": b or None, "method": "system.size_estimates (approximate)"}
    t = _docker(["exec", "neo4j", "du", "-sb", "/data/databases/neo4j"])
    if t:
        out["neo4j"] = {"bytes": int(t.split()[0]), "method": "du -sb /data/databases/neo4j (store files, includes the Lab 16 graph)"}
    else:
        c = PG.cypher("MATCH (n) RETURN count(n) AS n")[0]["n"]
        r = PG.cypher("MATCH ()-[r]->() RETURN count(r) AS r")[0]["r"]
        out["neo4j"] = {"bytes": c * 15 + r * 34 + n * 60, "method": "record-size estimate: 15 B/node, 34 B/relationship + properties"}
    for k in out:
        out[k]["per_threat"] = round(out[k]["bytes"] / n, 1) if out[k].get("bytes") else None
    return out


def scale(sz: dict, bench: dict) -> dict:
    """1M / 10M / 100M threats: storage x replication 3, RAM, nodes, ingest headroom. Formulas are in the report."""
    out = []
    for n in (1_000_000, 10_000_000, 100_000_000):
        row = {"threats": n}
        for dbn in ("mongodb", "cassandra", "neo4j"):
            pt = (sz.get(dbn) or {}).get("per_threat") or 0
            raw = pt * n
            tot = raw * 3
            if dbn == "mongodb":
                idx = sz["mongodb"]["indexes"] * n / sz["_n"]
                ram = idx + raw * 7 / DAYS * 0.5
                nodes = max(3, 3 * math.ceil(raw / (1.5 * 1024 ** 4)))
                how = "replica set of 3; shard (Lab 11) when data per replica > ~1.5 TB or the working set > RAM"
            elif dbn == "cassandra":
                ram = 16 * 1024 ** 3 * max(3, math.ceil(tot / (2 * 1024 ** 4)))
                nodes = max(3, math.ceil(tot / (2 * 1024 ** 4)))
                how = "add nodes to the ring (vnodes rebalance automatically), RF 3, ~2 TB per node"
            else:
                ram = raw * 1.2
                nodes = 3 if raw < 512 * 1024 ** 3 else math.ceil(raw / (512 * 1024 ** 3)) * 3
                how = "one primary holds the whole graph (page cache ~ store size); read replicas scale reads; sharding = Fabric/composite DBs (Enterprise)"
            row[dbn] = {"storage_gb": round(tot / 1024 ** 3, 1), "ram_gb": round(ram / 1024 ** 3, 1), "nodes": nodes, "how": how}
        row["ingest_needed_per_s"] = round(n / (DAYS * 86400) * 20, 1)
        out.append(row)
    return {"rows": out, "measured_throughput": {k: v.get("throughput_per_s") for k, v in bench["results"]["q4"].items()},
            "assumptions": ["replication factor 3 in every database", "data volume = the last 30 days of threats", "ingest peak = 20 x the average",
                            "MongoDB RAM = all indexes + half of the last 7 days of documents", "Cassandra: 16 GB RAM per node (8 GB heap + page cache), 2 TB per node",
                            "Neo4j: page cache >= 1.2 x store size for traversals at memory speed"]}


ERGONOMICS = [
    ("Data model", "Documents as the application uses them; embedding (recent_threats) + references", "Tables designed from the queries; the same data written 4-6 times",
     "Nodes and relationships; new relationship types without migration"),
    ("Query language", "MQL + aggregation pipeline, Python dicts", "CQL looks like SQL but no joins, no GROUP BY across partitions, no ad-hoc filters",
     "Cypher: ASCII-art patterns, multi-hop and variable-length paths in one line"),
    ("New question", "Usually a new index or pipeline stage", "Usually a new table + backfill + dual writes", "Usually just a new pattern"),
    ("Aggregations", "Strong (pipeline, $group, $facet, $topN)", "Precompute (counters, materialised tables) or use Spark", "Fine on subgraphs; full-graph scans are slow"),
    ("Writes", "Single document atomic; multi-document transactions (Lab 10)", "Very fast, idempotent upserts, last-write-wins, lightweight transactions only", "ACID transactions; MERGE needs a unique constraint"),
    ("Driver / tooling", "PyMongo, Compass, Atlas, mongodump (Lab 14)", "cassandra-driver (prepared statements, concurrency), cqlsh, nodetool", "neo4j driver, Browser, Bloom, GDS library"),
    ("Backups", "mongodump / oplog point-in-time (Lab 14), Atlas snapshots", "nodetool snapshot + incremental backups / Medusa per node", "neo4j-admin database dump (Community: offline) / online backup (Enterprise)"),
    ("Monitoring", "serverStatus, exporter + Prometheus (Lab 13)", "nodetool, JMX exporter; watch compactions, tombstones, GC", "metrics + query log (Enterprise), Prometheus endpoint"),
    ("Skills needed", "Schema design, indexes, aggregation", "Query-first modelling, partition sizing, compaction/repair operations", "Graph modelling, Cypher, memory sizing"),
]


def recommendation(bench: dict, fp: dict) -> list[dict]:
    r = bench["results"]

    def win(key, metric="median_ms", high=False):
        vals = {k: r[key][k][metric] for k in ("mongodb", "cassandra", "neo4j")}
        return (max if high else min)(vals, key=vals.get), vals
    out = []
    w, v = win("q1")
    out.append({"pattern": "Threat history of a source (latest 50)", "measured_winner": w, "values": v,
                "recommend": "MongoDB" if w != "cassandra" or v["mongodb"] <= 1.5 * v["cassandra"] else "Cassandra",
                "why": "the embedded recent_threats array answers it with one document read; Cassandra's (source, month) partition is "
                       "equally fast and grows better at very high write rates"})
    w, v = win("q2")
    out.append({"pattern": "Target and spread (who reported it, related threats)", "measured_winner": w, "values": v, "recommend": "Neo4j",
                "why": "a 2-hop pattern (indicator -> family -> other indicators <- same feeds) is one Cypher statement; Cassandra needs 2 tables and "
                       "client-side filtering, MongoDB 2 round trips"})
    w, v = win("q3")
    out.append({"pattern": "Daily volume analytics", "measured_winner": w, "values": v, "recommend": "Cassandra" if w == "cassandra" else w.capitalize(),
                "why": "the answer is maintained on write (counter table) - one partition read; MongoDB aggregates 30 days at read time; "
                       "Neo4j scans relationships"})
    w, v = win("q4", "throughput_per_s", high=True)
    out.append({"pattern": "Real-time ingest", "measured_winner": w, "values": v, "recommend": "Cassandra" if w == "cassandra" else w.capitalize(),
                "why": "log-structured writes, no read-before-write, linear scale-out by adding nodes"})
    fm = {k: fp["per_db"][k]["fetch_ms"] for k in fp["per_db"]}
    out.append({"pattern": "False-positive detection (corroboration, allowlist, feed quality)", "measured_winner": min(fm, key=fm.get), "values": fm,
                "recommend": "Neo4j + MongoDB",
                "why": "graph patterns find feeds that share false positives (SUSPICIOUS) and campaign communities; MongoDB keeps the documents analysts "
                       "read and cross-reference"})
    return out


def run_181(quick: bool = False, log=print) -> dict:
    n = 10_000 if quick else 100_000
    load = load_all(n, log)
    bench = benchmark(10, log)
    fp = detect_fp(log)
    comm = communities(log)
    sz = sizes(n)
    sz["_n"] = n
    sc = scale(sz, bench)
    applied = apply_to_lab7(fp, log)
    rec = recommendation(bench, fp)
    fp.pop("_verdict", None)
    ok = all(bench["same_answer"].get(k, False) for k in ("q2", "q3")) and fp["identical"] and comm["pass"]
    return {"load": load, "benchmark": bench, "fp": fp, "communities": comm, "sizes": {k: v for k, v in sz.items() if k != "_n"},
            "scale": sc, "ergonomics": ERGONOMICS, "recommendation": rec, "applied": applied, "quick": quick,
            "environment": {"at": datetime.now(UTC).isoformat(), "versions": {k: v.get("version") for k, v in PG.availability(True).items() if isinstance(v, dict) and "ok" in v}},
            "pass": ok}


def apply_to_lab7(fp: dict, log=print) -> dict:
    """Mentor's rule: Lab 18 is applied to the Kavach platform - the verdicts are written next to the Lab 7 indicators."""
    db = PG.mongo()["cti_platform"]
    v = fp.get("_verdict", {})
    now = datetime.now(UTC)
    db.fp_verdicts.delete_many({})
    docs = [{"_id": k, "verdict": r, "reason": ALLOWLIST.get(k, "single low-quality source, low confidence"), "at": now} for k, r in v.items()]
    if docs:
        db.fp_verdicts.insert_many(docs)
    hits = db.indicators.count_documents({"value": {"$in": list(v)[:50000]}}) if v else 0
    log(f"   applied to Lab 7: {len(docs):,} verdicts written to cti_platform.fp_verdicts; {hits} match indicators of the Kavach platform")
    return {"written": len(docs), "matching_kavach_indicators": hits, "collection": "cti_platform.fp_verdicts"}


# ============================================================================ status / results
def status() -> dict:
    a = PG.availability()
    out = {"available": all(a[k]["ok"] for k in ("mongodb", "cassandra", "neo4j")), "backends": a}
    if not out["available"]:
        out["reason"] = "; ".join(f"{k}: {a[k].get('error')}" for k in ("mongodb", "cassandra", "neo4j") if not a[k]["ok"])
        return out
    try:
        db = PG.mongo()[DB]
        out["counts"] = {"mongodb": db.full_threats.estimated_document_count(), "sources": db.sources.estimated_document_count(),
                         "neo4j_reported": PG.cypher("MATCH ()-[r:REPORTED]->() RETURN count(r) AS n")[0]["n"],
                         "fp_verdicts": PG.mongo()["cti_platform"].fp_verdicts.estimated_document_count()}
    except Exception as exc:                          # noqa: BLE001
        out["counts_error"] = str(exc)[:200]
    return out


def history(src: str) -> dict:
    return {"source": src, "mongodb": PG.mongo()[DB].sources.find_one({"_id": src}, {"_id": 0, "recent_threats": {"$slice": 20}, "total": 1})}


def investigate(ind: str) -> dict:
    t = {}
    for name, fn in (("mongodb", q2_mongo), ("cassandra", q2_cass), ("neo4j", q2_neo)):
        t0 = time.perf_counter()
        r = fn(ind)
        t[name] = {"ms": round((time.perf_counter() - t0) * 1000, 2), **_jd(r)}
    verdict = PG.mongo()["cti_platform"].fp_verdicts.find_one({"_id": ind})
    docs = list(PG.mongo()[DB].full_threats.find({"indicator": ind}, {"_id": 0}).sort("timestamp", -1).limit(10))
    return {"indicator": ind, "results": t, "verdict": _jd(verdict), "sightings": _jd(docs), "allowlisted": ind in ALLOWLIST}


def load_results() -> dict:
    p = RESULTS / "lab18_results.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def save_part(name: str, value: dict):
    out = load_results()
    out[name] = _jd(value)
    out.setdefault("environment", {})["updated"] = datetime.now(UTC).isoformat()
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "lab18_results.json").write_text(json.dumps(out, indent=2, default=str))

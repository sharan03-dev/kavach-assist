"""Lab 11 - Sharding and horizontal scalability, measured on a REAL sharded cluster.

One module, used by the website API (backend/app/lab11_api.py) AND by the command-line scripts
(labs/lab11/*.py), so what you demonstrate in the browser is exactly what the scripts run.

Cluster (docker-compose.yml):   application -> mongos router -> config server replica set (cfgrs)
                                                             -> shardA (replica set: primary + secondary)
                                                             -> shardB (replica set: primary + secondary)

Lab 11.1  evaluate shard keys   order_id, customer_id, order_date, hashed customer_id (+2 extra rows from the
                                course material). Every candidate gets its OWN sharded collection with the SAME
                                24,000 e-commerce orders, so cardinality, distribution, hot-spots and query
                                targeting are measured, not guessed.
Lab 11.2  hotspot detection     db.hotspot_data {ts: 1}  vs  db.even_data {user_id: "hashed"}, 10,000 documents
                                each, collStats per shard, getShardDistribution, explain() of range queries.
Activity  resharding (5.0+)     live reshardCollection of the bad order_date key to hashed customer_id while a
                                writer keeps inserting; every phase is recorded from $currentOp.

How the classroom solution text maps onto what MongoDB 7.0 really does (verified on mongod 7.0.34):
1. A ranged shard key on an EMPTY collection starts as ONE chunk on the primary shard, so "9,500 on shard A and
   500 on shard B" cannot happen by itself: the 500 come from older data that was split off and migrated (what
   the balancer does). We perform that split + migration explicitly (split + moveChunk), so the experiment is
   repeatable, and then show that every NEW insert still lands on the shard that owns the [last, MaxKey) chunk.
2. A hashed shard key on an empty collection is pre-split by MongoDB into chunks on every shard (2 per shard),
   which is why it spreads writes from the very first insert.
3. A range query on a hashed key is not only broadcast to all shards - the hashed index cannot serve a range,
   so every shard does a COLLSCAN. explain() shows this.
4. The balancer is stopped while we measure, so the counts show where mongos ROUTED each insert, not what the
   balancer moved later. It is switched back on afterwards.
"""
from __future__ import annotations

import bisect
import itertools
import os
import random
import statistics
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from bson import MaxKey, MinKey, ObjectId
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import OperationFailure, PyMongoError

UTC = timezone.utc
SHARD_URI = os.getenv("SHARD_URI", "mongodb://mongos:27030")
CFG_RS = os.getenv("CFG_RS", "cfgrs/cfg1:27101")
SHARD_RS = os.getenv("SHARD_RS", "shardA/shard-a1:27111,shard-a2:27112;shardB/shard-b1:27121,shard-b2:27122")
SHOP_DB = "lab11_shop"          # Lab 11.1 - one collection per candidate shard key
HOT_DB = "lab11"                # Lab 11.2 - db.hotspot_data and db.even_data (names from the classroom solution)
RESHARD_DB = "lab11_reshard"    # Activity 11.1 - resharding demo

# Workload of the order-management system (share of all queries). It decides "query targeting".
WORKLOAD = [
    {"id": "q_order", "label": "Order lookup by order_id", "weight": 0.25},
    {"id": "q_customer", "label": "Customer's order history", "weight": 0.40},
    {"id": "q_cust_recent", "label": "Customer's orders, last 30 days", "weight": 0.20},
    {"id": "q_report", "label": "Sales report, last 7 days (all customers)", "weight": 0.15},
]
# Weights of the decision matrix (the website lets you change them and re-rank live).
CRITERIA = {"cardinality": 0.15, "distribution": 0.25, "hotspot": 0.25, "targeting": 0.35}

CANDIDATES = [
    {"id": "order_id", "label": "order_id", "key": {"order_id": 1}, "required": True,
     "note": "random order number (ORD-xxxxxxxxxxxxxxxx)"},
    {"id": "customer_id", "label": "customer_id", "key": {"customer_id": 1}, "required": True,
     "note": "ranged; ids are given out in sign-up order"},
    {"id": "order_date", "label": "order_date", "key": {"order_date": 1}, "required": True,
     "note": "ranged; always increasing"},
    {"id": "hashed_customer_id", "label": "hashed customer_id", "key": {"customer_id": "hashed"}, "required": True,
     "note": "hash(customer_id) decides the chunk"},
    {"id": "category", "label": "category", "key": {"category": 1}, "required": False,
     "note": "extra row: the low-cardinality example from the attached Shard Key Selection matrix"},
    {"id": "compound", "label": "{customer_id: hashed, order_date: 1}", "key": {"customer_id": "hashed", "order_date": 1},
     "required": False, "note": "extra row: refinement of the compound key suggested in the course repository"},
]

CATEGORIES = [("Electronics", 30), ("Fashion", 22), ("Home & Kitchen", 14), ("Books", 10), ("Beauty", 8),
              ("Sports", 7), ("Toys", 5), ("Grocery", 4)]
CITIES = ["Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Chennai", "Pune", "Kolkata", "Ahmedabad", "Jaipur", "Kochi"]
PAYMENTS = ["UPI", "Card", "NetBanking", "Cash on delivery", "Wallet"]


# ============================================================================ connections
_client: MongoClient | None = None
_direct: dict[str, MongoClient] = {}
_lock = threading.Lock()
HEAVY = threading.Lock()                 # one experiment at a time inside one process


def client() -> MongoClient:
    """MongoClient for the mongos router (the application only ever talks to mongos)."""
    global _client
    with _lock:
        if _client is None:
            _client = MongoClient(SHARD_URI, serverSelectionTimeoutMS=5000, appname="lab11", tz_aware=True)
    return _client


def direct(host: str) -> MongoClient:
    """Direct connection to one mongod (used only for monitoring: replica-set state, opcounters)."""
    with _lock:
        if host not in _direct:
            _direct[host] = MongoClient(host, directConnection=True, serverSelectionTimeoutMS=2500,
                                        connectTimeoutMS=2500, tz_aware=True, appname="lab11-monitor")
    return _direct[host]


def parse_rs(spec: str) -> tuple[str, list[str]]:
    """'shardA/shard-a1:27111,shard-a2:27112' -> ('shardA', ['shard-a1:27111', 'shard-a2:27112'])"""
    name, _, hosts = spec.partition("/")
    return name, [h for h in hosts.split(",") if h]


def shard_specs() -> list[tuple[str, list[str]]]:
    return [parse_rs(s) for s in SHARD_RS.split(";") if s.strip()]


def shard_names() -> list[str]:
    return [n for n, _ in shard_specs()]


def available() -> dict:
    try:
        c = client()
        c.admin.command("ping")
        shards = c.admin.command("listShards")["shards"]
    except PyMongoError as exc:
        return {"available": False, "reason": f"mongos not reachable at {SHARD_URI}: {str(exc)[:140]}"}
    if len(shards) < 2:
        return {"available": False, "reason": f"only {len(shards)} shard(s) registered - the shard-init container "
                                              f"has not finished yet (docker compose logs shard-init)"}
    return {"available": True, "shards": [s["_id"] for s in shards]}


def _primary_of(hosts: list[str]) -> str | None:
    for h in hosts:
        try:
            hello = direct(h).admin.command("hello")
            if hello.get("isWritablePrimary"):
                return h
            if hello.get("primary"):
                return hello["primary"]
        except PyMongoError:
            continue
    return None


def rs_members(hosts: list[str]) -> list[dict]:
    for h in hosts:
        try:
            st = direct(h).admin.command("replSetGetStatus")
        except PyMongoError:
            continue
        prim = next((m for m in st["members"] if m["stateStr"] == "PRIMARY"), None)
        ref = (prim or {}).get("optimeDate")
        out = []
        for m in st["members"]:
            lag = None
            if ref is not None and m.get("optimeDate") is not None and m.get("health") == 1:
                lag = round(max(0.0, (ref - m["optimeDate"]).total_seconds()), 1)
            out.append({"name": m["name"], "state": m["stateStr"], "health": m.get("health"), "lag_s": lag,
                        "uptime": m.get("uptime")})
        return out
    return [{"name": h, "state": "UNREACHABLE", "health": 0, "lag_s": None, "uptime": None} for h in hosts]


def cluster_status() -> dict:
    """Everything the website's Cluster tab shows: router, config servers, shards, balancer, sharded collections."""
    av = available()
    if not av["available"]:
        return av
    c = client()
    shards = []
    for s in c.admin.command("listShards")["shards"]:
        name, hosts = parse_rs(s["host"])
        shards.append({"name": s["_id"], "host": s["host"], "members": rs_members(hosts)})
    cfg_name, cfg_hosts = parse_rs(CFG_RS)
    bal = c.admin.command("balancerStatus")
    return {"available": True, "router": SHARD_URI, "mongos_version": c.server_info()["version"],
            "config": {"name": cfg_name, "members": rs_members(cfg_hosts)}, "shards": shards,
            "balancer": {"mode": bal.get("mode"), "in_round": bal.get("inBalancerRound")},
            "collections": sharded_collections(), "at": datetime.now(UTC)}


def sharded_collections(prefix: str = "lab11") -> list[dict]:
    c = client()
    out = []
    for coll in c.config.collections.find({"_id": {"$regex": f"^{prefix}"}}).sort("_id", 1):
        chunks = Counter(ch["shard"] for ch in c.config.chunks.find({"uuid": coll["uuid"]}, {"shard": 1}))
        db_name, _, name = coll["_id"].partition(".")
        counts = shard_counts(db_name, name)
        out.append({"ns": coll["_id"], "key": key_text(coll["key"]), "unique": coll.get("unique", False),
                    "chunks": dict(chunks), "docs": {s: v["count"] for s, v in counts.items()},
                    "total": sum(v["count"] for v in counts.values())})
    return out


# ============================================================================ measuring tools
def key_text(key: dict) -> str:
    return "{" + ", ".join(f"{k}: {repr(v) if isinstance(v, str) else v}" for k, v in key.items()) + "}"


def bound_text(v) -> str:
    if isinstance(v, MinKey):
        return "MinKey"
    if isinstance(v, MaxKey):
        return "MaxKey"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    return str(v)


def chunk_map(db_name: str, coll: str) -> list[dict]:
    """config.chunks of one collection, in shard-key order: [{min, max, shard}] (what sh.status() prints)."""
    c = client()
    meta = c.config.collections.find_one({"_id": f"{db_name}.{coll}"})
    if not meta:
        return []
    rows = []
    for ch in c.config.chunks.find({"uuid": meta["uuid"]}).sort("min", 1):
        rows.append({"shard": ch["shard"],
                     "min": ", ".join(bound_text(v) for v in ch["min"].values()),
                     "max": ", ".join(bound_text(v) for v in ch["max"].values())})
    return rows


def shard_counts(db_name: str, coll: str) -> dict:
    """Per-shard document count and size from $collStats (the aggregation form of collStats)."""
    out = {s: {"count": 0, "size": 0, "storage": 0, "avg_obj": 0, "indexes": 0, "index_size": 0} for s in shard_names()}
    try:
        for r in client()[db_name][coll].aggregate([{"$collStats": {"storageStats": {}}}]):
            ss = r.get("storageStats", {})
            out[r["shard"]] = {"count": int(ss.get("count", 0)), "size": int(ss.get("size", 0)),
                               "storage": int(ss.get("storageSize", 0)), "avg_obj": int(ss.get("avgObjSize", 0) or 0),
                               "indexes": int(ss.get("nindexes", 0)), "index_size": int(ss.get("totalIndexSize", 0))}
    except OperationFailure:
        pass
    return out


def collstats(db_name: str, coll: str) -> dict:
    """The collStats command the classroom solution asks for (db.coll.stats() in mongosh).
    It is deprecated since 6.2 but still answered by 7.0; if a server refuses it, $collStats is used instead."""
    try:
        cs = client()[db_name].command("collStats", coll)
        shards = {s: {"count": 0, "size": 0, "storage": 0, "avg_obj": 0, "indexes": 0, "index_size": 0}
                  for s in shard_names()}
        for s, v in cs.get("shards", {}).items():
            shards[s] = {"count": int(v.get("count", 0)), "size": int(v.get("size", 0)),
                         "storage": int(v.get("storageSize", 0)), "avg_obj": int(v.get("avgObjSize", 0) or 0),
                         "indexes": int(v.get("nindexes", 0)), "index_size": int(v.get("totalIndexSize", 0))}
        return {"source": "collStats command", "sharded": cs.get("sharded"), "count": int(cs.get("count", 0)),
                "size": int(cs.get("size", 0)), "nchunks": cs.get("nchunks"), "shards": shards}
    except OperationFailure:
        shards = shard_counts(db_name, coll)
        return {"source": "$collStats", "sharded": True, "count": sum(v["count"] for v in shards.values()),
                "size": sum(v["size"] for v in shards.values()), "nchunks": len(chunk_map(db_name, coll)),
                "shards": shards}


def human_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.2f}{unit}"
        n /= 1024
    return str(n)


def shard_distribution_text(db_name: str, coll: str) -> str:
    """Same layout as mongosh's db.collection.getShardDistribution()."""
    stats = shard_counts(db_name, coll)
    chunks = Counter(ch["shard"] for ch in chunk_map(db_name, coll))
    hosts = {n: f"{n}/{','.join(h)}" for n, h in shard_specs()}
    tot_docs = sum(v["count"] for v in stats.values()) or 1
    tot_size = sum(v["size"] for v in stats.values()) or 1
    lines = []
    for s, v in stats.items():
        ch = chunks.get(s, 0)
        lines += [f"Shard {s} at {hosts.get(s, s)}", "{",
                  f"  data: '{human_bytes(v['size'])}',", f"  docs: {v['count']},", f"  chunks: {ch},",
                  f"  'estimated data per chunk': '{human_bytes(v['size'] / ch) if ch else '0B'}',",
                  f"  'estimated docs per chunk': {v['count'] // ch if ch else 0}", "}", "---"]
    lines += ["Totals", "{", f"  data: '{human_bytes(tot_size)}',", f"  docs: {tot_docs},",
              f"  chunks: {sum(chunks.values())},"]
    for s, v in stats.items():
        lines.append(f"  'Shard {s}': [ '{100 * v['size'] / tot_size:.2f} % data', "
                     f"'{100 * v['count'] / tot_docs:.2f} % docs in cluster', '{v['avg_obj']}B avg obj size on shard' ],")
    lines.append("}")
    return "\n".join(lines)


def opcounters() -> dict:
    """opcounters.insert of every shard PRIMARY: the number of inserts each shard actually executed."""
    out = {}
    for name, hosts in shard_specs():
        p = _primary_of(hosts)
        try:
            out[name] = int(direct(p).admin.command("serverStatus")["opcounters"]["insert"]) if p else None
        except PyMongoError:
            out[name] = None
    return out


def _plan_chain(wp: dict) -> list[str]:
    wp = wp.get("queryPlan", wp)
    chain = []
    while wp:
        st = wp.get("stage", "?")
        if st == "IXSCAN":
            st += " " + key_text(wp.get("keyPattern", {}))
        chain.append(st)
        wp = wp.get("inputStage") or (wp.get("inputStages") or [None])[0]
    return chain


def explain_query(db_name: str, coll: str, filt: dict, *, runs: int = 15) -> dict:
    """Run explain() through mongos and summarise the routing: which shards, which plan, how much work."""
    c = client()
    col = c[db_name][coll]
    ex = col.find(filt).explain()
    wp = ex["queryPlanner"]["winningPlan"]
    es = ex.get("executionStats", {})
    by_shard = {s["shardName"]: s for s in es.get("executionStages", {}).get("shards", [])}
    per = []
    for s in wp.get("shards", []):
        chain = _plan_chain(s.get("winningPlan", {}))
        e = by_shard.get(s["shardName"], {})
        per.append({"shard": s["shardName"], "plan": " > ".join(chain),
                    "access": next((x for x in chain if x.startswith(("IXSCAN", "COLLSCAN"))), chain[-1] if chain else "?"),
                    "n": e.get("nReturned"), "docs": e.get("totalDocsExamined"), "keys": e.get("totalKeysExamined")})
    meta = c.config.collections.find_one({"_id": f"{db_name}.{coll}"}) or {}
    key = meta.get("key", {})
    first = next(iter(key), None)
    total = len(shard_names())
    n = len(per)
    hashed = key.get(first) == "hashed"
    cond = filt.get(first) if first else None
    is_eq = cond is not None and not isinstance(cond, dict)
    if n == 1:
        kind = "targeted"
    elif cond is None:
        kind = "scatter-gather"
    elif hashed and not is_eq:
        kind = "scatter-gather (hashed key cannot route a range)"
    else:
        kind = "multi-shard (range spans chunks on several shards)"
    lat = []
    for _ in range(runs):
        t = time.perf_counter()
        list(col.find(filt, {"_id": 1}))
        lat.append((time.perf_counter() - t) * 1000)
    return {"ns": f"{db_name}.{coll}", "filter": filter_text(filt), "stage": wp.get("stage"),
            "shards": [p["shard"] for p in per], "n_shards": n, "total_shards": total, "kind": kind,
            "targeted": n == 1, "nReturned": es.get("nReturned"), "docsExamined": es.get("totalDocsExamined"),
            "keysExamined": es.get("totalKeysExamined"), "per_shard": per,
            "avg_ms": round(statistics.fmean(lat), 2) if lat else None}


def filter_text(f) -> str:
    def v(x):
        if isinstance(x, dict):
            return "{" + ", ".join(f"{k}: {v(y)}" for k, y in x.items()) + "}"
        if isinstance(x, datetime):
            return f"ISODate('{x.strftime('%Y-%m-%dT%H:%M')}')"
        if isinstance(x, ObjectId):
            return f"ObjectId('{x}')"
        if isinstance(x, str):
            return f"'{x}'"
        return str(x)
    return v(f)


def prepare_db(db_name: str, log=print):
    """Fresh database whose primary shard is shardA, so every run starts from the same layout."""
    c = client()
    c.drop_database(db_name)
    try:
        c.admin.command("enableSharding", db_name, primaryShard=shard_names()[0])
    except OperationFailure as exc:                       # older servers: no primaryShard option
        c.admin.command("enableSharding", db_name)
        log(f"   (enableSharding without primaryShard: {exc.code})")


class balancer_paused:
    """Context manager: stop the balancer while we measure routing, restart it afterwards (always)."""

    def __enter__(self):
        self.was = client().admin.command("balancerStatus").get("mode")
        client().admin.command("balancerStop")
        return self

    def __exit__(self, *exc):
        if self.was != "off":
            try:
                client().admin.command("balancerStart")
            except PyMongoError:
                pass
        return False


def split_and_move(db_name: str, coll: str, field: str, at, to_shard: str) -> float:
    """What the balancer does, done explicitly: split the chunk at `at` and migrate [MinKey, at) to `to_shard`."""
    c = client()
    ns = f"{db_name}.{coll}"
    t = time.perf_counter()
    c.admin.command("split", ns, middle={field: at})
    c.admin.command("moveChunk", ns, bounds=[{field: MinKey()}, {field: at}], to=to_shard, _waitForDelete=True)
    return round(time.perf_counter() - t, 2)


# ============================================================================ e-commerce data (Lab 11.1)
def gen_orders(n: int, seed: int = 11, year: int = 2025) -> list[dict]:
    """n orders in time order (that is how orders arrive). Deterministic for a given seed.

    * customers: 60% already existed on 1 January, 40% sign up during the year (customer ids are given out in
      sign-up order, as most systems do); a customer can only order after signing up.
    * popularity: Pareto-distributed (a few loyal customers order a lot, most order a few times).
    * order_id: random (like a UUID), order_date: strictly increasing, category: skewed (Electronics 30%).
    """
    R = random.Random(seed)
    n_cust = max(2000, n // 8)
    existing = int(n_cust * 0.6)
    signup = [-1.0] * existing + sorted(R.random() for _ in range(n_cust - existing))
    cum = list(itertools.accumulate(min(R.paretovariate(1.3), 80.0) for _ in range(n_cust)))
    cats = [c for c, _ in CATEGORIES]
    ccum = list(itertools.accumulate(w for _, w in CATEGORIES))
    start = datetime(year, 1, 1, tzinfo=UTC)
    span = (datetime(year, 12, 31, 23, 0, tzinfo=UTC) - start).total_seconds()
    out = []
    for i in range(n):
        f = (i + R.random()) / n
        k = max(1, bisect.bisect_right(signup, f))
        cust = bisect.bisect_right(cum, R.random() * cum[k - 1])
        items = R.randint(1, 5)
        out.append({
            "order_id": "ORD-" + uuid.UUID(int=R.getrandbits(128), version=4).hex[:16].upper(),
            "customer_id": f"C-{cust + 1:06d}",
            "order_date": start + timedelta(seconds=round(f * span, 3)),
            "category": cats[bisect.bisect_right(ccum, R.random() * ccum[-1])],
            "items": items,
            "amount": round(R.uniform(199, 4999) * items, 2),
            "city": R.choice(CITIES),
            "payment": R.choice(PAYMENTS),
            "status": "delivered" if f < 0.97 else R.choice(["placed", "packed", "shipped"]),
        })
    return out


def _key_value(doc: dict, key: dict):
    return tuple(doc[k] for k in key)


def _score_cardinality(distinct: int, n: int, top_share: float) -> int:
    s = 5 if distinct >= 0.5 * n else 4 if distinct >= 1000 else 3 if distinct >= 100 else 2 if distinct >= 20 else 1
    return min(s, 2) if top_share > 0.05 else s


def _score_distribution(busiest: float) -> int:
    return 5 if busiest <= 0.56 else 4 if busiest <= 0.62 else 3 if busiest <= 0.72 else 2 if busiest <= 0.85 else 1


def _hotspot_level(last_window: float) -> tuple[str, int]:
    return ("Low", 5) if last_window <= 0.65 else ("Medium", 3) if last_window <= 0.90 else ("High", 1)


def total_score(scores: dict, weights: dict = CRITERIA) -> float:
    return round(sum(weights[k] * scores[k] for k in weights), 2)


def evaluate_shard_keys(n: int = 24_000, *, seed: int = 11, history_frac: float = 0.2, batches: int = 8,
                        log=print, candidates=None) -> dict:
    """Lab 11.1: load the same orders into one sharded collection per candidate key and measure."""
    candidates = candidates or CANDIDATES
    orders = gen_orders(n, seed)
    c = client()
    db = c[SHOP_DB]
    shards = shard_names()
    h = int(n * history_frac)
    history, live = orders[:h], orders[h:]
    step = -(-len(live) // batches)
    end = orders[-1]["order_date"]
    probe = {"order_id": orders[int(n * 0.73)]["order_id"], "customer_id": orders[int(n * 0.9)]["customer_id"]}
    queries = {
        "q_order": {"order_id": probe["order_id"]},
        "q_customer": {"customer_id": probe["customer_id"]},
        "q_cust_recent": {"customer_id": probe["customer_id"], "order_date": {"$gte": end - timedelta(days=30)}},
        "q_report": {"order_date": {"$gte": end - timedelta(days=7), "$lte": end}},
    }
    log(f"Dataset: {n:,} orders, {len({o['customer_id'] for o in orders}):,} distinct customers, "
        f"{orders[0]['order_date']:%d %b %Y} -> {end:%d %b %Y}")
    log(f"Each candidate: {h:,} 'history' orders, balancer-style split at the median + migrate the lower half to "
        f"{shards[1]}, then {len(live):,} 'live' orders in {batches} batches (balancer stopped, so we see routing).")
    prepare_db(SHOP_DB, log)
    results = []
    with balancer_paused():
        for cand in candidates:
            name = f"orders_by_{cand['id']}"
            ns = f"{SHOP_DB}.{name}"
            key = cand["key"]
            first = next(iter(key))
            hashed = "hashed" in key.values()
            log(f"\n[{cand['label']}]  sh.shardCollection('{ns}', {key_text(key)})")
            db.create_collection(name)
            for ix in ([("order_id", ASCENDING)], [("customer_id", ASCENDING), ("order_date", DESCENDING)],
                       [("order_date", ASCENDING)]):
                db[name].create_index(ix)                 # same secondary indexes everywhere: only routing differs
            c.admin.command("shardCollection", ns, key=key)
            db[name].insert_many([dict(o) for o in history], ordered=False)
            moved = None
            if not hashed:
                vals = sorted(o[first] for o in history)
                median = vals[len(vals) // 2]
                secs = split_and_move(SHOP_DB, name, first, median, shards[1])
                moved = {"at": bound_text(median), "seconds": secs}
                log(f"   history loaded; split at {first} = {bound_text(median)} and moved [MinKey, median) "
                    f"to {shards[1]} in {secs} s")
            else:
                log("   history loaded; hashed key: MongoDB pre-split the empty collection on every shard")
            base = {s: v["count"] for s, v in shard_counts(SHOP_DB, name).items()}
            ops0 = opcounters()
            t0 = time.perf_counter()
            timeline, prev = [], dict(base)
            for b in range(batches):
                chunk = live[b * step:(b + 1) * step]
                if not chunk:
                    break
                db[name].insert_many([dict(o) for o in chunk], ordered=False)
                now = {s: v["count"] for s, v in shard_counts(SHOP_DB, name).items()}
                timeline.append({"batch": b + 1, "added": {s: now[s] - prev[s] for s in shards}, "total": now})
                prev = now
            ins_s = time.perf_counter() - t0
            ops1 = opcounters()
            final = prev
            live_by = {s: final[s] - base[s] for s in shards}
            busiest_shard = max(live_by, key=live_by.get)
            busiest = live_by[busiest_shard] / max(1, len(live))
            last = timeline[-1]["added"]
            last_busiest = max(last.values()) / max(1, sum(last.values()))
            keys_seq = [_key_value(o, key) for o in orders]
            cnt = Counter(keys_seq)
            top_val, top_n = cnt.most_common(1)[0]
            mx, newmax = None, 0
            for kv in keys_seq[h:]:
                if mx is None or kv > mx:
                    newmax, mx = newmax + 1, kv
            mono = newmax / len(live) if not hashed else None
            card = {"distinct": len(cnt), "top_value": " / ".join(bound_text(v) for v in top_val),
                    "top_share": round(top_n / n, 4)}
            card["score"] = _score_cardinality(card["distinct"], n, card["top_share"])
            dist = {"live_by_shard": live_by, "busiest_shard": busiest_shard, "busiest_share": round(busiest, 4)}
            dist["score"] = _score_distribution(busiest)
            level, hs = _hotspot_level(last_busiest)
            hot = {"last_batch_busiest_share": round(last_busiest, 4), "level": level, "score": hs,
                   "new_max_rate": round(mono, 4) if mono is not None else None}
            targ = {}
            for q in WORKLOAD:
                targ[q["id"]] = explain_query(SHOP_DB, name, queries[q["id"]], runs=8)
            tw = sum(q["weight"] for q in WORKLOAD if targ[q["id"]]["targeted"])
            targeting = {"targeted_weight": round(tw, 2), "score": round(1 + 4 * tw, 1),
                         "queries": {k: {kk: v[kk] for kk in ("n_shards", "kind", "targeted", "docsExamined", "avg_ms", "shards")}
                                     for k, v in targ.items()}}
            scores = {"cardinality": card["score"], "distribution": dist["score"], "hotspot": hot["score"],
                      "targeting": targeting["score"]}
            row = {**{k: cand[k] for k in ("id", "label", "required", "note")}, "key": key_text(key), "ns": ns,
                   "cardinality": card, "distribution": dist, "hotspot": hot, "targeting": targeting,
                   "scores": scores, "total": total_score(scores), "history_split": moved,
                   "history_by_shard": base, "timeline": timeline, "chunks": chunk_map(SHOP_DB, name),
                   "opcounters_live": {s: (ops1[s] - ops0[s]) if ops1.get(s) is not None and ops0.get(s) is not None else None
                                       for s in shards},
                   "insert_seconds": round(ins_s, 2), "inserts_per_s": round(len(live) / ins_s) if ins_s else None}
            log(f"   live inserts by shard: " + ", ".join(f"{s}={v:,}" for s, v in live_by.items())
                + f"  (busiest {busiest:.0%}, last batch {last_busiest:.0%})")
            log("   targeting: " + ", ".join(f"{q['id']}={targ[q['id']]['n_shards']}/{targ[q['id']]['total_shards']}"
                                            for q in WORKLOAD) + f"   score total {row['total']}")
            results.append(row)
    req = [r for r in results if r["required"]]
    best = max(req, key=lambda r: r["total"])
    best_all = max(results, key=lambda r: r["total"])
    for r in results:
        r["verdict"], r["reason"] = _verdict(r, best["id"], best["total"])
        r["cells"] = matrix_cells(r)
    return {"n_orders": n, "seed": seed, "history": h, "live": len(live), "batches": batches,
            "customers": len({o["customer_id"] for o in orders}), "period": [orders[0]["order_date"], end],
            "probe": probe, "queries": {k: filter_text(v) for k, v in queries.items()}, "workload": WORKLOAD,
            "criteria": CRITERIA, "candidates": results, "recommended": best["id"],
            "best_overall": best_all["id"], "shards": shards}


def _verdict(r: dict, best_id: str, best_total: float = 0) -> tuple[str, str]:
    d, h, cd, t = r["distribution"], r["hotspot"], r["cardinality"], r["targeting"]
    if h["level"] == "High":
        return "AVOID", (f"monotonic: {h['last_batch_busiest_share']:.0%} of the newest orders went to "
                         f"{d['busiest_shard']} - every insert hits the [last, MaxKey) chunk")
    if cd["score"] <= 2:
        return "AVOID", (f"only {cd['distinct']} distinct values and '{cd['top_value']}' alone holds "
                         f"{cd['top_share']:.0%} of the orders: chunks can never be split below one value (jumbo chunks)")
    if not r["required"] and r["total"] > best_total:
        return "REFINEMENT", (f"routes exactly like hashed customer_id ({d['busiest_share']:.0%} on the busiest shard, "
                              f"{t['targeted_weight']:.0%} of the workload single-shard) and adds order_date, so one very "
                              f"busy customer's orders can still be split into several chunks (no jumbo chunk)")
    if r["id"] == best_id:
        return "RECOMMENDED", (f"even writes ({d['busiest_share']:.0%} on the busiest shard), low hot-spot risk and "
                               f"{t['targeted_weight']:.0%} of the workload goes to a single shard")
    weak = min(("distribution", "hotspot", "targeting", "cardinality"), key=lambda k: r["scores"][k])
    why = {"targeting": f"only {t['targeted_weight']:.0%} of the workload is single-shard - customer-history "
                        f"queries become scatter-gather",
           "distribution": f"uneven: {d['busiest_share']:.0%} of the new orders landed on {d['busiest_shard']}",
           "hotspot": f"hot-spot risk {h['level'].lower()}: {h['last_batch_busiest_share']:.0%} of the latest batch on one shard",
           "cardinality": f"{cd['distinct']:,} distinct values, the most common holds {cd['top_share']:.1%}"}[weak]
    return ("GOOD with caveats" if r["total"] >= 3.5 else "WEAK"), why


def matrix_cells(r: dict) -> dict:
    """Plain-language cells of the decision matrix (same words in the terminal, the website and the PDF)."""
    cd, d, h, t = r["cardinality"], r["distribution"], r["hotspot"], r["targeting"]
    lvl = {5: "Very high", 4: "High", 3: "Medium", 2: "Low", 1: "Very low"}[cd["score"]]
    dist = {5: "Even", 4: "Slightly uneven", 3: "Uneven", 2: "Uneven", 1: "Skewed"}[d["score"]]
    nq = sum(1 for q in t["queries"].values() if q["targeted"])
    return {"cardinality": f"{lvl}: {cd['distinct']:,} values, top {cd['top_share']:.1%}",
            "distribution": f"{dist}: {d['busiest_share']:.0%} of new orders on {d['busiest_shard']}",
            "hotspot": f"{h['level']} ({h['last_batch_busiest_share']:.0%} of last batch on one shard)",
            "targeting": f"{nq} of {len(t['queries'])} queries single-shard ({t['targeted_weight']:.0%} of workload)"}


# ============================================================================ Lab 11.2 hotspot detection
def _hot_docs(n: int, history: int, seed: int = 112) -> list[dict]:
    R = random.Random(seed)
    cut = datetime(2025, 1, 1, tzinfo=UTC)
    step_hist = timedelta(minutes=52)
    step_live = timedelta(days=364) / max(1, n - history)
    docs = []
    for i in range(n):
        ts = cut - (history - i) * step_hist if i < history else cut + (i - history) * step_live
        docs.append({"ts": ts, "user_id": ObjectId(), "seq": i,
                     "event": R.choice(["page_view", "add_to_cart", "checkout", "payment"]),
                     "value": round(R.uniform(10, 5000), 2)})
    return docs


def hotspot_experiment(n: int = 10_000, history: int = 500, batches: int = 10, log=print) -> dict:
    """Lab 11.2 exactly as the classroom solution describes it, measured.

    hotspot_data  shard key {ts: 1}                monotonically increasing ISODate
    even_data     shard key {user_id: 'hashed'}    a new ObjectId per document
    The first `history` documents are December 2024; their range is split off and migrated to shardB (the
    balancer's job), then the remaining documents of 2025 arrive in time order.
    """
    c = client()
    db = c[HOT_DB]
    shards = shard_names()
    a, b = shards[0], shards[1]
    cut = datetime(2025, 1, 1, tzinfo=UTC)
    prepare_db(HOT_DB, log)
    log(f"sh.enableSharding('{HOT_DB}')   (primary shard {a})")
    db.create_collection("hotspot_data")
    db.create_collection("even_data")
    c.admin.command("shardCollection", f"{HOT_DB}.hotspot_data", key={"ts": 1})
    c.admin.command("shardCollection", f"{HOT_DB}.even_data", key={"user_id": "hashed"})
    db.even_data.create_index([("ts", ASCENDING)])        # same access path on ts for a fair explain() comparison
    log("sh.shardCollection('lab11.hotspot_data', {ts: 1})")
    log("sh.shardCollection('lab11.even_data', {user_id: 'hashed'})   + index {ts: 1} on even_data")
    log("chunks before inserting:  hotspot_data " + str(len(chunk_map(HOT_DB, "hotspot_data")))
        + " (one range MinKey..MaxKey on the primary shard),  even_data "
        + str(len(chunk_map(HOT_DB, "even_data"))) + " (hashed: pre-split on every shard)")
    docs = _hot_docs(n, history)
    out = {"n": n, "history": history, "batches": batches, "cut": cut, "shards": shards, "collections": {}}
    res = {"hotspot_data": {"timeline": []}, "even_data": {"timeline": []}}
    with balancer_paused():
        for coll in ("hotspot_data", "even_data"):
            db[coll].insert_many([dict(d) for d in docs[:history]], ordered=False)
        secs = split_and_move(HOT_DB, "hotspot_data", "ts", cut, b)
        log(f"\nPhase 1: {history} documents of December 2024 inserted into both collections.")
        log(f"   hotspot_data: split at ts = 2025-01-01 and moveChunk [MinKey, 2025-01-01) -> {b} ({secs} s)")
        log(f"   = what the balancer does to spread OLD data; the chunk [2025-01-01, MaxKey) stays on {a}.")
        live = docs[history:]
        step = -(-len(live) // batches)
        log(f"\nPhase 2: {len(live):,} documents of 2025 in {batches} batches of {step}, timestamps always increasing")
        log(f"{'batch':>5}  {'hotspot_data ' + a:>18} {'hotspot_data ' + b:>18}   {'even_data ' + a:>15} {'even_data ' + b:>15}")
        ops_tot = {coll: {s: 0 for s in shards} for coll in res}
        secs_tot = {coll: 0.0 for coll in res}
        for i in range(batches):
            chunk = live[i * step:(i + 1) * step]
            if not chunk:
                break
            row = {}
            for coll in ("hotspot_data", "even_data"):
                o0 = opcounters()
                t = time.perf_counter()
                db[coll].insert_many([dict(d) for d in chunk], ordered=False)
                secs_tot[coll] += time.perf_counter() - t
                o1 = opcounters()
                for s in shards:
                    if o0.get(s) is not None and o1.get(s) is not None:
                        ops_tot[coll][s] += o1[s] - o0[s]
                cnt = {s: v["count"] for s, v in shard_counts(HOT_DB, coll).items()}
                res[coll]["timeline"].append({"batch": i + 1, "docs": cnt})
                row[coll] = cnt
            log(f"{i + 1:>5}  {row['hotspot_data'][a]:>18,} {row['hotspot_data'][b]:>18,}   "
                f"{row['even_data'][a]:>15,} {row['even_data'][b]:>15,}")
        for coll in res:
            res[coll]["inserts_by_shard_phase2"] = ops_tot[coll]
            res[coll]["insert_seconds"] = round(secs_tot[coll], 2)
            res[coll]["inserts_per_s"] = round(len(live) / secs_tot[coll]) if secs_tot[coll] else None
    for coll in res:
        res[coll]["collstats"] = collstats(HOT_DB, coll)
        res[coll]["chunks"] = chunk_map(HOT_DB, coll)
        res[coll]["distribution_text"] = shard_distribution_text(HOT_DB, coll)
        res[coll]["key"] = "{ts: 1}" if coll == "hotspot_data" else "{user_id: 'hashed'}"
    out["collections"] = res
    log("\ndb.hotspot_data.stats() / db.even_data.stats()  ->  per-shard count and size (collStats)")
    log(f"{'collection':<14}{'shard':<9}{'count':>8}{'size':>12}{'avgObj':>8}{'chunks':>8}")
    for coll in res:
        cs = res[coll]["collstats"]
        ch = Counter(x["shard"] for x in res[coll]["chunks"])
        for s in shards:
            v = cs["shards"][s]
            log(f"{coll:<14}{s:<9}{v['count']:>8,}{human_bytes(v['size']):>12}{v['avg_obj']:>8}{ch.get(s, 0):>8}")
    for coll in res:
        log(f"\ndb.{coll}.getShardDistribution()\n" + res[coll]["distribution_text"])

    q = explain_presets()
    out["explain"] = {}
    log("\nexplain() through mongos")
    log(f"{'id':<4}{'collection':<14}{'stage':<14}{'shards':<17}{'returned':>9}{'examined':>10}{'avg ms':>8}  routing / plan")
    for k, (coll, f, why) in q.items():
        e = explain_query(HOT_DB, coll, f)
        e["why"] = why
        out["explain"][k] = e
        log(f"{k:<4}{coll:<14}{e['stage']:<14}{','.join(e['shards']):<17}{e['nReturned']:>9,}{e['docsExamined']:>10,}"
            f"{e['avg_ms']:>8}  {e['kind']}; {e['per_shard'][0]['access'] if e['per_shard'] else ''}")
        log(f"      {why}: {e['filter']}")
    hd, ed = res["hotspot_data"], res["even_data"]
    out["summary"] = {
        "hotspot_by_shard": {s: hd["collstats"]["shards"][s]["count"] for s in shards},
        "even_by_shard": {s: ed["collstats"]["shards"][s]["count"] for s in shards},
        "hotspot_phase2_inserts": hd["inserts_by_shard_phase2"], "even_phase2_inserts": ed["inserts_by_shard_phase2"],
    }
    return out


def explain_presets() -> dict:
    """The five explain() cases of Lab 11.2, built from the documents that are in the collections right now."""
    db = client()[HOT_DB]
    n = db.even_data.estimated_document_count()
    if not n:
        raise ValueError("run the Lab 11.2 experiment first")
    pick = lambda frac: next(db.even_data.find({}, {"user_id": 1}).sort("user_id", 1).skip(int(n * frac)).limit(1))["user_id"]
    y25, y26 = datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 12, 31, tzinfo=UTC)
    return {
        "H1": ("hotspot_data", {"ts": {"$gte": y25, "$lt": y26}}, "range on the shard key ts (the classroom query)"),
        "H2": ("hotspot_data", {"ts": {"$gte": datetime(2024, 12, 20, tzinfo=UTC), "$lt": datetime(2025, 1, 10, tzinfo=UTC)}},
               "range that crosses the chunk boundary 2025-01-01"),
        "E1": ("even_data", {"ts": {"$gte": y25, "$lt": y26}}, "same ts range on even_data (ts is not its shard key)"),
        "E2": ("even_data", {"user_id": {"$gte": pick(0.1), "$lt": pick(0.6)}}, "range on the hashed shard key user_id"),
        "E3": ("even_data", {"user_id": pick(0.5)}, "equality on the hashed shard key user_id"),
    }


def explain_preset(pid: str) -> dict:
    coll, f, why = explain_presets()[pid]
    return {**explain_query(HOT_DB, coll, f), "why": why, "id": pid}


def insert_more(coll: str, n: int = 1000) -> dict:
    """Website button: continue the timeline with n more documents and report where they were routed."""
    if coll not in ("hotspot_data", "even_data"):
        raise ValueError("collection must be hotspot_data or even_data")
    db = client()[HOT_DB]
    last = db[coll].find_one(sort=[("ts", DESCENDING)])
    if not last:
        raise ValueError("run the Lab 11.2 experiment first")
    before = {s: v["count"] for s, v in shard_counts(HOT_DB, coll).items()}
    o0 = opcounters()
    start = last["ts"]
    R = random.Random()
    docs = [{"ts": start + timedelta(minutes=55 * (i + 1)), "user_id": ObjectId(), "seq": int(last.get("seq", 0)) + i + 1,
             "event": R.choice(["page_view", "add_to_cart", "checkout", "payment"]), "value": round(R.uniform(10, 5000), 2)}
            for i in range(n)]
    t = time.perf_counter()
    db[coll].insert_many(docs, ordered=False)
    ms = round((time.perf_counter() - t) * 1000)
    o1 = opcounters()
    after = {s: v["count"] for s, v in shard_counts(HOT_DB, coll).items()}
    return {"collection": coll, "inserted": n, "ms": ms, "added": {s: after[s] - before[s] for s in after},
            "total": after, "opcounters": {s: (o1[s] - o0[s]) if o1.get(s) is not None and o0.get(s) is not None else None
                                           for s in after},
            "from": start, "to": docs[-1]["ts"]}


# ============================================================================ Activity 11.1 - resharding
RESHARD_STATE: dict = {"running": False}


def _cfg_primary() -> MongoClient | None:
    _, hosts = parse_rs(CFG_RS)
    p = _primary_of(hosts)
    return direct(p) if p else None


def reshard_demo(n: int = 12_000, *, min_duration_ms: int = 5000, writer_interval: float = 0.02, log=print,
                 state: dict | None = None, initial_chunks: int = 32) -> dict:
    """Load orders with the BAD key {order_date: 1}, then reshardCollection to {customer_id: 'hashed'} while a
    writer keeps inserting. Records the phases (from $currentOp), the write latency and the before/after picture."""
    st = state if state is not None else {}
    st.update({"running": True, "phase": "loading data", "timeline": [], "error": None, "started": datetime.now(UTC)})
    c = client()
    name, ns = "orders", f"{RESHARD_DB}.orders"
    shards = shard_names()
    db = c[RESHARD_DB]
    try:
        orders = gen_orders(n, seed=7)
        h = int(n * 0.2)
        prepare_db(RESHARD_DB, log)
        db.create_collection(name)
        db[name].create_index([("customer_id", ASCENDING), ("order_date", DESCENDING)])
        c.admin.command("shardCollection", ns, key={"order_date": 1})
        with balancer_paused():
            db[name].insert_many([dict(o) for o in orders[:h]], ordered=False)
            split_and_move(RESHARD_DB, name, "order_date", sorted(o["order_date"] for o in orders[:h])[h // 2], shards[1])
            db[name].insert_many([dict(o) for o in orders[h:]], ordered=False)
        cust = orders[int(n * 0.9)]["customer_id"]
        before = {"by_shard": {s: v["count"] for s, v in shard_counts(RESHARD_DB, name).items()},
                  "chunks": chunk_map(RESHARD_DB, name), "key": "{order_date: 1}",
                  "explain_customer": explain_query(RESHARD_DB, name, {"customer_id": cust}, runs=5)}
        log(f"Before: shard key {{order_date: 1}}  docs by shard {before['by_shard']}  "
            f"customer query -> {before['explain_customer']['n_shards']} shard(s) ({before['explain_customer']['kind']})")

        cfg = _cfg_primary()
        was = None
        if cfg is not None and min_duration_ms:
            was = cfg.admin.command("setParameter", 1, reshardingMinimumOperationDurationMillis=min_duration_ms).get("was")
            log(f"Lab setting: reshardingMinimumOperationDurationMillis {was} -> {min_duration_ms} on the config server "
                f"(default {was / 60000:.0f} min would make the demo wait 5 minutes; restored afterwards)")
        bal_was = c.admin.command("balancerStatus").get("mode")
        c.admin.command("balancerStop")
        log("Step 1  sh.stopBalancer()   (the manual says: turn the balancer off before resharding)")

        stop = threading.Event()
        lat, errs = [], []
        t_start = time.time()

        def writer():
            R = random.Random(3)
            last = orders[-1]["order_date"]
            i = 0
            wc = MongoClient(SHARD_URI, serverSelectionTimeoutMS=8000, appname="lab11-writer")
            col = wc[RESHARD_DB][name]
            while not stop.is_set():
                i += 1
                doc = {"order_id": f"LIVE-{i:06d}", "customer_id": f"C-{R.randint(1, 2000):06d}",
                       "order_date": last + timedelta(seconds=30 * i), "category": "Electronics", "items": 1,
                       "amount": 999.0, "city": "Bengaluru", "payment": "UPI", "status": "placed"}
                t = time.perf_counter()
                try:
                    col.insert_one(doc)
                    lat.append((round(time.time() - t_start, 2), round((time.perf_counter() - t) * 1000, 1)))
                except PyMongoError as exc:
                    errs.append(f"{type(exc).__name__}: {str(exc)[:100]}")
                time.sleep(writer_interval)
            wc.close()

        seen = []

        def note(src, state_name, extra=None):
            key = (src, state_name)
            if state_name and key not in [(x["source"], x["state"]) for x in seen]:
                ev = {"t": round(time.time() - t_start, 1), "source": src, "state": state_name, **(extra or {})}
                seen.append(ev)
                st["timeline"] = list(seen)
                st["phase"] = f"{src}: {state_name}"
                log(f"   t={ev['t']:>5.1f}s  {src:<12} {state_name}" + (f"  {extra}" if extra else ""))

        def monitor():
            mc = MongoClient(SHARD_URI, serverSelectionTimeoutMS=3000, appname="lab11-monitor")
            while not stop.is_set():
                try:
                    coord = mc.config.reshardingOperations.find_one({"ns": ns})
                    if coord:
                        note("coordinator", coord.get("state"))
                    for o in mc.admin.aggregate([{"$currentOp": {"allUsers": True, "localOps": False}},
                                                 {"$match": {"type": "op", "originatingCommand.reshardCollection": ns}}]):
                        desc = o.get("desc", "")
                        role = "donor" if "Donor" in desc else "recipient" if "Recipient" in desc else "coordinator"
                        s = o.get("donorState") or o.get("recipientState") or o.get("coordinatorState")
                        extra = {}
                        if role == "recipient" and o.get("approxDocumentsToCopy") is not None:
                            extra = {"copied": o.get("documentsCopied"), "to_copy": o.get("approxDocumentsToCopy"),
                                     "oplog_applied": o.get("oplogEntriesApplied")}
                            st["progress"] = extra | {"shard": o.get("shard")}
                        note(f"{role}:{o.get('shard', '')}" if role != "coordinator" else "coordinator", s, extra)
                except PyMongoError:
                    pass
                time.sleep(0.4)
            mc.close()

        th_w, th_m = threading.Thread(target=writer, daemon=True), threading.Thread(target=monitor, daemon=True)
        th_w.start()
        th_m.start()
        time.sleep(1.0)
        log("Step 2  db.adminCommand({reshardCollection: '%s', key: {customer_id: 'hashed'}, numInitialChunks: %d})"
            % (ns, initial_chunks))
        st["phase"] = "reshardCollection running"
        refused, used_chunks = [], None
        t0 = time.time()
        try:
            # numInitialChunks: 7.0 defaults to 90 chunks and samples 10 documents per chunk. On this skewed order data
            # (loyal customers repeat) that once failed with "can only make 89 chunks". Too FEW chunks is bad too: the
            # boundaries come from fewer samples and the shards get unequal hash ranges (8 chunks gave 70/30).
            # So: 32 chunks, and if MongoDB still refuses for cardinality, 16, then 8 - every refusal is recorded.
            for nchunks in sorted({initial_chunks, 16, 8}, reverse=True):
                if nchunks > initial_chunks:
                    continue
                try:
                    c.admin.command("reshardCollection", ns, key={"customer_id": "hashed"}, numInitialChunks=nchunks)
                    used_chunks = nchunks
                    break
                except OperationFailure as exc:
                    if exc.code != 4952606 or nchunks == 8:
                        raise
                    refused.append({"numInitialChunks": nchunks, "error": str(exc.details.get("errmsg", exc))[:200]})
                    log(f"   MongoDB refused numInitialChunks {nchunks}: {refused[-1]['error']}  -> retrying with fewer")
            ok = True
        finally:
            took = round(time.time() - t0, 1)
            time.sleep(1.0)
            stop.set()
            th_w.join(5)
            th_m.join(5)
            if cfg is not None and was is not None:
                cfg.admin.command("setParameter", 1, reshardingMinimumOperationDurationMillis=was)
            if bal_was != "off":
                c.admin.command("balancerStart")
        log(f"Step 3  monitored with $currentOp (see the phase list above)")
        log(f"Step 4  sh.startBalancer();  reshardCollection returned after {took} s")
        after = {"by_shard": {s: v["count"] for s, v in shard_counts(RESHARD_DB, name).items()},
                 "chunks": chunk_map(RESHARD_DB, name),
                 "key": key_text(c.config.collections.find_one({"_id": ns})["key"]),
                 "explain_customer": explain_query(RESHARD_DB, name, {"customer_id": cust}, runs=5)}
        ms = sorted(x[1] for x in lat)
        worst = max(lat, key=lambda x: x[1]) if lat else (None, None)
        writes = {"count": len(lat), "errors": len(errs), "error_samples": errs[:3],
                  "p50_ms": ms[len(ms) // 2] if ms else None, "p99_ms": ms[int(len(ms) * 0.99) - 1] if ms else None,
                  "max_ms": worst[1], "max_at_s": worst[0],
                  "series": sorted(set(lat[:: max(1, len(lat) // 400)]) | ({worst} if lat else set()))}
        log(f"After:  shard key {after['key']}  docs by shard {after['by_shard']}  customer query -> "
            f"{after['explain_customer']['n_shards']} shard(s) ({after['explain_customer']['kind']})")
        log(f"Writes during resharding: {writes['count']:,} inserts, {writes['errors']} errors, p50 {writes['p50_ms']} ms, "
            f"p99 {writes['p99_ms']} ms, max {writes['max_ms']} ms at t={writes['max_at_s']} s (the write-blocking "
            f"critical section)")
        res = {"ok": ok, "ns": ns, "n": n, "seconds": took, "before": before, "after": after, "writes": writes,
               "timeline": seen, "min_duration_default_ms": was, "min_duration_used_ms": min_duration_ms,
               "probe_customer": cust, "num_initial_chunks": used_chunks, "refused": refused,
               "steps": RESHARD_STEPS, "limits": RESHARD_LIMITS}
        st.update({"running": False, "phase": "done", "result": res, "finished": datetime.now(UTC)})
        return res
    except Exception as exc:                                  # noqa: BLE001
        st.update({"running": False, "phase": "failed", "error": f"{type(exc).__name__}: {exc}"})
        raise


# Facts for the write-up, checked against the MongoDB 7.0 binary (getParameter) and the MongoDB manual.
RESHARD_STEPS = [
    ("Plan and check resources", "Pick the new key; free storage per recipient shard >= ((collection storage size + "
     "index size) x 2) / number of shards; I/O below 50 %, CPU below 80 %; no index build may be running."),
    ("Stop the balancer", "sh.stopBalancer() - the manual requires the balancer off for the whole operation."),
    ("Start", "db.adminCommand({reshardCollection: 'db.coll', key: {...}}) on mongos. The config server primary "
     "becomes the coordinator; every shard is a donor (old chunks), a recipient (new chunks) or both."),
    ("Initialise", "The coordinator samples the collection and computes the new chunk boundaries; each recipient "
     "creates a temporary collection system.resharding.<uuid>."),
    ("Clone", "Recipients copy the documents that belong to them from every donor at one snapshot time."),
    ("Build indexes", "Recipients build the indexes of the collection (including the new shard-key index)."),
    ("Catch up (applying)", "Recipients replay the donors' oplog entries written since the clone started, while "
     "the application keeps writing."),
    ("Block writes (critical section)", "When the estimated remaining time is under about 2 s the coordinator "
     "blocks writes to the collection; if it cannot finish within reshardingCriticalSectionTimeoutMillis (5 s) the "
     "operation aborts."),
    ("Commit", "The coordinator switches the routing table to the new key; the temporary collection becomes the "
     "collection and donors drop the old data. sh.startBalancer() afterwards."),
]
RESHARD_LIMITS = [
    "Available from MongoDB 5.0; only ONE collection in the cluster can be resharded at a time.",
    "Minimum duration: 5 minutes by default (reshardingMinimumOperationDurationMillis = 300000 on 7.0) - "
    "this lab lowers it to 5 s for the demonstration and restores it afterwards.",
    "Writes to the collection are blocked for up to about 2 seconds at the end; the application sees a latency spike.",
    "Storage: each recipient shard needs about twice (collection + indexes) divided by the number of shards free; "
    "I/O and CPU headroom are recommended but not enforced.",
    "While it runs, collMod, convertToCapped, createIndexes, dropIndexes, drop and renameCollection on the collection "
    "are refused; addShard, removeShard and dropDatabase wait until it finishes.",
    "writeConcernMajorityJournalDefault must be true; existing unique indexes must still be valid for the new key; "
    "duplicate _id values across shards make it fail.",
    "Not supported on 7.0: sharded time-series collections, collections with Queryable Encryption, and resharding to "
    "the SAME key (that redistribution option came in a later release).",
    "Retryable writes started before or during resharding can be retried for 5 minutes; queries that use the old key "
    "instead of the new one may need the slower two-phase write protocol, so the application should switch to the "
    "new key.",
    "It generates a lot of oplog traffic: size the oplog so secondaries do not fall behind.",
    "The new key needs enough DISTINCT values in MongoDB's sample: 7.0 asks for 90 initial chunks by default and "
    "samples 10 documents per chunk. On our skewed order data (loyal customers repeat) it failed with \"The shard key "
    "provided does not have enough cardinality to make the required number of chunks of 90, it can only make 89 "
    "chunks\" - observed in this lab; fixed by passing numInitialChunks explicitly (32 here: fewer chunks means fewer samples and uneven hash ranges).",
]

"""Shared connections for Labs 16-18 (polyglot persistence): Cassandra, Neo4j and the MongoDB Atlas Local server.

    Cassandra   cassandra:9042               (CASSANDRA_HOSTS, CASSANDRA_PORT, CASSANDRA_DC)       - cassandra-driver
    Neo4j       bolt://neo4j:7687            (NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)              - neo4j driver
    Vector      mongodb://atlas-local:27017  (VECTOR_URI) MongoDB 8 + mongot = $vectorSearch locally (Lab 17)
    MongoDB     the Lab 7-10 replica set rs0 (MONGO_URI) - operational data of Labs 16 and 18

One driver object per database for the whole process (each keeps its own connection pool):
    PyMongo   maxPoolSize=10 in the URI (the Lab 16.2 solution)
    Cassandra protocol v5 multiplexes many requests over one connection per host; executor_threads in the Cluster
    Neo4j     max_connection_pool_size in the driver
"""
from __future__ import annotations

import os
import statistics
import threading
import time

from pymongo import MongoClient

CASS_HOSTS = [h.strip() for h in os.getenv("CASSANDRA_HOSTS", "cassandra").split(",") if h.strip()]
CASS_PORT = int(os.getenv("CASSANDRA_PORT", "9042"))
CASS_DC = os.getenv("CASSANDRA_DC", "datacenter1")
CASS_THREADS = int(os.getenv("CASSANDRA_EXECUTOR_THREADS", "4"))
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "Lab16-neo4j!")
NEO4J_POOL = int(os.getenv("NEO4J_POOL", "20"))
VECTOR_URI = os.getenv("VECTOR_URI", "mongodb://atlas-local:27017/?directConnection=true")
MONGO_POOL = int(os.getenv("POLYGLOT_MONGO_POOL", "10"))

_lock = threading.Lock()
_cass: dict = {"cluster": None, "session": None}
_neo: dict = {"driver": None}
_vec: dict = {"client": None}
_avail: dict = {"at": 0.0, "value": None}


# ============================================================================ Cassandra
def cassandra():
    """The shared Cassandra session (rows come back as dicts). Every CQL statement names its keyspace."""
    with _lock:
        if _cass["session"] is None:
            from cassandra.cluster import EXEC_PROFILE_DEFAULT, Cluster, ExecutionProfile
            from cassandra.policies import DCAwareRoundRobinPolicy, TokenAwarePolicy
            from cassandra.query import dict_factory
            prof = ExecutionProfile(load_balancing_policy=TokenAwarePolicy(DCAwareRoundRobinPolicy(local_dc=CASS_DC)),
                                    row_factory=dict_factory, request_timeout=60)
            cl = Cluster(CASS_HOSTS, port=CASS_PORT, execution_profiles={EXEC_PROFILE_DEFAULT: prof},
                         connect_timeout=10, executor_threads=CASS_THREADS)
            _cass["cluster"], _cass["session"] = cl, cl.connect()
        return _cass["session"]


def cassandra_reset():
    with _lock:
        if _cass["cluster"] is not None:
            try:
                _cass["cluster"].shutdown()
            except Exception:                         # noqa: BLE001
                pass
        _cass.update({"cluster": None, "session": None})


def cql_all(cql: str, params=None) -> list:
    return list(cassandra().execute(cql, params))


def keyspace(name: str, rf: int = 1):
    cassandra().execute(f"CREATE KEYSPACE IF NOT EXISTS {name} WITH replication = "
                        f"{{'class': 'SimpleStrategy', 'replication_factor': {rf}}}")


def bulk(cql: str, rows: list, concurrency: int = 64) -> int:
    """Prepared statement + execute_concurrent_with_args (the fast way to load Cassandra from Python)."""
    from cassandra.concurrent import execute_concurrent_with_args
    if not rows:
        return 0
    s = cassandra()
    ps = s.prepare(cql)
    done = 0
    for i in range(0, len(rows), 5000):
        res = execute_concurrent_with_args(s, ps, rows[i:i + 5000], concurrency=concurrency, raise_on_first_error=True)
        done += sum(1 for ok, _ in res if ok)
    return done


# ============================================================================ Neo4j
def neo4j():
    with _lock:
        if _neo["driver"] is None:
            from neo4j import GraphDatabase
            _neo["driver"] = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD),
                                                  max_connection_pool_size=NEO4J_POOL, connection_timeout=10,
                                                  max_transaction_retry_time=8)
        return _neo["driver"]


def cypher(q: str, **params) -> list:
    """Run one Cypher statement, return a list of dicts."""
    recs, _, _ = neo4j().execute_query(q, params, database_="neo4j")
    return [r.data() for r in recs]


def cypher_write(q: str, **params):
    recs, summary, _ = neo4j().execute_query(q, params, database_="neo4j")
    return summary.counters


def neo4j_reset():
    with _lock:
        if _neo["driver"] is not None:
            try:
                _neo["driver"].close()
            except Exception:                         # noqa: BLE001
                pass
        _neo["driver"] = None


# ============================================================================ MongoDB Atlas Local (Lab 17 vector search)
def vector_client() -> MongoClient:
    with _lock:
        if _vec["client"] is None:
            _vec["client"] = MongoClient(VECTOR_URI, serverSelectionTimeoutMS=5000, maxPoolSize=MONGO_POOL, tz_aware=True,
                                         appname="lab17-vector")
        return _vec["client"]


def mongo():
    """The Lab 7-10 replica set (operational data of Labs 16 and 18)."""
    from .db import client
    return client()


# ============================================================================ health
def _try(fn):
    t0 = time.perf_counter()
    try:
        v = fn()
        return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1), "version": v}
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def _cass_version():
    r = cassandra().execute("SELECT release_version FROM system.local").one()
    return r["release_version"]


def _neo_version():
    neo4j().verify_connectivity()                      # fails fast when the server is down (no retries)
    r = cypher("CALL dbms.components() YIELD name, versions, edition RETURN name, versions[0] AS v, edition")
    return f"{r[0]['v']} {r[0]['edition']}"


def _vec_version():
    c = vector_client()
    v = c.server_info()["version"]
    try:                                               # mongot present = search commands work
        c["lab17_probe"]["probe"].update_one({"_id": 1}, {"$set": {"x": 1}}, upsert=True)
        list(c["lab17_probe"]["probe"].list_search_indexes())
        return f"{v} + mongot"
    except Exception as exc:                          # noqa: BLE001
        return f"{v} (no search: {str(exc)[:60]})"


def availability(force: bool = False) -> dict:
    now = time.time()
    if not force and _avail["value"] and now - _avail["at"] < 5:
        return _avail["value"]
    v = {"mongodb": _try(lambda: mongo().server_info()["version"]),
         "cassandra": _try(_cass_version),
         "neo4j": _try(_neo_version),
         "vector": _try(_vec_version)}
    if not v["cassandra"]["ok"]:
        cassandra_reset()
    if not v["neo4j"]["ok"]:
        neo4j_reset()
    v["hosts"] = {"cassandra": f"{','.join(CASS_HOSTS)}:{CASS_PORT}", "neo4j": NEO4J_URI, "vector": VECTOR_URI.split("?")[0]}
    _avail.update({"at": now, "value": v})
    return v


def wait_for(names=("mongodb", "cassandra", "neo4j"), seconds: int = 300, log=print) -> dict:
    t0 = time.time()
    while True:
        a = availability(force=True)
        missing = [n for n in names if not a[n]["ok"]]
        if not missing:
            log("   " + ", ".join(f"{n} {a[n]['version']}" for n in names))
            return a
        if time.time() - t0 > seconds:
            raise RuntimeError("not reachable: " + "; ".join(f"{n}: {a[n].get('error')}" for n in missing))
        time.sleep(4)


# ============================================================================ timing
def timed(fn, runs: int = 10, warmup: int = 1) -> dict:
    """Run fn() `runs` times (after warm-up), return median / p95 / min / max in milliseconds + the last result."""
    out = None
    for _ in range(warmup):
        out = fn()
    ms = []
    for _ in range(runs):
        t0 = time.perf_counter()
        out = fn()
        ms.append((time.perf_counter() - t0) * 1000)
    s = sorted(ms)
    return {"median_ms": round(statistics.median(s), 3), "p95_ms": round(s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))], 3),
            "min_ms": round(s[0], 3), "max_ms": round(s[-1], 3), "runs": len(s), "all_ms": [round(x, 3) for x in ms], "result": out}

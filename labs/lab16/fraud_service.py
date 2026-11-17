"""Lab 16.2 - FraudQueryService: one Python service that queries MongoDB, Cassandra and Neo4j for the same fraud
detection dataset and merges the results (polyglot persistence).

    MongoDB    get_transaction(txn_id)            flexible document lookup       db.transactions.find_one({txn_id})
    Cassandra  get_user_profile(user_id)          time-ordered events            user_profiles partition = user_id,
                                                                                 clustering event_time DESC
    Neo4j      detect_fraud_rings(user_id, hops)  multi-hop traversal            (:User)-[:TRANSFERRED_TO*1..3]-(:User)
    analyze_user(user_id)                         the full fraud analysis workflow: profile + transactions + rings,
                                                  degraded (not failed) when one backend is down

Connection pooling: PyMongo maxPoolSize=10 (in the URI), Cassandra executor_threads in the Cluster, Neo4j
max_connection_pool_size in the driver. Circuit breaker: three consecutive failures of a backend -> it is marked
unavailable and skipped; after 30 seconds one trial call is allowed (half-open); success closes the circuit again.
The drivers are injected, so the unit tests (test_fraud_service.py) replace them with unittest.mock objects.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class CircuitOpen(RuntimeError):
    """Raised (and caught by the service) when a backend is skipped because its circuit is open."""


class CircuitBreaker:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, name: str, threshold: int = 3, reset_after: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.name, self.threshold, self.reset_after, self.clock = name, threshold, reset_after, clock
        self.failures, self.opened_at, self._state = 0, None, self.CLOSED
        self.lock = threading.Lock()
        self.history: list[dict] = []                    # state changes, for the website / report

    @property
    def state(self) -> str:
        with self.lock:
            if self._state == self.OPEN and self.clock() - self.opened_at >= self.reset_after:
                self._move(self.HALF_OPEN, "reset timeout elapsed - one trial call allowed")
            return self._state

    def _move(self, new: str, why: str):
        if new != self._state:
            self.history.append({"backend": self.name, "from": self._state, "to": new, "why": why, "t": round(self.clock(), 3)})
            self._state = new

    def call(self, fn: Callable[[], Any]):
        st = self.state
        if st == self.OPEN:
            raise CircuitOpen(f"{self.name} unavailable (circuit open, retry in "
                              f"{max(0.0, self.reset_after - (self.clock() - self.opened_at)):.0f} s)")
        try:
            out = fn()
        except Exception:
            with self.lock:
                self.failures += 1
                if self._state == self.HALF_OPEN or self.failures >= self.threshold:
                    self.opened_at = self.clock()
                    self._move(self.OPEN, f"{self.failures} consecutive failure(s)")
            raise
        with self.lock:
            self.failures = 0
            self._move(self.CLOSED, "call succeeded")
        return out

    def snapshot(self) -> dict:
        return {"backend": self.name, "state": self.state, "failures": self.failures, "threshold": self.threshold,
                "reset_after_s": self.reset_after}


class FraudQueryService:
    MAX_HOPS = 6

    def __init__(self, mongo_client, cassandra_session, neo4j_driver, *, mongo_db: str = "lab16_fraud",
                 keyspace: str = "lab16", threshold: int = 3, reset_after: float = 30.0, clock=time.monotonic):
        """mongo_client  = MongoClient('mongodb://...&maxPoolSize=10')
           cassandra_session = Cluster([...], executor_threads=4).connect()
           neo4j_driver  = GraphDatabase.driver(uri, auth=..., max_connection_pool_size=20)"""
        self.mongo, self.cass, self.neo = mongo_client, cassandra_session, neo4j_driver
        self.db, self.ks = mongo_client[mongo_db], keyspace
        self.breakers = {n: CircuitBreaker(n, threshold, reset_after, clock) for n in ("mongodb", "cassandra", "neo4j")}
        self.ring_min_amount = 10000.0                   # transfers smaller than this are not considered for rings
        self.faults: set[str] = set()                    # fault injection for the demo ("this backend is down")
        self._ps = None

    # ------------------------------------------------------------------ helpers
    def _guard(self, backend: str, fn):
        def run():
            if backend in self.faults:
                raise ConnectionError(f"{backend}: injected outage (no route to host)")
            return fn()
        return self.breakers[backend].call(run)

    def inject_fault(self, backend: str, on: bool = True):
        (self.faults.add if on else self.faults.discard)(backend)

    def health(self) -> list[dict]:
        return [b.snapshot() for b in self.breakers.values()]

    # ------------------------------------------------------------------ MongoDB: flexible document retrieval
    def get_transaction(self, txn_id: str) -> dict | None:
        return self._guard("mongodb", lambda: self.db.transactions.find_one({"txn_id": txn_id}, {"_id": 0}))

    def recent_transactions(self, user_id: str, limit: int = 10) -> list[dict]:
        return self._guard("mongodb", lambda: list(self.db.transactions.find({"user_id": user_id}, {"_id": 0})
                                                   .sort("timestamp", -1).limit(limit)))

    # ------------------------------------------------------------------ Cassandra: time-ordered events of one partition
    def get_user_profile(self, user_id: str, limit: int = 10) -> list[dict]:
        def q():
            if self._ps is None:
                self._ps = self.cass.prepare(f"SELECT user_id, event_time, txn_id, event_type, amount, merchant, country "
                                             f"FROM {self.ks}.user_profiles WHERE user_id = ? ORDER BY event_time DESC LIMIT ?")
            return [dict(r) for r in self.cass.execute(self._ps, (user_id, int(limit)))]
        return self._guard("cassandra", q)

    # ------------------------------------------------------------------ Neo4j: multi-hop traversal of the transfer graph
    def detect_fraud_rings(self, user_id: str, max_hops: int = 3) -> dict:
        hops = int(max_hops)
        if not 1 <= hops <= self.MAX_HOPS:              # a variable-length bound cannot be a parameter -> validate it
            raise ValueError(f"max_hops must be 1..{self.MAX_HOPS}")

        def q():
            with self.neo.session(database="neo4j") as s:
                connected = s.run(f"MATCH path = (u:User {{id: $uid}})-[:TRANSFERRED_TO*1..{hops}]-(c:User) "
                                  "WHERE c <> u RETURN c.id AS user, min(length(path)) AS hops, count(path) AS paths "
                                  "ORDER BY hops, paths DESC LIMIT 50", uid=user_id).data()
                # a ring = money that comes back to the user: a cycle of large transfers (>= min_amount) within 24 hours
                rings = s.run(f"MATCH path = (u:User {{id: $uid}})-[:TRANSFERRED_TO*2..{min(2 * hops, 8)}]->(u) "
                              "WITH path, relationships(path) AS rs WHERE all(r IN rs WHERE r.amount >= $min_amount) "
                              "WITH path, rs, reduce(a = rs[0].ts, r IN rs | CASE WHEN r.ts < a THEN r.ts ELSE a END) AS t0, "
                              "reduce(b = rs[0].ts, r IN rs | CASE WHEN r.ts > b THEN r.ts ELSE b END) AS t1 "
                              "WHERE duration.inSeconds(t0, t1).seconds <= 86400 "
                              "RETURN [n IN nodes(path) | n.id] AS ring, reduce(t = 0.0, r IN rs | t + r.amount) AS total, "
                              "duration.inSeconds(t0, t1).minutes AS minutes LIMIT 10", uid=user_id, min_amount=self.ring_min_amount).data()
            return {"connected": connected, "rings": rings}
        return self._guard("neo4j", q)

    # ------------------------------------------------------------------ the full fraud analysis workflow (merged)
    def analyze_user(self, user_id: str, max_hops: int = 3) -> dict:
        out: dict = {"user_id": user_id, "sources": {}, "degraded": []}
        t0 = time.perf_counter()
        for key, backend, fn in (("transactions", "mongodb", lambda: self.recent_transactions(user_id)),
                                 ("profile", "cassandra", lambda: self.get_user_profile(user_id)),
                                 ("graph", "neo4j", lambda: self.detect_fraud_rings(user_id, max_hops))):
            t = time.perf_counter()
            try:
                out[key] = fn()
                out["sources"][backend] = {"ok": True, "ms": round((time.perf_counter() - t) * 1000, 2)}
            except CircuitOpen as exc:
                out[key] = None
                out["sources"][backend] = {"ok": False, "skipped": True, "error": str(exc), "ms": round((time.perf_counter() - t) * 1000, 3)}
                out["degraded"].append(backend)
            except Exception as exc:                     # noqa: BLE001
                out[key] = None
                out["sources"][backend] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                                           "ms": round((time.perf_counter() - t) * 1000, 2)}
                out["degraded"].append(backend)
        out["risk"] = self.score(out)
        out["total_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return out

    @staticmethod
    def score(r: dict) -> dict:
        """Merged verdict: ring membership (Neo4j) + velocity (Cassandra) + risky documents (MongoDB)."""
        reasons, pts = [], 0
        g = r.get("graph") or {}
        if g.get("rings"):
            pts += 50; reasons.append(f"member of {len(g['rings'])} transfer cycle(s) (Neo4j)")
        if len(g.get("connected") or []) >= 8:
            pts += 10; reasons.append(f"{len(g['connected'])} accounts within reach (Neo4j)")
        prof = r.get("profile") or []
        if len(prof) >= 2:
            ts = sorted(p["event_time"] for p in prof if p.get("event_time"))
            if len(ts) >= 5 and (ts[-1] - ts[-5]).total_seconds() < 600:
                pts += 20; reasons.append("5 events within 10 minutes (Cassandra)")
        tx = r.get("transactions") or []
        hi = [t for t in tx if (t.get("risk_score") or 0) >= 0.8]
        if hi:
            pts += 20; reasons.append(f"{len(hi)} high-risk transaction(s) (MongoDB)")
        level = "high" if pts >= 50 else "medium" if pts >= 20 else "low"
        if r.get("degraded"):
            reasons.append("partial result - skipped: " + ", ".join(r["degraded"]))
        return {"score": min(100, pts), "level": level, "reasons": reasons}

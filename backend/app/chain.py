"""Lab dependency chain (mentor's rule) + shared status/log store.

    Lab 18  needs  Lab 17  needs  Lab 16  needs  Lab 15  needs  Lab 14  needs  Lab 13  needs  Lab 12  needs  Lab 11  needs  Lab 10  needs  Lab 9  needs  Lab 8  needs  Lab 7

Starting a lab therefore activates every lab below it first, in order: `Lab 18` runs 7 -> 8 -> ... -> 17 -> 18.
The status of every lab is kept in MongoDB (cti_platform.lab_chain) so the website, the Docker
containers and the command line all see the same picture.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from pymongo.errors import CollectionInvalid, OperationFailure

UTC = timezone.utc
DEPENDS = {7: None, 8: 7, 9: 8, 10: 9, 11: 10, 12: 11, 13: 12, 14: 13, 15: 14, 16: 15, 17: 16, 18: 17}     # lab -> the lab it requires
TITLES = {
    7: "Lab 7 - Kavach CTI platform, schema design and working set",
    8: "Lab 8 - Replica set, failover and elections",
    9: "Lab 9 - Write concern latency and network partitions",
    10: "Lab 10 - Transactions, atomicity, durability and journaling",
    11: "Lab 11 - Sharding and horizontal scalability",
    12: "Lab 12 - Database security and the CIA triad",
    13: "Lab 13 - Deploying MongoDB with containers and the cloud",
    14: "Lab 14 - Managing database services in production",
    15: "Lab 15 - A MongoDB-backed product as a service",
    16: "Lab 16 - Wide-column and graph databases: Cassandra and Neo4j",
    17: "Lab 17 - AI-native MongoDB: vector search and RAG",
    18: "Lab 18 - Comparative capstone: false positives in threat intelligence",
}
SHORT = {7: "Platform + schema", 8: "Replica set", 9: "Write concern", 10: "Transactions", 11: "Sharding", 12: "Security", 13: "Containers + cloud", 14: "Operations", 15: "SaaS product", 16: "Cassandra + Neo4j", 17: "Vector search + RAG", 18: "Capstone"}


def chain_for(lab: int) -> list[int]:
    """[7, 8, ..., 18][:...] - every lab that must be active before `lab` (and lab itself)."""
    if lab not in DEPENDS:
        raise ValueError(f"unknown lab {lab} (use 7 to 18)")
    out, cur = [], lab
    while cur is not None:
        out.append(cur)
        cur = DEPENDS[cur]
    return list(reversed(out))


def _coll(db):
    return db["lab_chain"]


def set_status(db, lab: int, **fields):
    fields["updated_at"] = datetime.now(UTC)
    _coll(db).update_one({"_id": f"lab{lab}"}, {"$set": {"lab": lab, "title": TITLES[lab], **fields}}, upsert=True)


def reset_for_run(db, labs: list[int], target: int, mode_of: dict):
    """Mark the labs of a new run as pending (so the page shows the whole chain at once)."""
    for lab in labs:
        set_status(db, lab, status="pending", mode=mode_of[lab], requested_by=target, steps=[],
                   started_at=None, finished_at=None, seconds=None, error=None)


def _log_coll(db):
    try:
        db.create_collection("lab_chain_log", capped=True, size=2 * 1024 * 1024, max=4000)
    except (CollectionInvalid, OperationFailure):
        pass
    return db["lab_chain_log"]


def log(db, lab: int, line: str):
    try:
        _log_coll(db).insert_one({"at": datetime.now(UTC), "lab": lab, "line": line.rstrip()[:600]})
    except Exception:                                    # logging must never break a lab
        pass


def snapshot(db, tail: int = 160) -> dict:
    docs = {d["lab"]: d for d in _coll(db).find({}, {"_id": 0})}
    labs = []
    for lab in sorted(DEPENDS):
        d = docs.get(lab) or {"lab": lab, "title": TITLES[lab], "status": "idle", "steps": []}
        d["requires"] = DEPENDS[lab]
        d["short"] = SHORT[lab]
        labs.append(d)
    lines = list(_log_coll(db).find({}, {"_id": 0}).sort("$natural", -1).limit(tail))
    lines.reverse()
    running = any(d.get("status") in ("running", "pending") and
                  (datetime.now(UTC) - (d.get("updated_at") or datetime(1970, 1, 1, tzinfo=UTC))).total_seconds() < 1800
                  for d in labs)
    return {"labs": labs, "log": lines, "running": running, "now": datetime.now(UTC)}


def elapsed(t0: float) -> float:
    return round(time.perf_counter() - t0, 1)

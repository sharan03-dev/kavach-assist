#!/usr/bin/env python3
"""Lab 15.2 - Data migration between tenant isolation strategies.

    python migrate_tenants.py --uri mongodb://localhost:27017 [--db tasktracker_mig] [--quick]

  1. seed      document-level isolation: ONE `tasks` collection, 5 tenants x 100 tasks {_id, tenant_id, title, status,
               priority, created_at} + index (tenant_id, created_at)
  2. migrate   collection-per-tenant: db.tasks.aggregate([{$match: {tenant_id: tid}}, {$out: 'tasks_tenant_' + tid}])
               (the Python insert_many loop is timed as well)
  3. validate  counts per tenant, every document in tasks_tenant_X has tenant_id X, the same _ids and the same content
               (sha256) in both layouts, no _id in two tenant collections; a deliberate leak proves the check can fail
  4. compare   list a tenant's tasks, create tasks, cross-tenant analytics ($group vs $unionWith) - wall clock (time
               module) and explain() - at 5 x 100 (the lab), 5 x 20,000 and 100 tenants x 100
  5. backup    restore ONE tenant (Lab 14 tools): per-tenant collection vs --query on the shared collection
  6. evolve    schema change (new field + validator) in both layouts
  7. recommend  when each strategy is appropriate - from the measurements
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import json_util
from pymongo import ASCENDING, DESCENDING, MongoClient

UTC = timezone.utc
TENANTS5 = ["acme", "globex", "initech", "umbrella", "hooli"]
HERE = Path(__file__).resolve().parent


def say(m=""):
    print(m, flush=True)


def tcoll(tid: str) -> str:
    return f"tasks_tenant_{tid}"


# ============================================================================ 1. seed (document-level isolation)
def seed(db, tenants: list, per: int) -> dict:
    for n in db.list_collection_names():
        if n == "tasks" or n.startswith("tasks_tenant_"):
            db.drop_collection(n)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rnd = random.Random(15)
    t0 = time.perf_counter()
    batch = []
    for tid in tenants:
        for i in range(per):
            batch.append({"tenant_id": tid, "title": f"{tid} task {i}", "status": rnd.choice(["open", "in_progress", "done"]),
                          "priority": rnd.choice(["low", "medium", "high"]),
                          "created_at": base + timedelta(minutes=rnd.randrange(0, 525600)),
                          "description": "x" * rnd.randrange(40, 200)})
            if len(batch) == 5000:
                db.tasks.insert_many(batch); batch = []
    if batch:
        db.tasks.insert_many(batch)
    db.tasks.create_index([("tenant_id", ASCENDING), ("created_at", DESCENDING)], name="tenant_created")
    return {"tenants": len(tenants), "per_tenant": per, "documents": db.tasks.count_documents({}),
            "seconds": round(time.perf_counter() - t0, 3)}


# ============================================================================ 2. migrate (collection-per-tenant)
def migrate(db, tenants: list, method: str = "out") -> dict:
    rows = []
    t_all = time.perf_counter()
    for tid in tenants:
        t0 = time.perf_counter()
        if method == "out":
            db.drop_collection(tcoll(tid))           # fresh target: $out into an EXISTING collection keeps its old indexes
            db.tasks.aggregate([{"$match": {"tenant_id": tid}}, {"$out": tcoll(tid)}])
        else:
            db.drop_collection(tcoll(tid))
            docs = list(db.tasks.find({"tenant_id": tid}))
            if docs:
                db[tcoll(tid)].insert_many(docs)
        idx_before = sorted(i["name"] for i in db[tcoll(tid)].list_indexes())
        db[tcoll(tid)].create_index([("created_at", DESCENDING)], name="created")    # $out does NOT copy indexes
        rows.append({"tenant": tid, "collection": tcoll(tid), "seconds": round(time.perf_counter() - t0, 4),
                     "indexes_after_out": idx_before})
    return {"method": "$match + $out" if method == "out" else "find + insert_many", "tenants": rows,
            "seconds": round(time.perf_counter() - t_all, 3)}


# ============================================================================ 3. validate (no cross-tenant leakage)
def _digest(docs) -> str:
    return hashlib.sha256(json_util.dumps(sorted(docs, key=lambda d: str(d["_id"]))).encode()).hexdigest()[:16]


def validate(db, tenants: list) -> dict:
    rows, seen, dup = [], set(), 0
    for tid in tenants:
        src = list(db.tasks.find({"tenant_id": tid}))
        dst = list(db[tcoll(tid)].find())
        foreign = db[tcoll(tid)].count_documents({"tenant_id": {"$ne": tid}})
        ids = {d["_id"] for d in dst}
        dup += len(ids & seen)
        seen |= ids
        ok = len(src) == len(dst) and foreign == 0 and ids == {d["_id"] for d in src} and _digest(src) == _digest(dst)
        rows.append({"tenant": tid, "shared_count": len(src), "tenant_collection_count": len(dst), "foreign_docs": foreign,
                     "same_ids": ids == {d["_id"] for d in src}, "sha256_shared": _digest(src), "sha256_tenant": _digest(dst),
                     "ok": ok})
    total = db.tasks.count_documents({})
    return {"tenants": rows, "total_shared": total, "total_migrated": len(seen), "ids_in_two_collections": dup,
            "pass": all(r["ok"] for r in rows) and dup == 0 and total == len(seen)}


def leak_check(db, tenants: list) -> dict:
    """Negative control: put one acme task into globex's collection - the validation must catch it."""
    a, b = tenants[0], tenants[1]
    doc = db.tasks.find_one({"tenant_id": a})
    db[tcoll(b)].insert_one({**doc, "_id": "leak-test"})
    v = validate(db, [b])
    db[tcoll(b)].delete_one({"_id": "leak-test"})
    return {"injected": f"one {a} task into {tcoll(b)}", "detected": not v["pass"], "foreign_docs": v["tenants"][0]["foreign_docs"]}


# ============================================================================ 4. compare performance
def _time(fn, reps: int) -> dict:
    fn()                                           # warm-up (cache, plan cache)
    xs = []
    for _ in range(reps):
        t0 = time.perf_counter(); fn(); xs.append((time.perf_counter() - t0) * 1000)
    xs.sort()
    return {"median_ms": round(statistics.median(xs), 3), "p95_ms": round(xs[int(0.95 * (len(xs) - 1))], 3), "reps": reps}


def _explain(cursor) -> dict:
    e = cursor.explain()
    s = e.get("executionStats", {})
    plan = json.dumps(e.get("queryPlanner", {}).get("winningPlan", {}))
    stage = "IXSCAN" if "IXSCAN" in plan else ("COLLSCAN" if "COLLSCAN" in plan else "?")
    return {"stage": stage, "keys_examined": s.get("totalKeysExamined"), "docs_examined": s.get("totalDocsExamined"),
            "returned": s.get("nReturned")}


def compare(db, tenants: list, reps: int = 200) -> dict:
    tid = tenants[len(tenants) // 2]
    shared, own = db.tasks, db[tcoll(tid)]
    lst = {"document-level": _time(lambda: list(shared.find({"tenant_id": tid}).sort("created_at", -1).limit(20)), reps),
           "collection-per-tenant": _time(lambda: list(own.find().sort("created_at", -1).limit(20)), reps)}
    lst["explain"] = {"document-level": _explain(shared.find({"tenant_id": tid}).sort("created_at", -1).limit(20)),
                      "collection-per-tenant": _explain(own.find().sort("created_at", -1).limit(20))}
    n = max(50, reps // 2)
    now = datetime.now(UTC)
    ins = {"document-level": _time(lambda: shared.insert_one({"tenant_id": tid, "title": "bench", "status": "open",
                                                              "priority": "low", "created_at": now, "bench": True}), n),
           "collection-per-tenant": _time(lambda: own.insert_one({"tenant_id": tid, "title": "bench", "status": "open",
                                                                 "priority": "low", "created_at": now, "bench": True}), n)}
    shared.delete_many({"bench": True}); own.delete_many({"bench": True})
    group = [{"$group": {"_id": {"tenant": "$tenant_id", "status": "$status"}, "n": {"$sum": 1}}}]
    union = [{"$unionWith": {"coll": tcoll(t)}} for t in tenants[1:]]

    def per_tenant_union():
        return list(db[tcoll(tenants[0])].aggregate(union + group))

    def app_side():
        out = []
        for t in tenants:
            out += list(db[tcoll(t)].aggregate(group))
        return out
    ar = max(10, reps // 10)
    ana = {"document-level": _time(lambda: list(shared.aggregate(group)), ar),
           "collection-per-tenant ($unionWith)": _time(per_tenant_union, ar),
           "collection-per-tenant (app-side merge)": _time(app_side, ar)}
    same = sorted((r["_id"]["tenant"], r["_id"]["status"], r["n"]) for r in shared.aggregate(group)) == \
        sorted((r["_id"]["tenant"], r["_id"]["status"], r["n"]) for r in per_tenant_union())
    return {"tenant": tid, "list_20": lst, "create": ins, "analytics": ana, "analytics_same_result": same}


# ============================================================================ 5. backup granularity (Lab 14)
def _bv():
    sys.path.insert(0, str(HERE.parent / "lab14"))
    import backup_verify as BV                      # noqa: E402
    BV.say = lambda m="": None
    return BV


def backup_granularity(uri: str, db, tenants: list) -> dict:
    """Restore ONE tenant after its tasks were deleted by mistake - in both layouts; the other tenants must not move."""
    if not shutil.which("mongodump"):
        return {"skipped": "mongodump not installed"}
    BV = _bv()
    tid, name = tenants[1], db.name
    work = Path(tempfile.mkdtemp(prefix="lab15-bk-"))
    others = lambda: {t: db.tasks.count_documents({"tenant_id": t}) for t in tenants if t != tid}   # noqa: E731
    res = {"tenant": tid}
    # collection-per-tenant: the tenant IS a collection
    d1 = BV.tool("mongodump", uri, ["--db", name, "--collection", tcoll(tid), "--out", str(work / "c")])
    before = db[tcoll(tid)].count_documents({})
    db[tcoll(tid)].delete_many({})
    r1 = BV.tool("mongorestore", uri, ["--drop", f"--nsInclude={name}.{tcoll(tid)}", str(work / "c")])
    res["collection_per_tenant"] = {"dump_s": d1["seconds"], "dump_bytes": BV.dir_bytes(work / "c"), "restore_s": r1["seconds"],
                                    "restored": db[tcoll(tid)].count_documents({}), "expected": before,
                                    "command": f"mongodump --db {name} --collection {tcoll(tid)} ; mongorestore --drop --nsInclude={name}.{tcoll(tid)}"}
    # document-level: the tenant is a slice of a shared collection
    q = json.dumps({"tenant_id": tid})
    d2 = BV.tool("mongodump", uri, ["--db", name, "--collection", "tasks", "--query", q, "--out", str(work / "d")])
    before2, oth = db.tasks.count_documents({"tenant_id": tid}), others()
    db.tasks.delete_many({"tenant_id": tid})
    r2 = BV.tool("mongorestore", uri, [f"--nsInclude={name}.tasks", str(work / "d")])      # NO --drop
    res["document_level"] = {"dump_s": d2["seconds"], "dump_bytes": BV.dir_bytes(work / "d"), "restore_s": r2["seconds"],
                             "restored": db.tasks.count_documents({"tenant_id": tid}), "expected": before2,
                             "others_unchanged": others() == oth,
                             "command": f"mongodump --collection tasks --query '{q}' ; delete_many({{tenant_id}}) ; mongorestore (no --drop)"}
    # the pitfall: --drop on the shared collection with a ONE-tenant dump
    full = BV.tool("mongodump", uri, ["--db", name, "--collection", "tasks", "--out", str(work / "full")])
    BV.tool("mongorestore", uri, ["--drop", f"--nsInclude={name}.tasks", str(work / "d")])
    left = {t: db.tasks.count_documents({"tenant_id": t}) for t in tenants}
    BV.tool("mongorestore", uri, ["--drop", f"--nsInclude={name}.tasks", str(work / "full")])        # repair
    res["pitfall_drop_on_shared"] = {"tenants_left_after": {t: n for t, n in left.items()},
                                     "other_tenants_wiped": sum(1 for t, n in left.items() if t != tid and n == 0),
                                     "repaired": db.tasks.count_documents({}) == sum(others().values()) + before2,
                                     "full_dump_rc": full["rc"]}
    shutil.rmtree(work, ignore_errors=True)
    res["pass"] = (res["collection_per_tenant"]["restored"] == before and res["document_level"]["restored"] == before2 and
                   res["document_level"]["others_unchanged"])
    return res


# ============================================================================ 6. schema evolution
def schema_evolution(db, tenants: list) -> dict:
    v = {"$jsonSchema": {"bsonType": "object", "required": ["tenant_id", "title", "labels"],
                         "properties": {"labels": {"bsonType": "array"}}}}
    t0 = time.perf_counter()
    a = db.tasks.update_many({"labels": {"$exists": False}}, {"$set": {"labels": []}}).modified_count
    db.command("collMod", "tasks", validator=v)
    doc_s, doc_ops = time.perf_counter() - t0, 2
    t0 = time.perf_counter()
    b = 0
    for t in tenants:
        b += db[tcoll(t)].update_many({"labels": {"$exists": False}}, {"$set": {"labels": []}}).modified_count
        db.command("collMod", tcoll(t), validator=v)
    per_s = time.perf_counter() - t0
    for c in ["tasks"] + [tcoll(t) for t in tenants]:          # undo for the next run
        db.command("collMod", c, validator={})
        db[c].update_many({}, {"$unset": {"labels": ""}})
    return {"change": "add field labels: [] to every task + require it in the validator",
            "document_level": {"operations": doc_ops, "modified": a, "seconds": round(doc_s, 3)},
            "collection_per_tenant": {"operations": 2 * len(tenants), "modified": b, "seconds": round(per_s, 3),
                                      "note": "no atomicity across collections: a failure half way leaves tenants on different schema versions"}}


# ============================================================================ 7. run everything
def run_all(uri: str, dbname: str = "tasktracker_mig", quick: bool = False, log=say) -> dict:
    global say
    say = log
    c = MongoClient(uri, serverSelectionTimeoutMS=6000, w="majority", tz_aware=True)
    db = c[dbname]
    out = {"db": dbname}
    log(f"1. seed: {len(TENANTS5)} tenants x 100 tasks in ONE collection `tasks` (document-level isolation)")
    out["seed"] = seed(db, TENANTS5, 100)
    log(f"   {out['seed']['documents']} documents, index (tenant_id, created_at)")
    t0 = time.perf_counter()
    loop = migrate(db, TENANTS5, "loop")
    log(f"2. migrate with a Python loop (find + insert_many): {loop['seconds']:.3f} s")
    out["migrate"] = migrate(db, TENANTS5, "out")
    out["migrate"]["loop_seconds"] = loop["seconds"]
    log(f"   migrate with aggregate $match + $out: {out['migrate']['seconds']:.3f} s; indexes right after $out: "
        f"{out['migrate']['tenants'][0]['indexes_after_out']} (so `created` is created afterwards)")
    out["validate"] = validate(db, TENANTS5)
    for r in out["validate"]["tenants"]:
        log(f"   {r['tenant']:<9} shared {r['shared_count']:>4} -> {tcoll(r['tenant']):<22} {r['tenant_collection_count']:>4} | "
            f"foreign docs {r['foreign_docs']} | same _ids {r['same_ids']} | sha256 {r['sha256_shared']} = {r['sha256_tenant']} "
            f"{'OK' if r['ok'] else 'FAIL'}")
    out["leak_check"] = leak_check(db, TENANTS5)
    log(f"3. validation {'PASS' if out['validate']['pass'] else 'FAIL'}: {out['validate']['total_migrated']} of "
        f"{out['validate']['total_shared']} migrated, 0 in two collections; negative control "
        f"({out['leak_check']['injected']}) detected: {out['leak_check']['detected']}")
    scales = [("5 tenants x 100 (the lab)", TENANTS5, 100)]
    if not quick:
        scales += [("5 tenants x 20,000", TENANTS5, 20000), ("100 tenants x 100", [f"t{i:03d}" for i in range(100)], 100)]
    out["compare"] = []
    for label, tenants, per in scales:
        if per != 100 or tenants is not TENANTS5:
            seed(db, tenants, per); migrate(db, tenants, "out")
        r = compare(db, tenants, reps=200 if per <= 100 else 100)
        r["scale"] = label
        out["compare"].append(r)
        ls, cr, an = r["list_20"], r["create"], r["analytics"]
        log(f"4. [{label}] list 20 newest: shared {ls['document-level']['median_ms']} ms vs own collection "
            f"{ls['collection-per-tenant']['median_ms']} ms (docs examined {ls['explain']['document-level']['docs_examined']} vs "
            f"{ls['explain']['collection-per-tenant']['docs_examined']}); create {cr['document-level']['median_ms']} vs "
            f"{cr['collection-per-tenant']['median_ms']} ms; analytics {an['document-level']['median_ms']} ms vs $unionWith "
            f"{an['collection-per-tenant ($unionWith)']['median_ms']} ms (same result: {r['analytics_same_result']})")
    if not quick:
        seed(db, TENANTS5, 100); migrate(db, TENANTS5, "out")
        for n in db.list_collection_names():
            if n.startswith("tasks_tenant_t"):
                db.drop_collection(n)
    log("5. backup granularity: restore ONE tenant (Lab 14 mongodump/mongorestore)")
    out["backup"] = backup_granularity(uri, db, TENANTS5)
    if "skipped" not in out["backup"]:
        b = out["backup"]
        log(f"   per-tenant collection: restored {b['collection_per_tenant']['restored']}/{b['collection_per_tenant']['expected']} in "
            f"{b['collection_per_tenant']['restore_s']} s | shared collection + --query: restored {b['document_level']['restored']}/"
            f"{b['document_level']['expected']}, other tenants unchanged {b['document_level']['others_unchanged']}")
        log(f"   PITFALL: mongorestore --drop of a one-tenant dump on the shared collection wiped "
            f"{b['pitfall_drop_on_shared']['other_tenants_wiped']} other tenants (left: {b['pitfall_drop_on_shared']['tenants_left_after']}) "
            f"- repaired from the full dump: {b['pitfall_drop_on_shared']['repaired']}")
    out["schema"] = schema_evolution(db, TENANTS5)
    log(f"6. schema change: {out['schema']['document_level']['operations']} operations on the shared collection vs "
        f"{out['schema']['collection_per_tenant']['operations']} for {len(TENANTS5)} tenant collections")
    out["recommendation"] = recommendation(out)
    out["pass"] = out["validate"]["pass"] and out["leak_check"]["detected"] and out["backup"].get("pass", True)
    c.close()
    return out


def recommendation(o: dict) -> list:
    big = o["compare"][-1] if o["compare"] else {}
    an = big.get("analytics", {})
    return [
        {"criterion": "Single-tenant queries (list a tenant's tasks)",
         "document_level": "IXSCAN on (tenant_id, created_at): reads only that tenant's index keys",
         "collection_per_tenant": "IXSCAN on (created_at) of a smaller collection",
         "measured": "about the same - with the compound index the shared collection examines exactly as many documents",
         "winner": "tie (if the compound index exists)"},
        {"criterion": "Cross-tenant analytics",
         "document_level": "one $group over one collection", "collection_per_tenant": "$unionWith over N collections or app-side merge",
         "measured": (f"{an.get('document-level', {}).get('median_ms', '?')} ms vs {an.get('collection-per-tenant ($unionWith)', {}).get('median_ms', '?')} ms "
                      f"at {big.get('scale', '')}"), "winner": "document-level"},
        {"criterion": "Query isolation (a forgotten filter)",
         "document_level": "a missing tenant_id filter leaks every tenant - must be enforced in ONE place (scoped())",
         "collection_per_tenant": "the collection name is the boundary; a wrong name still leaks", "measured": "",
         "winner": "collection-per-tenant (stronger by construction)"},
        {"criterion": "Backup / restore of one tenant",
         "document_level": "mongodump --query + delete + restore WITHOUT --drop (with --drop all other tenants are wiped)",
         "collection_per_tenant": "mongodump --collection / mongorestore --drop --nsInclude of one collection",
         "measured": "both restored exactly; the shared layout has a destructive pitfall", "winner": "collection-per-tenant"},
        {"criterion": "Schema evolution", "document_level": "1 update + 1 collMod for everyone",
         "collection_per_tenant": "2 x N operations, not atomic, tenants can drift", "measured": "", "winner": "document-level"},
        {"criterion": "Thousands of tenants", "document_level": "one collection, one set of indexes",
         "collection_per_tenant": "a collection + its indexes are files in WiredTiger: thousands of tenants = tens of thousands of files",
         "measured": "", "winner": "document-level"},
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uri", default=os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    ap.add_argument("--db", default="tasktracker_mig")
    ap.add_argument("--quick", action="store_true", help="only the 5 x 100 scale")
    ap.add_argument("--json", help="write the results to this file")
    a = ap.parse_args()
    out = run_all(a.uri, a.db, a.quick)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=2, default=str))
    say("\nRecommendation:")
    for r in out["recommendation"]:
        say(f"  - {r['criterion']}: {r['winner']}")
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Lab 16 - Wide-column and graph databases: Cassandra and Neo4j next to MongoDB (polyglot persistence).

    Lab 16.1   one dataset (a university course enrollment system) modelled three times:
               MongoDB    students {course_ids[]} + courses {roster[]}                (document model, embedding)
               Cassandra  student_courses (PK student_id) + course_students (PK course_id)   (one table per query)
               Neo4j      (:Student)-[:ENROLLED_IN]->(:Course)                         (nodes + relationships)
               3 equivalent queries in each, answers compared (must be identical), syntax + latency compared.
    Activity   hybrid architecture: MongoDB = operational data, Cassandra = audit log, Neo4j = prerequisite chains.
    Lab 16.2   labs/lab16/fraud_service.py - FraudQueryService over a fraud dataset (users, transactions, transfer
               graph with planted rings): pooling, circuit breaker, unit tests (mock) + integration test + latency.
    Applied    (mentor's rule) the Lab 10 classifier billing ledger is imported into the fraud dataset and analysed by
               the same service - real payments of Kavach users go through the polyglot fraud check.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pymongo import ASCENDING, DESCENDING, InsertOne

from . import polyglot as PG

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "labs" / "lab16"
RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
sys.path.insert(0, str(LAB))
import fraud_service as FS  # noqa: E402

HEAVY = threading.Lock()
UNI_DB, FRAUD_DB, KS = "lab16_university", "lab16_fraud", "lab16"
DEPTS = ["CSE", "AIML", "ECE", "MATH", "PHY"]
FIRST = ["Aarav", "Diya", "Ishaan", "Meera", "Kabir", "Anaya", "Vihaan", "Sara", "Arjun", "Riya", "Rohan", "Tara", "Neel",
         "Kavya", "Dev", "Isha", "Aditya", "Nisha", "Karan", "Pooja", "Alice", "Bob"]
LAST = ["Sharma", "Iyer", "Reddy", "Nair", "Gupta", "Rao", "Menon", "Das", "Patel", "Joshi", "Kulkarni", "Singh"]
# course id -> (title, dept, credits, prerequisites)
COURSES = {
    "C101": ("Programming in Python", "CSE", 4, []), "C102": ("Discrete Mathematics", "MATH", 3, []),
    "C103": ("Linear Algebra", "MATH", 3, []), "C104": ("Probability and Statistics", "MATH", 3, ["C102"]),
    "C201": ("Data Structures", "CSE", 4, ["C101"]), "C202": ("Database Systems", "CSE", 4, ["C201"]),
    "C203": ("Operating Systems", "CSE", 4, ["C201"]), "C204": ("Computer Networks", "ECE", 3, ["C203"]),
    "C205": ("Design and Analysis of Algorithms", "CSE", 4, ["C201", "C102"]),
    "C301": ("NoSQL Databases using MongoDB", "CSE", 3, ["C202"]), "C302": ("Machine Learning", "AIML", 4, ["C104", "C103", "C101"]),
    "C303": ("Deep Learning", "AIML", 4, ["C302"]), "C304": ("Natural Language Processing", "AIML", 3, ["C303"]),
    "C305": ("Computer Vision", "AIML", 3, ["C303"]), "C306": ("Reinforcement Learning", "AIML", 3, ["C302"]),
    "C307": ("Distributed Systems", "CSE", 3, ["C204", "C203"]), "C308": ("Information Security", "CSE", 3, ["C204"]),
    "C401": ("Big Data Analytics", "AIML", 3, ["C301", "C302"]), "C402": ("Cloud Computing", "CSE", 3, ["C307"]),
    "C403": ("Threat Intelligence Engineering", "CSE", 3, ["C308", "C401"]),
    "C111": ("Physics for Engineers", "PHY", 3, []), "C112": ("Digital Electronics", "ECE", 3, ["C111"]),
    "C113": ("Signals and Systems", "ECE", 3, ["C112", "C103"]), "C114": ("Embedded Systems", "ECE", 3, ["C112", "C101"]),
}


def _jd(x):
    return json.loads(json.dumps(x, default=str))


def _file(name: str) -> str:
    return (LAB / name).read_text()


def _statements(text: str, comment: str) -> list[str]:
    # remove comment lines FIRST (a comment may contain ';'), then split into statements
    code = "\n".join(l for l in text.splitlines() if l.strip() and not l.strip().startswith(comment))
    return [c.strip() for c in code.split(";") if c.strip()]


# ============================================================================ schemas
def ensure_schema(log=print) -> dict:
    s = PG.cassandra()
    cql = _statements(_file("schema.cql"), "--")
    for st in cql:
        s.execute(st)
    cy = _statements(_file("schema.cypher"), "//")
    for st in cy:
        PG.cypher_write(st)
    m = PG.mongo()
    m[UNI_DB].students.create_index("course_ids")
    m[UNI_DB].courses.create_index("roster.student_id")
    m[FRAUD_DB].transactions.create_index("txn_id", unique=True)
    m[FRAUD_DB].transactions.create_index([("user_id", ASCENDING), ("timestamp", DESCENDING)])
    log(f"   schema ready: Cassandra {len(cql)} statements (keyspace lab16), Neo4j {len(cy)} constraints, MongoDB indexes")
    return {"cql": len(cql), "cypher": len(cy)}


# ============================================================================ 16.1 the university dataset
def university(n_students: int = 300, seed: int = 16) -> dict:
    rnd = random.Random(seed)
    students = []
    for i in range(n_students):
        sid = f"S{i + 1:04d}"
        if i == 0:
            name = "Alice Sharma"                      # the solution's example student
        else:
            name = "Alice Sharma"
            while name == "Alice Sharma":
                name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
        dept = rnd.choice(DEPTS)
        year = rnd.randint(1, 4)
        k = rnd.randint(3, 6)
        pool = [c for c in COURSES if int(c[1]) <= year] or list(COURSES)
        cids = sorted(rnd.sample(pool, min(k, len(pool))))
        students.append({"_id": sid, "name": name, "dept": dept, "year": year, "course_ids": cids})
    courses = {cid: {"_id": cid, "title": t, "dept": d, "credits": cr, "prereqs": p, "roster": []}
               for cid, (t, d, cr, p) in COURSES.items()}
    for s in students:
        for cid in s["course_ids"]:
            courses[cid]["roster"].append({"student_id": s["_id"], "name": s["name"], "year": s["year"]})
    return {"students": students, "courses": list(courses.values())}


def load_university(n_students: int = 300, log=print) -> dict:
    data = university(n_students)
    t = {}
    # MongoDB: two collections, both sides embedded (the solution's design)
    t0 = time.perf_counter()
    db = PG.mongo()[UNI_DB]
    db.students.drop(); db.courses.drop()
    db.students.insert_many(data["students"]); db.courses.insert_many(data["courses"])
    db.students.create_index("course_ids"); db.courses.create_index("roster.student_id")
    t["mongodb"] = round(time.perf_counter() - t0, 2)
    # Cassandra: the same enrollment written to BOTH tables (denormalised)
    t0 = time.perf_counter()
    s = PG.cassandra()
    s.execute(f"TRUNCATE {KS}.student_courses"); s.execute(f"TRUNCATE {KS}.course_students")
    cinfo = {c["_id"]: c for c in data["courses"]}
    sc = [(st["_id"], cid, cinfo[cid]["title"], cinfo[cid]["credits"], cinfo[cid]["dept"]) for st in data["students"] for cid in st["course_ids"]]
    cs = [(cid, st["_id"], st["name"], st["year"]) for st in data["students"] for cid in st["course_ids"]]
    PG.bulk(f"INSERT INTO {KS}.student_courses (student_id, course_id, course_title, credits, dept) VALUES (?, ?, ?, ?, ?)", sc)
    PG.bulk(f"INSERT INTO {KS}.course_students (course_id, student_id, student_name, year) VALUES (?, ?, ?, ?)", cs)
    t["cassandra"] = round(time.perf_counter() - t0, 2)
    # Neo4j: nodes + ENROLLED_IN relationships (+ REQUIRES for the activity)
    t0 = time.perf_counter()
    PG.cypher_write("MATCH (n) WHERE n:Student OR n:Course DETACH DELETE n")
    PG.cypher_write("UNWIND $rows AS r CREATE (:Course {id: r.id, title: r.title, dept: r.dept, credits: r.credits})",
                    rows=[{"id": c["_id"], "title": c["title"], "dept": c["dept"], "credits": c["credits"]} for c in data["courses"]])
    PG.cypher_write("UNWIND $rows AS r MATCH (a:Course {id: r.c}), (b:Course {id: r.p}) CREATE (a)-[:REQUIRES]->(b)",
                    rows=[{"c": c["_id"], "p": p} for c in data["courses"] for p in c["prereqs"]])
    PG.cypher_write("UNWIND $rows AS r CREATE (:Student {id: r.id, name: r.name, dept: r.dept, year: r.year})",
                    rows=[{"id": s_["_id"], "name": s_["name"], "dept": s_["dept"], "year": s_["year"]} for s_ in data["students"]])
    PG.cypher_write("UNWIND $rows AS r MATCH (s:Student {id: r.s}), (c:Course {id: r.c}) CREATE (s)-[:ENROLLED_IN {term: '2026-odd'}]->(c)",
                    rows=[{"s": st["_id"], "c": cid} for st in data["students"] for cid in st["course_ids"]])
    t["neo4j"] = round(time.perf_counter() - t0, 2)
    n_enr = sum(len(st["course_ids"]) for st in data["students"])
    log(f"   loaded {len(data['students'])} students, {len(data['courses'])} courses, {n_enr} enrollments - "
        f"seconds: MongoDB {t['mongodb']}, Cassandra {t['cassandra']} ({2 * n_enr} rows, 2 tables), Neo4j {t['neo4j']}")
    return {"students": len(data["students"]), "courses": len(data["courses"]), "enrollments": n_enr, "load_s": t,
            "cassandra_rows": 2 * n_enr}


# ---------------------------------------------------------------------------- the three queries x three databases
def q_mongo(name: str, arg: str):
    db = PG.mongo()[UNI_DB]
    if name == "courses_of_student":
        return sorted(c["_id"] for c in db.courses.find({"roster.student_id": arg}, {"_id": 1}))
    if name == "students_in_course":
        c = db.courses.find_one({"_id": arg}, {"roster.student_id": 1})
        return sorted(r["student_id"] for r in (c or {}).get("roster", []))
    if name == "shared_course":
        pipe = [{"$match": {"roster.student_id": arg}}, {"$unwind": "$roster"},
                {"$match": {"roster.student_id": {"$ne": arg}}}, {"$group": {"_id": "$roster.student_id"}}]
        return sorted(d["_id"] for d in db.courses.aggregate(pipe))
    raise ValueError(name)


_PS: dict = {}


def _ps(cql):
    if cql not in _PS:
        _PS[cql] = PG.cassandra().prepare(cql)
    return _PS[cql]


def q_cass(name: str, arg: str):
    s = PG.cassandra()
    if name == "courses_of_student":
        return sorted(r["course_id"] for r in s.execute(_ps(f"SELECT course_id, course_title FROM {KS}.student_courses WHERE student_id = ?"), (arg,)))
    if name == "students_in_course":
        return sorted(r["student_id"] for r in s.execute(_ps(f"SELECT student_id, student_name FROM {KS}.course_students WHERE course_id = ?"), (arg,)))
    if name == "shared_course":                       # no joins in CQL: 1 + N queries, the union is done by the application
        cids = [r["course_id"] for r in s.execute(_ps(f"SELECT course_id FROM {KS}.student_courses WHERE student_id = ?"), (arg,))]
        futs = [s.execute_async(_ps(f"SELECT student_id FROM {KS}.course_students WHERE course_id = ?"), (c,)) for c in cids]
        out = set()
        for f in futs:
            out.update(r["student_id"] for r in f.result())
        out.discard(arg)
        return sorted(out)
    raise ValueError(name)


CYPHER = {
    "courses_of_student": "MATCH (:Student {id: $a})-[:ENROLLED_IN]->(c:Course) RETURN c.id AS id",
    "students_in_course": "MATCH (s:Student)-[:ENROLLED_IN]->(:Course {id: $a}) RETURN s.id AS id",
    "shared_course": "MATCH (s1:Student {id: $a})-[:ENROLLED_IN]->(:Course)<-[:ENROLLED_IN]-(s2:Student) "
                     "WHERE s2 <> s1 RETURN DISTINCT s2.id AS id",
}


def q_neo(name: str, arg: str):
    return sorted(r["id"] for r in PG.cypher(CYPHER[name], a=arg))


QUERIES = {
    "courses_of_student": {
        "title": "Find all courses for a student",
        "mongodb": 'db.courses.find({"roster.student_id": sid}, {"_id": 1, "title": 1})',
        "cassandra": "SELECT course_id, course_title FROM lab16.student_courses WHERE student_id = ?;",
        "neo4j": CYPHER["courses_of_student"].replace("$a", "$sid"),
        "trips": {"mongodb": 1, "cassandra": 1, "neo4j": 1}},
    "students_in_course": {
        "title": "Find all students in a course",
        "mongodb": 'db.courses.find_one({"_id": cid}, {"roster.student_id": 1})',
        "cassandra": "SELECT student_id, student_name FROM lab16.course_students WHERE course_id = ?;",
        "neo4j": CYPHER["students_in_course"].replace("$a", "$cid"),
        "trips": {"mongodb": 1, "cassandra": 1, "neo4j": 1}},
    "shared_course": {
        "title": "Find students who share a course (with Alice)",
        "mongodb": 'db.courses.aggregate([\n  {"$match": {"roster.student_id": sid}},\n  {"$unwind": "$roster"},\n'
                   '  {"$match": {"roster.student_id": {"$ne": sid}}},\n  {"$group": {"_id": "$roster.student_id"}}])',
        "cassandra": "SELECT course_id FROM lab16.student_courses WHERE student_id = ?;\n-- then, for EACH course (async, in parallel):\n"
                     "SELECT student_id FROM lab16.course_students WHERE course_id = ?;\n-- union + remove the student: done in Python",
        "neo4j": "MATCH (s1:Student {id: $sid})-[:ENROLLED_IN]->(:Course)\n      <-[:ENROLLED_IN]-(s2:Student)\nWHERE s2 <> s1 RETURN DISTINCT s2.id",
        "trips": {"mongodb": 1, "cassandra": "1 + N", "neo4j": 1}},
}


def _complexity(text: str) -> dict:
    lines = [l for l in text.strip().splitlines() if l.strip() and not l.strip().startswith("--")]
    return {"lines": len(lines), "chars": sum(len(l.strip()) for l in lines)}


def run_161(quick: bool = False, log=print) -> dict:
    ensure_schema(log)
    load = load_university(150 if quick else 300, log)
    runs = 10
    rows, all_equal = [], True
    args = {"courses_of_student": ["S0001", "S0042", "S0101", "S0007", "S0150"],
            "students_in_course": ["C301", "C201", "C101", "C302", "C104"],
            "shared_course": ["S0001", "S0042", "S0101", "S0007", "S0150"]}
    for qname, spec in QUERIES.items():
        r = {"query": qname, "title": spec["title"], "db": {}}
        answers = {}
        for dbname, fn in (("mongodb", q_mongo), ("cassandra", q_cass), ("neo4j", q_neo)):
            lat = []
            for a in args[qname]:
                res = PG.timed(lambda: fn(qname, a), runs=runs)
                lat.append(res["median_ms"])
                answers.setdefault(a, {})[dbname] = res["result"]
            lat.sort()
            r["db"][dbname] = {"median_ms": round(lat[len(lat) // 2], 3), "per_arg_median_ms": lat, "code": spec[dbname],
                               "round_trips": spec["trips"][dbname], **_complexity(spec[dbname])}
        eq = all(v["mongodb"] == v["cassandra"] == v["neo4j"] for v in answers.values())
        all_equal &= eq
        r["identical_answers"] = eq
        r["example"] = {"arg": args[qname][0], "count": len(answers[args[qname][0]]["mongodb"]), "first": answers[args[qname][0]]["mongodb"][:8]}
        rows.append(r)
        log(f"   {spec['title']:<48} identical: {eq}   median ms  MongoDB {r['db']['mongodb']['median_ms']:>7}  "
            f"Cassandra {r['db']['cassandra']['median_ms']:>7}  Neo4j {r['db']['neo4j']['median_ms']:>7}")
    alice = PG.cypher("MATCH (s1:Student {id: 'S0001'})-[:ENROLLED_IN]->(c:Course)<-[:ENROLLED_IN]-(s2:Student) "
                      "WHERE s1.name = 'Alice Sharma' AND s2.name <> 'Alice Sharma' RETURN count(DISTINCT s2) AS n, collect(DISTINCT c.id) AS courses")
    return {"load": load, "queries": rows, "identical": all_equal, "runs_per_arg": runs, "args": args,
            "solution_cypher_alice": alice[0] if alice else {}, "pass": all_equal}


# ============================================================================ Activity: hybrid architecture
def prereq_chain(course_id: str) -> dict:
    rows = PG.cypher("MATCH p = (c:Course {id: $c})-[:REQUIRES*1..]->(pre:Course) "
                     "RETURN pre.id AS id, pre.title AS title, min(length(p)) AS depth ORDER BY depth, id", c=course_id)
    longest = PG.cypher("MATCH p = (c:Course {id: $c})-[:REQUIRES*1..]->(root:Course) WHERE NOT EXISTS { (root)-[:REQUIRES]->() } "
                        "RETURN [n IN nodes(p) | n.id] AS path ORDER BY length(p) DESC LIMIT 1", c=course_id)
    return {"course": course_id, "all": rows, "longest_path": longest[0]["path"] if longest else [course_id]}


def missing_prereqs(student_id: str, course_id: str) -> list[str]:
    rows = PG.cypher("MATCH (c:Course {id: $c})-[:REQUIRES*1..]->(pre:Course) "
                     "WHERE NOT EXISTS { MATCH (:Student {id: $s})-[:COMPLETED]->(pre) } RETURN DISTINCT pre.id AS id ORDER BY id",
                     c=course_id, s=student_id)
    return [r["id"] for r in rows]


def audit(entity: str, action: str, actor: str, outcome: str, details: dict):
    from cassandra.util import uuid_from_time
    now = datetime.now(UTC)
    PG.cassandra().execute(_ps(f"INSERT INTO {KS}.audit_log (entity_id, day, at, action, actor, outcome, details) VALUES (?, ?, ?, ?, ?, ?, ?)"),
                           (entity, now.date(), uuid_from_time(now), action, actor, outcome, json.dumps(details, default=str)))


def audit_trail(entity: str, limit: int = 20) -> list[dict]:
    from cassandra.util import datetime_from_uuid1
    rows = PG.cassandra().execute(_ps(f"SELECT at, action, actor, outcome, details FROM {KS}.audit_log WHERE entity_id = ? AND day = ? LIMIT ?"),
                                  (entity, datetime.now(UTC).date(), limit))
    return [{"at": datetime_from_uuid1(r["at"]).isoformat(), "action": r["action"], "actor": r["actor"], "outcome": r["outcome"],
             "details": json.loads(r["details"] or "{}")} for r in rows]


def enroll(student_id: str, course_id: str, actor: str = "registrar") -> dict:
    """The hybrid service: Neo4j decides (prerequisite chain), MongoDB stores (operational), Cassandra remembers (audit)."""
    t0 = time.perf_counter()
    miss = missing_prereqs(student_id, course_id)
    t_graph = (time.perf_counter() - t0) * 1000
    if miss:
        audit(student_id, "enroll", actor, "denied", {"course": course_id, "missing": miss})
        return {"ok": False, "student": student_id, "course": course_id, "missing": miss, "graph_ms": round(t_graph, 2)}
    m = PG.mongo()
    db = m[UNI_DB]
    st = db.students.find_one({"_id": student_id})
    with m.start_session() as s:                     # Lab 10: both documents change together or not at all
        s.with_transaction(lambda ss: (db.students.update_one({"_id": student_id}, {"$addToSet": {"course_ids": course_id}}, session=ss),
                                       db.courses.update_one({"_id": course_id, "roster.student_id": {"$ne": student_id}},
                                                             {"$push": {"roster": {"student_id": student_id, "name": st["name"], "year": st["year"]}}},
                                                             session=ss)))
    PG.cypher_write("MATCH (s:Student {id: $s}), (c:Course {id: $c}) MERGE (s)-[:ENROLLED_IN {term: '2026-odd'}]->(c)", s=student_id, c=course_id)
    c = db.courses.find_one({"_id": course_id})
    PG.cassandra().execute(_ps(f"INSERT INTO {KS}.student_courses (student_id, course_id, course_title, credits, dept) VALUES (?, ?, ?, ?, ?)"),
                           (student_id, course_id, c["title"], c["credits"], c["dept"]))
    PG.cassandra().execute(_ps(f"INSERT INTO {KS}.course_students (course_id, student_id, student_name, year) VALUES (?, ?, ?, ?)"),
                           (course_id, student_id, st["name"], st["year"]))
    audit(student_id, "enroll", actor, "ok", {"course": course_id})
    return {"ok": True, "student": student_id, "course": course_id, "missing": [], "graph_ms": round(t_graph, 2),
            "total_ms": round((time.perf_counter() - t0) * 1000, 2)}


def complete(student_id: str, course_id: str, grade: str = "A"):
    PG.cypher_write("MATCH (s:Student {id: $s}), (c:Course {id: $c}) MERGE (s)-[r:COMPLETED]->(c) SET r.grade = $g", s=student_id, c=course_id, g=grade)
    audit(student_id, "complete", "exam-cell", "ok", {"course": course_id, "grade": grade})


def run_activity(log=print) -> dict:
    """Bob (S0002) asks for Threat Intelligence Engineering (C403), the deepest course in the graph."""
    sid, target = "S0002", "C403"
    PG.cypher_write("MATCH (:Student {id: $s})-[r:COMPLETED]->() DELETE r", s=sid)
    chain = prereq_chain(target)
    steps = []
    r1 = enroll(sid, target)
    steps.append({"step": f"{sid} asks for {target} with nothing completed", **r1})
    log(f"   1. enroll {sid} in {target}: {'OK' if r1['ok'] else 'DENIED, missing ' + ', '.join(r1['missing'])}")
    for c in [x["id"] for x in sorted(chain["all"], key=lambda x: -x["depth"])]:
        complete(sid, c)
    steps.append({"step": f"{sid} completes all {len(chain['all'])} prerequisites (deepest first)", "ok": True, "missing": []})
    r2 = enroll(sid, target)
    steps.append({"step": f"{sid} asks again", **r2})
    log(f"   2. after completing {len(chain['all'])} prerequisites: {'OK' if r2['ok'] else 'DENIED ' + str(r2['missing'])}")
    trail = audit_trail(sid)
    in_mongo = target in (PG.mongo()[UNI_DB].students.find_one({"_id": sid}) or {}).get("course_ids", [])
    in_cass = any(r["course_id"] == target for r in q_cass_raw(sid))
    log(f"   prerequisite chain of {target}: {' -> '.join(chain['longest_path'])}  ({len(chain['all'])} courses)")
    log(f"   audit trail in Cassandra: {len(trail)} rows; enrollment stored in MongoDB: {in_mongo}, Cassandra: {in_cass}")
    return {"student": sid, "course": target, "chain": chain, "steps": steps, "audit": trail[:20],
            "stored": {"mongodb": in_mongo, "cassandra": in_cass}, "pass": (not r1["ok"]) and r2["ok"] and in_mongo and in_cass and len(trail) >= 2}


def q_cass_raw(sid):
    return list(PG.cassandra().execute(_ps(f"SELECT course_id, course_title FROM {KS}.student_courses WHERE student_id = ?"), (sid,)))


# ============================================================================ 16.2 fraud dataset + FraudQueryService
MERCHANTS = ["QuickMart", "TravelNow", "GameZone", "CryptoXchange", "FoodExpress", "ElectroHub", "GiftCards4U", "PharmaPlus",
             "BookNook", "StreamFlix"]
COUNTRIES = ["IN", "IN", "IN", "IN", "US", "GB", "SG", "AE", "NG", "RU"]


def fraud_dataset(n_users: int = 1000, n_tx: int = 12000, n_rings: int = 6, seed: int = 162) -> dict:
    rnd = random.Random(seed)
    users = [f"U{i:05d}" for i in range(1, n_users + 1)]
    now = datetime.now(UTC).replace(microsecond=0)
    txs, transfers = [], []
    for i in range(n_tx):
        u = rnd.choice(users)
        ts = now - timedelta(seconds=rnd.randint(60, 30 * 86400))
        amt = round(rnd.lognormvariate(6.5, 1.0), 2)
        m = rnd.choice(MERCHANTS)
        risk = round(min(0.99, rnd.betavariate(1.2, 8) + (0.35 if m in ("CryptoXchange", "GiftCards4U") and amt > 3000 else 0)), 3)
        t = {"txn_id": f"T{i + 1:06d}", "user_id": u, "amount": amt, "merchant": m, "country": rnd.choice(COUNTRIES),
             "timestamp": ts, "risk_score": risk, "channel": rnd.choice(["upi", "card", "netbanking"]), "kind": "payment"}
        txs.append(t)
        if rnd.random() < 0.06:                        # a person-to-person transfer
            v = rnd.choice(users)
            if v != u:
                t["kind"] = "transfer"; t["to_user"] = v
                transfers.append({"from": u, "to": v, "txn_id": t["txn_id"], "amount": amt, "ts": ts.isoformat()})
    rings = []
    ring_users = rnd.sample(users, n_rings * 5)
    for k in range(n_rings):                          # planted fraud rings: money goes round in a cycle within minutes
        members = ring_users[k * 5: k * 5 + rnd.randint(3, 5)]
        start = now - timedelta(minutes=rnd.randint(30, 600))
        amt = round(rnd.uniform(40000, 90000), 2)
        for j, a in enumerate(members):
            b = members[(j + 1) % len(members)]
            tid = f"R{k + 1:02d}{j + 1:02d}"
            ts = start + timedelta(minutes=2 * j)
            amt = round(amt * 0.97, 2)                 # each hop keeps a small cut
            txs.append({"txn_id": tid, "user_id": a, "to_user": b, "amount": amt, "merchant": "P2P", "country": "IN", "timestamp": ts,
                        "risk_score": 0.85, "channel": "upi", "kind": "transfer", "ring": k + 1})
            transfers.append({"from": a, "to": b, "txn_id": tid, "amount": amt, "ts": ts.isoformat()})
        rings.append(members)
    return {"users": users, "transactions": txs, "transfers": transfers, "rings": rings}


def load_fraud(quick: bool = False, log=print) -> dict:
    d = fraud_dataset(600 if quick else 1000, 5000 if quick else 12000)
    t = {}
    t0 = time.perf_counter()
    db = PG.mongo()[FRAUD_DB]
    db.transactions.drop()
    db.transactions.insert_many([dict(x) for x in d["transactions"]])
    db.transactions.create_index("txn_id", unique=True)
    db.transactions.create_index([("user_id", ASCENDING), ("timestamp", DESCENDING)])
    t["mongodb"] = round(time.perf_counter() - t0, 2)
    t0 = time.perf_counter()
    PG.cassandra().execute(f"TRUNCATE {KS}.user_profiles")
    rows = [(x["user_id"], x["timestamp"], x["txn_id"], x["kind"], float(x["amount"]), x["merchant"], x["country"]) for x in d["transactions"]]
    rows += [(x["to_user"], x["timestamp"], x["txn_id"], "transfer_in", float(x["amount"]), "P2P", x["country"])
             for x in d["transactions"] if x.get("to_user")]
    PG.bulk(f"INSERT INTO {KS}.user_profiles (user_id, event_time, txn_id, event_type, amount, merchant, country) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    t["cassandra"] = round(time.perf_counter() - t0, 2)
    t0 = time.perf_counter()
    PG.cypher_write("MATCH (u:User) DETACH DELETE u")
    PG.cypher_write("UNWIND $ids AS id CREATE (:User {id: id})", ids=d["users"])
    for i in range(0, len(d["transfers"]), 5000):
        PG.cypher_write("UNWIND $rows AS r MATCH (a:User {id: r.from}), (b:User {id: r.to}) "
                        "CREATE (a)-[:TRANSFERRED_TO {txn_id: r.txn_id, amount: r.amount, ts: datetime(r.ts)}]->(b)", rows=d["transfers"][i:i + 5000])
    t["neo4j"] = round(time.perf_counter() - t0, 2)
    log(f"   fraud dataset: {len(d['users'])} users, {len(d['transactions'])} transactions (MongoDB), {len(rows)} profile events "
        f"(Cassandra), {len(d['transfers'])} transfers (Neo4j), {len(d['rings'])} planted rings - load s {t}")
    PG.mongo()[FRAUD_DB].meta.replace_one({"_id": "rings"}, {"_id": "rings", "rings": d["rings"]}, upsert=True)
    return {"users": len(d["users"]), "transactions": len(d["transactions"]), "profile_rows": len(rows), "transfers": len(d["transfers"]),
            "rings": d["rings"], "load_s": t}


def service(**kw) -> FS.FraudQueryService:
    return FS.FraudQueryService(PG.mongo(), PG.cassandra(), PG.neo4j(), mongo_db=FRAUD_DB, keyspace=KS, **kw)


def run_unit_tests() -> dict:
    p = subprocess.run([sys.executable, "-m", "unittest", "-v", str(LAB / "test_fraud_service.py")], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    lines = [l for l in p.stderr.splitlines() if " ... " in l]
    tests = [{"test": l.split(" ... ")[0].split(" (")[0], "case": (l.split("(")[1].split(")")[0].split(".")[-1] if "(" in l else ""),
              "ok": l.rstrip().endswith("ok")} for l in lines]
    summary = [l for l in p.stderr.splitlines() if l.startswith("Ran ") or l.startswith("OK") or l.startswith("FAILED")]
    return {"tests": tests, "passed": sum(t["ok"] for t in tests), "total": len(tests), "ok": p.returncode == 0, "summary": summary,
            "command": "python -m unittest -v labs/lab16/test_fraud_service.py"}


def run_162(quick: bool = False, log=print) -> dict:
    ensure_schema(log)
    load = load_fraud(quick, log)
    ut = run_unit_tests()
    log(f"   unit tests (unittest.mock): {ut['passed']}/{ut['total']} passed")
    svc = service()
    # integration test: the full fraud analysis workflow on a planted ring member and on an ordinary user
    ring_user = load["rings"][0][0]
    normal = next(u for u in ["U00010", "U00020", "U00030", "U00040"] if all(u not in r for r in load["rings"]))
    a_ring, a_norm = svc.analyze_user(ring_user), svc.analyze_user(normal)
    found_ring = any(set(r["ring"]) == set(load["rings"][0]) for r in a_ring["graph"]["rings"])
    txn = svc.get_transaction("T000001")
    integration = [
        {"test": "get_transaction('T000001') returns the full document", "ok": bool(txn and {"amount", "merchant", "timestamp", "risk_score"} <= set(txn))},
        {"test": f"get_user_profile('{ring_user}') returns events newest first", "ok": _sorted_desc(a_ring["profile"])},
        {"test": f"detect_fraud_rings('{ring_user}') finds the planted ring {' -> '.join(load['rings'][0])}", "ok": found_ring},
        {"test": f"ring member {ring_user} scored high", "ok": a_ring["risk"]["level"] == "high"},
        {"test": f"ordinary user {normal} has no ring", "ok": not a_norm["graph"]["rings"]},
        {"test": "all three backends answered (no degraded source)", "ok": not a_ring["degraded"] and not a_norm["degraded"]},
    ]
    detected = 0
    for ring in load["rings"]:
        r = svc.detect_fraud_rings(ring[0], 3)
        detected += any(set(x["ring"]) == set(ring) for x in r["rings"])
    integration.append({"test": f"every planted ring detected from its first member ({detected}/{len(load['rings'])})", "ok": detected == len(load["rings"])})
    for t in integration:
        log(f"   [{'PASS' if t['ok'] else 'FAIL'}] {t['test']}")
    # latency of each method (50 calls each on different keys)
    rnd = random.Random(7)
    users = [f"U{rnd.randint(1, load['users']):05d}" for _ in range(50)]
    tids = [f"T{rnd.randint(1, 4000):06d}" for _ in range(50)]
    lat = {}
    lat["get_transaction (MongoDB)"] = _lat(lambda i: svc.get_transaction(tids[i]))
    lat["get_user_profile (Cassandra)"] = _lat(lambda i: svc.get_user_profile(users[i]))
    for h in (1, 2, 3):
        lat[f"detect_fraud_rings, {h} hop{'s' if h > 1 else ''} (Neo4j)"] = _lat(lambda i, h=h: svc.detect_fraud_rings(users[i], h))
    lat["analyze_user (all three, merged)"] = _lat(lambda i: svc.analyze_user(users[i]), n=20)
    for k, v in lat.items():
        log(f"   {k:<42} median {v['median_ms']:>8} ms   p95 {v['p95_ms']:>8} ms")
    cb = circuit_demo(log)
    pool = {"mongodb": "maxPoolSize=" + str(PG.MONGO_POOL) + " (PyMongo pool per server)", "cassandra": f"executor_threads={PG.CASS_THREADS}, "
            "protocol v5: up to 32,768 concurrent requests per connection", "neo4j": f"max_connection_pool_size={PG.NEO4J_POOL}"}
    ok = ut["ok"] and all(t["ok"] for t in integration) and cb["pass"]
    return {"load": load, "unit_tests": ut, "integration": integration, "examples": {"ring_user": _jd(a_ring), "normal_user": _jd(a_norm)},
            "latency": lat, "circuit": cb, "pooling": pool, "pass": ok}


def _sorted_desc(rows):
    ts = [r["event_time"] for r in rows or []]
    return bool(ts) and ts == sorted(ts, reverse=True)


def _lat(fn, n: int = 50) -> dict:
    ms = []
    fn(0)
    for i in range(n):
        t0 = time.perf_counter()
        fn(i % 50)
        ms.append((time.perf_counter() - t0) * 1000)
    ms.sort()
    return {"median_ms": round(ms[len(ms) // 2], 2), "p95_ms": round(ms[int(0.95 * (len(ms) - 1))], 2), "min_ms": round(ms[0], 2),
            "max_ms": round(ms[-1], 2), "calls": n}


class _Clock:
    def __init__(self):
        self.t = time.monotonic()

    def __call__(self):
        return self.t + self.extra if hasattr(self, "extra") else self.t + 0

    def advance(self, s):
        self.extra = getattr(self, "extra", 0) + s


def circuit_demo(log=print) -> dict:
    """Real backends, one injected outage of Cassandra; a controllable clock stands in for the 30 s wait."""
    clock = _Clock()
    svc = service(clock=clock)
    user = "U00001"
    events = []

    def call(label):
        t0 = time.perf_counter()
        r = svc.analyze_user(user)
        events.append({"call": label, "cassandra": svc.breakers["cassandra"].state, "degraded": r["degraded"],
                       "cassandra_result": r["sources"]["cassandra"], "ms": round((time.perf_counter() - t0) * 1000, 2)})
        return r
    call("1 normal")
    svc.inject_fault("cassandra")
    for i in range(3):
        call(f"{i + 2} Cassandra down - failure {i + 1}")
    call("5 circuit open - Cassandra skipped, no call made")
    svc.inject_fault("cassandra", False)
    call("6 Cassandra is back, but < 30 s - still skipped")
    clock.advance(30.1)
    call("7 after 30 s - half-open trial call succeeds -> closed")
    for e in events:
        log(f"   circuit: {e['call']:<55} cassandra={e['cassandra']:<9} degraded={e['degraded']}")
    ok = (events[0]["cassandra"] == "closed" and events[3]["cassandra"] == "open" and events[4]["cassandra_result"].get("skipped")
          and events[5]["cassandra_result"].get("skipped") and events[6]["cassandra"] == "closed" and not events[6]["degraded"])
    return {"events": events, "history": svc.breakers["cassandra"].history, "threshold": 3, "reset_after_s": 30, "pass": bool(ok)}


# ============================================================================ Applied: Lab 10 billing -> fraud check
def run_applied(log=print) -> dict:
    """The Kavach classifier billing ledger (Lab 10, wherever it lives now: rs0 / shards / prod) goes through the polyglot
    fraud check: payments -> MongoDB transactions, events -> Cassandra user_profiles, users -> Neo4j."""
    from . import billing as B
    try:
        bdb = B._db()
        pays = list(bdb.payments.find({}, {"_id": 0}).sort("at", -1).limit(3000))
        backend = B.backend()["label"]
    except Exception as exc:                          # noqa: BLE001
        log(f"   billing ledger not reachable ({str(exc)[:100]}) - skipped")
        return {"available": False, "reason": str(exc)[:200], "pass": True}
    if not pays:
        log("   billing ledger is empty (no payments yet) - nothing to import")
        return {"available": True, "imported": 0, "backend": backend, "pass": True}
    docs, rows = [], []
    for p in pays:
        tid = f"BILL-{p.get('idem', uuid.uuid4().hex)[:24]}-{p['user_id'][:20]}"
        ts = p.get("at") or datetime.now(UTC)
        docs.append({"txn_id": tid, "user_id": p["user_id"], "amount": float(p.get("amount", 0)), "merchant": "Kavach classifier (Lab 10)",
                     "country": "IN", "timestamp": ts, "risk_score": 0.05, "channel": "wallet", "kind": p.get("purpose", "payment"), "source": "lab10"})
        rows.append((p["user_id"], ts, tid, f"billing_{p.get('purpose', 'payment')}", float(p.get("amount", 0)), "Kavach classifier", "IN"))
    db = PG.mongo()[FRAUD_DB]
    db.transactions.delete_many({"source": "lab10"})
    db.transactions.bulk_write([InsertOne(d) for d in docs], ordered=False)
    PG.bulk(f"INSERT INTO {KS}.user_profiles (user_id, event_time, txn_id, event_type, amount, merchant, country) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    users = sorted({p["user_id"] for p in pays})
    PG.cypher_write("UNWIND $ids AS id MERGE (u:User {id: id}) SET u.source = 'lab10'", ids=users)
    svc = service()
    sample = [svc.analyze_user(u) for u in users[:5]]
    log(f"   imported {len(docs)} payments of {len(users)} Kavach users from the billing ledger ({backend}); "
        f"analysed {len(sample)}: " + ", ".join(f"{s['user_id']}={s['risk']['level']}" for s in sample))
    return {"available": True, "backend": backend, "imported": len(docs), "users": len(users),
            "sample": [{"user": s["user_id"], "risk": s["risk"], "transactions": len(s["transactions"] or []),
                        "profile_events": len(s["profile"] or []), "rings": len((s["graph"] or {}).get("rings", []))} for s in sample],
            "pass": all(not s["degraded"] for s in sample)}


# ============================================================================ status / results
def status() -> dict:
    a = PG.availability()
    out = {"available": all(a[k]["ok"] for k in ("mongodb", "cassandra", "neo4j")), "backends": a}
    if not out["available"]:
        out["reason"] = "; ".join(f"{k}: {a[k].get('error')}" for k in ("mongodb", "cassandra", "neo4j") if not a[k]["ok"])
        return out
    try:
        out["counts"] = {
            "mongodb": {"students": PG.mongo()[UNI_DB].students.estimated_document_count(), "courses": PG.mongo()[UNI_DB].courses.estimated_document_count(),
                        "transactions": PG.mongo()[FRAUD_DB].transactions.estimated_document_count()},
            "neo4j": PG.cypher("MATCH (n) WITH labels(n)[0] AS l, count(*) AS c RETURN collect({label: l, count: c}) AS x")[0]["x"],
            "neo4j_rels": PG.cypher("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY t"),
        }
    except Exception as exc:                          # noqa: BLE001
        out["counts_error"] = str(exc)[:200]
    return out


def load_results() -> dict:
    p = RESULTS / "lab16_results.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def save_part(name: str, value: dict):
    out = load_results()
    out[name] = _jd(value)
    out.setdefault("environment", {})["updated"] = datetime.now(UTC).isoformat()
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "lab16_results.json").write_text(json.dumps(out, indent=2, default=str))

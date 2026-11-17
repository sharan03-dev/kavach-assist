"""Lab 15 - Designing and architecting a MongoDB-backed product as a service, applied to Labs 14 -> 7 (mentor's rule).

    Lab 15.1   labs/lab15/tasktracker/app.py - the TaskTracker SaaS API (FastAPI): JWT tenants, CRUD where every query is
               tenant-scoped, rate limiting with MongoDB as the counter store; activity: RBAC (admin / member).
               Mounted at /tasktracker on the website (Swagger UI at /tasktracker/docs) and used by the TaskTracker page.
    Lab 15.2   labs/lab15/migrate_tenants.py - document-level -> collection-per-tenant migration, leakage validation,
               performance comparison, backup granularity, schema evolution, recommendation.
    Applied    the SaaS runs on the Lab 13 production replica set as the least-privilege user tasktracker_app (Lab 12
               idea), writes with w: majority (Lab 9), creates a tenant + its admin in one transaction (Lab 10), is backed
               up hourly and watched by the Lab 14 ops daemon, and its per-tenant restores use the Lab 14 tools.
Every test goes through the real HTTP layer (FastAPI TestClient on the same app), against the real production set.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt as pyjwt
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError, WriteError

from . import lab13 as L13
from . import lab14 as L14

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "labs" / "lab15"
sys.path.insert(0, str(LAB))
from tasktracker import app as TT          # noqa: E402  (the Lab 15.1 API, used as a module)
import migrate_tenants as MT               # noqa: E402  (the Lab 15.2 script)

RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
HEAVY = threading.Lock()
DB = "tasktracker"
DEMO_PW = "Lab15-demo!"
DEMO = {
    "acme": {"name": "Acme Robotics", "plan": "free", "users": [("admin@acme.test", "Asha (admin)", "admin"),
                                                                ("ravi@acme.test", "Ravi", "member"),
                                                                ("meera@acme.test", "Meera", "member")]},
    "globex": {"name": "Globex Corp", "plan": "pro", "users": [("admin@globex.test", "Gopal (admin)", "admin"),
                                                               ("lin@globex.test", "Lin", "member")]},
    "initech": {"name": "Initech", "plan": "free", "users": [("admin@initech.test", "Isha (admin)", "admin"),
                                                             ("pete@initech.test", "Pete", "member")]},
}
TASKS = ["Rotate the API keys", "Review the Q3 roadmap", "Fix login timeout", "Write onboarding guide",
         "Patch CVE in base image", "Plan the team offsite", "Migrate reports to v2", "Load-test the checkout"]


def _jd(x):
    return json.loads(json.dumps(x, default=str))


# ============================================================================ connection (production set, least privilege)
def tt_uri() -> str:
    sec = L13.secrets()
    if "tasktracker_app" not in sec:
        raise RuntimeError("TaskTracker is not set up yet - start Lab 15 (docker compose up -d lab15)")
    return L14.uri("tasktracker_app").replace("/?", f"/{DB}?", 1)


def configure():
    TT.configure(uri=tt_uri, db=DB)


configure()


def adm():
    return L13.client("admin")


def ensure_user(log=print) -> dict:
    a = adm()
    have = {u["user"] for u in a.admin.command("usersInfo")["users"]}
    roles = [{"role": "readWrite", "db": DB}]
    if "tasktracker_app" in have:
        a.admin.command("updateUser", "tasktracker_app", pwd=L14.password("tasktracker_app"), roles=roles)
    else:
        a.admin.command("createUser", "tasktracker_app", pwd=L14.password("tasktracker_app"), roles=roles,
                        mechanisms=["SCRAM-SHA-256"])
    configure()
    c = MongoClient(L14.uri("tasktracker_app"), serverSelectionTimeoutMS=4000)
    tests = [("read tasktracker.tasks", lambda: c[DB].tasks.find_one()),
             ("write tasktracker.tasks", lambda: c[DB].probe.insert_one({"x": 1})),
             ("read cti_billing (Lab 10)", lambda: c.cti_billing.accounts.find_one()),
             ("read cti_platform (Lab 7)", lambda: c.cti_platform.reports.find_one()),
             ("collMod (change / switch off the validator)", lambda: c[DB].command("collMod", "tasks", validator={})),
             ("drop another database", lambda: c.lab14.command("dropDatabase")),
             ("createUser", lambda: c.admin.command("createUser", "x15", pwd="x" * 12, roles=[])),
             ("serverStatus", lambda: c.admin.command("serverStatus"))]
    m = {}
    for name, fn in tests:
        try:
            fn(); m[name] = "allowed"
        except OperationFailure as exc:
            m[name] = "refused" if exc.code == 13 else f"error {exc.code}"
    c[DB].probe.drop() if m["write tasktracker.tasks"] == "allowed" else None
    c.close()
    log("   tasktracker_app (readWrite on tasktracker only): " + ", ".join(f"{k}={v}" for k, v in m.items()))
    return {"user": "tasktracker_app", "roles": ["readWrite@tasktracker"], "matrix": m}


def seed_demo(log=print) -> dict:
    db = TT.get_db()
    ix = TT.init_db(adm()[DB])          # schema + indexes = deploy-time job as admin: the app user may not run collMod
    now = datetime.now(UTC)
    made = {"tenants": 0, "users": 0, "tasks": 0}
    for tid, t in DEMO.items():
        if db.tenants.update_one({"_id": tid}, {"$setOnInsert": {"name": t["name"], "plan": t["plan"], "created_at": now}},
                                 upsert=True).upserted_id:
            made["tenants"] += 1
        for email, name, role in t["users"]:
            r = db.users.update_one({"tenant_id": tid, "email": email},
                                    {"$setOnInsert": {"name": name, "role": role, "pw": TT.hash_pw(DEMO_PW), "created_at": now}},
                                    upsert=True)
            made["users"] += 1 if r.upserted_id else 0
        if db.tasks.count_documents({"tenant_id": tid}) == 0:
            us = list(db.users.find({"tenant_id": tid}))
            docs = []
            for i, title in enumerate(TASKS):
                u = us[i % len(us)]
                docs.append({"tenant_id": tid, "owner_id": str(u["_id"]), "owner": u.get("name", ""), "title": f"{title}",
                             "description": f"{t['name']} - demo task {i + 1}", "priority": ["low", "medium", "high"][i % 3],
                             "status": ["open", "in_progress", "done"][i % 3], "created_at": now - timedelta(hours=i * 7)})
            db.tasks.insert_many(docs)
            made["tasks"] += len(docs)
    log(f"   schema ready (validators on tasks/users/tenants; indexes {', '.join(sum(ix.values(), []))}); demo data added: {made}")
    return {"indexes": ix, "created": made, "tenants": list(DEMO), "password": DEMO_PW}


def demo_users() -> list:
    return [{"tenant_id": tid, "tenant": t["name"], "plan": t["plan"], "email": e, "name": n, "role": r}
            for tid, t in DEMO.items() for e, n, r in t["users"]]


# ============================================================================ helpers: real HTTP through TestClient
def _client():
    from fastapi.testclient import TestClient
    return TestClient(TT.app, raise_server_exceptions=False)


def _login(c, tid, email):
    r = c.post("/auth/login", json={"tenant_id": tid, "email": email, "password": DEMO_PW})
    if r.status_code != 200:
        raise RuntimeError(f"login {email}: {r.status_code} {r.text[:120]}")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _row(rows, log, test, expected, got, detail=""):
    ok = got == expected if not isinstance(expected, (list, tuple)) else got in expected
    rows.append({"test": test, "expected": expected, "got": got, "pass": ok, "detail": detail})
    log(f"   [{'PASS' if ok else 'FAIL'}] {test}: expected {expected}, got {got}{(' - ' + detail) if detail else ''}")


def _wait_window(margin=10):
    left = TT.WINDOW_S - (time.time() % TT.WINDOW_S)
    if left < margin:
        time.sleep(left + 0.2)


def _reset_rate(tids):
    TT.get_db().rate_limits.delete_many({"tenant_id": {"$in": list(tids)}})


# ============================================================================ Lab 15.1 - CRUD + isolation
def run_151(log=print) -> dict:
    seed_demo(log)
    _reset_rate(DEMO)
    c, rows = _client(), []
    ravi, gadm = _login(c, "acme", "ravi@acme.test"), _login(c, "globex", "admin@globex.test")
    r = c.post("/auth/login", json={"tenant_id": "acme", "email": "ravi@acme.test", "password": "wrong"})
    _row(rows, log, "login with a wrong password", 401, r.status_code)
    log("   -- CRUD (every query filtered by the tenant_id from the JWT)")
    r = c.post("/tasks/", json={"title": "Lab 15 CRUD test", "description": "created by run_151", "priority": "high"}, headers=ravi)
    _row(rows, log, "POST /tasks/ (create)", 201, r.status_code, f"tenant_id={r.json().get('tenant_id')}")
    tid_ = r.json()["id"]
    r = c.get("/tasks/", headers=ravi)
    _row(rows, log, "GET /tasks/ (list) contains it, only acme tasks", True,
         any(t["id"] == tid_ for t in r.json()) and all(t["tenant_id"] == "acme" for t in r.json()), f"{len(r.json())} tasks")
    _row(rows, log, "GET /tasks/{id} (read)", 200, c.get(f"/tasks/{tid_}", headers=ravi).status_code)
    r = c.patch(f"/tasks/{tid_}", json={"status": "in_progress"}, headers=ravi)
    _row(rows, log, "PATCH /tasks/{id} (update)", 200, r.status_code, f"status={r.json().get('status')}")
    log("   -- tenant isolation: Globex tries to reach the Acme task")
    _row(rows, log, "Globex GET the Acme task id", 404, c.get(f"/tasks/{tid_}", headers=gadm).status_code, "not 403: ids of other tenants are never confirmed")
    _row(rows, log, "Globex PATCH the Acme task", 404, c.patch(f"/tasks/{tid_}", json={"status": "done"}, headers=gadm).status_code)
    _row(rows, log, "Globex DELETE the Acme task", 404, c.delete(f"/tasks/{tid_}", headers=gadm).status_code)
    r = c.get("/tasks/?tenant_id=acme", headers=gadm)
    _row(rows, log, "Globex GET /tasks/?tenant_id=acme (the sample's parameter)", True,
         all(t["tenant_id"] == "globex" for t in r.json()), "parameter ignored - the tenant comes from the token")
    _row(rows, log, "no token", 401, c.get("/tasks/").status_code)
    forged = pyjwt.encode({"sub": "x", "tenant_id": "acme", "role": "admin", "exp": datetime.now(UTC) + timedelta(hours=1)},
                          "attacker-secret", algorithm="HS256")
    _row(rows, log, "token signed with another key (tenant_id=acme, role=admin)", 401,
         c.get("/tasks/", headers={"Authorization": f"Bearer {forged}"}).status_code)
    none_tok = pyjwt.encode({"sub": "x", "tenant_id": "acme", "exp": datetime.now(UTC) + timedelta(hours=1)}, None, algorithm="none") \
        if "none" in pyjwt.algorithms.get_default_algorithms() else "e30.e30."
    _row(rows, log, "alg=none token", 401, c.get("/tasks/", headers={"Authorization": f"Bearer {none_tok}"}).status_code)
    claims = pyjwt.decode(ravi["Authorization"][7:], options={"verify_signature": False})
    swap = pyjwt.encode({**claims, "tenant_id": "globex"}, TT._secret(), algorithm="HS256")
    _row(rows, log, "valid signature but tenant_id switched to globex", 401,
         c.get("/tasks/", headers={"Authorization": f"Bearer {swap}"}).status_code, "the user is re-read with its tenant")
    exp = pyjwt.encode({**claims, "exp": datetime.now(UTC) - timedelta(minutes=1)}, TT._secret(), algorithm="HS256")
    _row(rows, log, "expired token", 401, c.get("/tasks/", headers={"Authorization": f"Bearer {exp}"}).status_code)
    log("   -- validation")
    _row(rows, log, "priority 'urgent!!' (API, pydantic)", 422,
         c.post("/tasks/", json={"title": "x", "priority": "urgent!!"}, headers=ravi).status_code)
    try:
        adm()[DB].tasks.insert_one({"tenant_id": "acme", "owner_id": "x", "title": "bad", "priority": "urgent!!",
                                    "status": "open", "created_at": datetime.now(UTC)})
        got = "stored"
    except WriteError:
        got = "rejected"
    _row(rows, log, "priority 'urgent!!' written directly to MongoDB", "rejected", got, "$jsonSchema validator = second line of defence")
    _row(rows, log, "DELETE /tasks/{id}", 204, c.delete(f"/tasks/{tid_}", headers=ravi).status_code)
    _row(rows, log, "GET after delete", 404, c.get(f"/tasks/{tid_}", headers=ravi).status_code)
    e = TT.get_db().tasks.find({"tenant_id": "acme"}).sort("created_at", -1).limit(20).explain()
    s = e["executionStats"]
    plan = json.dumps(e["queryPlanner"]["winningPlan"])
    ex = {"index": "tenant_created" if "tenant_created" in plan else "?", "stage": "IXSCAN" if "IXSCAN" in plan else "COLLSCAN",
          "keys_examined": s["totalKeysExamined"], "docs_examined": s["totalDocsExamined"], "returned": s["nReturned"],
          "total_tasks": TT.get_db().tasks.estimated_document_count()}
    log(f"   explain list query: {ex['stage']} {ex['index']}, keys {ex['keys_examined']}, docs {ex['docs_examined']}, "
        f"returned {ex['returned']} of {ex['total_tasks']} tasks in the collection")
    return {"tests": rows, "explain": ex, "sample": sample_findings(log), "pass": all(r["pass"] for r in rows)}


def sample_findings(log=print) -> dict:
    """The classroom sample, unchanged, against a scratch database on the same production set."""
    import importlib
    os.environ.setdefault("MONGO_URI", L14.uri("admin"))
    sample = importlib.import_module("sample_app")
    sample.db = MongoClient(L14.uri("admin"))["tasktracker_sample"]
    sample.db.tasks.drop()
    from fastapi.testclient import TestClient
    c = TestClient(sample.app, raise_server_exceptions=False)
    r1 = c.post("/tasks/?tenant_id=acme", json={"title": "Acme secret roadmap", "description": "confidential"})
    stored = sample.db.tasks.count_documents({})
    r2 = c.post("/tasks/?tenant_id=acme", json={"title": "x", "description": "x", "priority": "urgent!!"})
    r3 = c.get("/tasks/?tenant_id=acme")
    out = {"create_status": r1.status_code, "stored_despite_error": stored >= 1,
           "invalid_priority_stored": sample.db.tasks.count_documents({"priority": "urgent!!"}) == 1,
           "read_other_tenant_without_login": r3.status_code, "leaked_titles": [t["title"] for t in r3.json()][:3] if r3.status_code == 200 else []}
    sample.db.client.drop_database("tasktracker_sample")
    log(f"   the classroom sample: POST -> HTTP {out['create_status']} but stored={out['stored_despite_error']} (ObjectId added by "
        f"insert_one is not JSON) | priority 'urgent!!' stored={out['invalid_priority_stored']} | GET ?tenant_id=acme without "
        f"any login -> HTTP {out['read_other_tenant_without_login']}: {out['leaked_titles']}")
    return out


# ============================================================================ rate limiting
def run_rate(log=print) -> dict:
    db = TT.get_db()
    TT.init_db(adm()[DB])
    now = datetime.now(UTC)
    for tid in ("ratelab", "ratelab-b"):
        db.tenants.update_one({"_id": tid}, {"$set": {"name": f"Rate test {tid}", "plan": "free", "rate_limit": 50},
                                             "$setOnInsert": {"created_at": now}}, upsert=True)
        db.users.update_one({"tenant_id": tid, "email": "bot@rate.test"},
                            {"$setOnInsert": {"name": "bot", "role": "admin", "pw": TT.hash_pw(DEMO_PW), "created_at": now}}, upsert=True)
    TT._plans.clear()
    _reset_rate(["ratelab", "ratelab-b", "acme"])
    c = _client()
    h = _login(c, "ratelab", "bot@rate.test")
    acme = _login(c, "acme", "admin@acme.test")
    _wait_window()
    log("   burst: 70 requests to GET /tasks/ from tenant ratelab (limit 50 / minute)")
    codes, series = [], []
    t0 = time.perf_counter()
    for i in range(70):
        r = c.get("/tasks/", headers=h)
        codes.append(r.status_code)
        series.append({"i": i + 1, "code": r.status_code, "remaining": int(r.headers.get("X-RateLimit-Remaining", -1))})
    burst_s = time.perf_counter() - t0
    last = r
    other = c.get("/tasks/", headers=acme).status_code
    win = int(time.time() // TT.WINDOW_S) * TT.WINDOW_S
    doc = db.rate_limits.find_one({"_id": f"ratelab:{win}"})
    ok, lim = codes.count(200), codes.count(429)
    log(f"   -> {ok} x 200, {lim} x 429 in {burst_s:.2f} s; Retry-After {last.headers.get('Retry-After')} s; tenant acme at the same "
        f"time -> {other}; counter document {_jd(doc)}")
    log("   concurrency: 200 simultaneous requests (32 threads) for tenant ratelab-b, limit 50")
    _wait_window()
    res = []

    def hit(_):
        return TT.check_rate("ratelab-b", 50)["allowed"]
    with ThreadPoolExecutor(32) as ex:
        res = list(ex.map(hit, range(200)))
    win2 = int(time.time() // TT.WINDOW_S) * TT.WINDOW_S
    cdoc = db.rate_limits.find_one({"_id": f"ratelab-b:{win2}"}) or {}
    log(f"   -> allowed exactly {sum(res)} of 200 (counter {cdoc.get('count')}): $inc inside findAndModify is atomic, no race")
    ttl = next((i for i in db.rate_limits.list_indexes() if i["name"] == "ttl_ts"), {})
    return _jd({"limit": 50, "burst": {"requests": 70, "ok": ok, "limited": lim, "seconds": round(burst_s, 2), "series": series,
                                       "retry_after": last.headers.get("Retry-After"),
                                       "headers": {k: v for k, v in last.headers.items() if k.lower().startswith(("x-ratelimit", "retry"))}},
                "other_tenant_status": other, "counter_doc": doc,
                "concurrency": {"requests": 200, "threads": 32, "allowed": sum(res), "counter": cdoc.get("count")},
                "ttl_index": {"key": dict(ttl.get("key", {})), "expireAfterSeconds": ttl.get("expireAfterSeconds")},
                "pass": ok == 50 and lim == 20 and other == 200 and sum(res) == 50})


# ============================================================================ activity: RBAC
def run_rbac(log=print) -> dict:
    seed_demo(lambda *a: None)
    _reset_rate(DEMO)
    c, rows = _client(), []
    A, R, M, G = (_login(c, "acme", "admin@acme.test"), _login(c, "acme", "ravi@acme.test"),
                  _login(c, "acme", "meera@acme.test"), _login(c, "globex", "admin@globex.test"))
    t1 = c.post("/tasks/", json={"title": "Ravi's RBAC task"}, headers=R).json()["id"]
    t2 = c.post("/tasks/", json={"title": "Meera's RBAC task"}, headers=M).json()["id"]
    cases = [
        ("member Ravi reads Meera's task (team board)", lambda: c.get(f"/tasks/{t2}", headers=R), 200),
        ("member Ravi updates Meera's task", lambda: c.patch(f"/tasks/{t2}", json={"status": "done"}, headers=R), 403),
        ("member Ravi deletes Meera's task", lambda: c.delete(f"/tasks/{t2}", headers=R), 403),
        ("member Ravi updates his own task", lambda: c.patch(f"/tasks/{t1}", json={"status": "in_progress"}, headers=R), 200),
        ("admin Asha updates Meera's task", lambda: c.patch(f"/tasks/{t2}", json={"priority": "high"}, headers=A), 200),
        ("member Ravi lists the users (admin endpoint)", lambda: c.get("/users/", headers=R), 403),
        ("member Ravi adds a user", lambda: c.post("/users/", json={"email": "x@acme.test", "password": "longpassword"}, headers=R), 403),
        ("admin Asha lists the users", lambda: c.get("/users/", headers=A), 200),
        ("Globex admin deletes Meera's task (other tenant)", lambda: c.delete(f"/tasks/{t2}", headers=G), 404),
        ("admin Asha deletes Ravi's task", lambda: c.delete(f"/tasks/{t1}", headers=A), 204),
    ]
    for name, fn, exp in cases:
        _row(rows, log, name, exp, fn().status_code)
    db = TT.get_db()
    meera = db.users.find_one({"tenant_id": "acme", "email": "meera@acme.test"})
    db.users.update_one({"_id": meera["_id"]}, {"$set": {"role": "admin"}})
    t3 = c.post("/tasks/", json={"title": "Ravi's second task"}, headers=R).json()["id"]
    _row(rows, log, "Meera promoted to admin in MongoDB - SAME token - deletes Ravi's task", 204,
         c.delete(f"/tasks/{t3}", headers=M).status_code, "the role is re-read on every request")
    db.users.update_one({"_id": meera["_id"]}, {"$set": {"role": "member"}})
    _row(rows, log, "Meera demoted again - same token - lists users", 403, c.get("/users/", headers=M).status_code)
    c.delete(f"/tasks/{t2}", headers=A)
    return {"tests": rows, "pass": all(r["pass"] for r in rows)}


# ============================================================================ applied to the lower labs
def run_applied(log=print) -> dict:
    out = {"least_privilege": ensure_user(log)}
    TT.init_db(adm()[DB])               # the unique (tenant_id, email) index is what makes the 2nd insert fail
    db, c = TT.get_db(), _client()
    db.tenants.delete_many({"_id": {"$in": ["newco", "rollco"]}})
    db.users.delete_many({"tenant_id": {"$in": ["newco", "rollco"]}})
    body = {"tenant_id": "newco", "tenant_name": "NewCo", "admin_email": "boss@newco.test", "admin_name": "Boss", "password": DEMO_PW}
    r1 = c.post("/auth/signup", json=body)
    r2 = c.post("/auth/signup", json=body)
    db.users.insert_one({"tenant_id": "rollco", "email": "boss@rollco.test", "role": "member", "pw": TT.hash_pw("x" * 12)})
    r3 = c.post("/auth/signup", json={**body, "tenant_id": "rollco", "tenant_name": "RollCo", "admin_email": "boss@rollco.test"})
    rolled = db.tenants.count_documents({"_id": "rollco"}) == 0
    out["transaction"] = {"signup": r1.status_code, "signup_again": r2.status_code, "newco_users": db.users.count_documents({"tenant_id": "newco"}),
                          "failing_signup": r3.status_code, "tenant_rolled_back": rolled,
                          "explain": "tenant + admin inserted in one transaction; the 2nd insert fails (email exists) -> the tenant insert is rolled back too"}
    log(f"   signup newco -> {r1.status_code}, again -> {r2.status_code}; signup rollco whose admin email already exists -> "
        f"{r3.status_code}, tenant rollco rolled back: {rolled} (Lab 10 transaction)")
    db.tenants.delete_many({"_id": {"$in": ["newco", "rollco"]}})
    db.users.delete_many({"tenant_id": {"$in": ["newco", "rollco"]}})
    try:
        bk = L14.backup_now("tasktracker", log=log)
    except Exception as exc:                            # noqa: BLE001
        bk = {"ok": False, "error": str(exc)[:200]}
    out["backup"] = bk
    rs = L13.rs_summary()
    out["deployment"] = {"replica_set": rs["set"], "members": [f"{m['name']} {m['state']}" for m in rs["members"]],
                         "write_concern": "majority", "db": DB}
    out["pass"] = (out["least_privilege"]["matrix"].get("read cti_billing (Lab 10)") == "refused" and r1.status_code == 201
                   and r2.status_code == 409 and r3.status_code == 409 and rolled and bk.get("ok", False))
    return _jd(out)


def run_migration(quick=False, log=print) -> dict:
    return _jd(MT.run_all(L14.uri("admin"), "tasktracker_mig", quick, log=log))


# ============================================================================ status + results
def status() -> dict:
    av = L13.available()
    out = {**av, "db": DB, "demo_users": demo_users(), "password": DEMO_PW}
    if av["available"]:
        try:
            db = TT.get_db()
            out["counts"] = {"tenants": db.tenants.count_documents({}), "users": db.users.count_documents({}),
                             "tasks": db.tasks.count_documents({})}
            win = int(time.time() // TT.WINDOW_S) * TT.WINDOW_S
            out["rate_windows"] = _jd(list(db.rate_limits.find({"window": win}).sort("count", -1).limit(10)))
            out["ready"] = out["counts"]["users"] > 0
        except Exception as exc:                        # noqa: BLE001
            out["ready"], out["reason"] = False, str(exc)[:200]
    return _jd(out)


def save_part(name, data):
    p = RESULTS / "lab15_results.json"
    try:
        doc = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        doc = {}
    doc[name] = _jd(data)
    doc["updated_at"] = datetime.now(UTC).isoformat()
    p.write_text(json.dumps(doc, indent=2))
    try:
        L14.kdb().lab_results.update_one({"_id": "lab_15"}, {"$set": {name: _jd(data)}}, upsert=True)
    except PyMongoError:
        pass


def load_results() -> dict:
    try:
        d = L14.kdb().lab_results.find_one({"_id": "lab_15"}, {"_id": 0})
        if d:
            return _jd(d)
    except PyMongoError:
        pass
    p = RESULTS / "lab15_results.json"
    return json.loads(p.read_text()) if p.exists() else {}

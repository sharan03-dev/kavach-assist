"""Lab 12 - Database security and the CIA triad, on a hardened MongoDB (`mongo-secure`) - and applied to Lab 7.

The secure server (docker-compose service `mongo-secure`, port 27040) is a one-member replica set `rs12` started with
    --auth (keyFile)  --tlsMode requireTLS  --tlsCAFile ca.crt  (client certificates required)
    --setParameter authenticationMechanisms=SCRAM-SHA-256        (SHA-1 and X.509 log-ins switched off)
Its first admin user is created by labs/lab12/setup/certs.py -> bootstrap.sh through the localhost exception.

    Lab 12.1   admin + least-privilege users, custom roles (createRole), the classroom `analyticsReader` snippet,
               verification matrix: every user x every operation -> allowed / "not authorized" (code 13)
    Activity   field-level redaction with db.createView(): employees_public hides `salary`; staff read the view,
               HR reads the collection.  Measured weakness: an exclusion view leaks fields added later.
    Lab 12.2   TLS: plain connections refused, wrong CA / no client certificate / rogue certificate refused,
               encrypted connection works (TLS 1.3), certificate chain verified and documented
    12 on 7    the Kavach CTI platform (Lab 7) on the secure server: least-privilege service accounts for the
               website (analyst), the 5-minute auto-updater (ingest) and the Lab 10 billing (finance), a masked
               customer view, schema validation, Client-Side Field Level Encryption of UPI ids, an access audit
               trail (profiler + change stream), IP allow-listing, and the threat model with live evidence
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import socket
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import Binary
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import OperationFailure, PyMongoError

UTC = timezone.utc
SECURE_HOST = os.getenv("SECURE_HOST", "mongo-secure:27040")
CERTS = Path(os.getenv("CERTS_DIR", "/certs"))
HEAVY = threading.Lock()
COMPANY, ANALYTICS, CTI, BILL = "lab12_company", "analytics", "cti_secure", "kavach_billing"
KEYVAULT = "encryption.__keyVault"
_clients: dict = {}
_lock = threading.Lock()


# ============================================================================ connections
def host_port() -> tuple[str, int]:
    h, _, p = SECURE_HOST.rpartition(":")
    return (h or SECURE_HOST), int(p or 27040)


def secrets_() -> dict:
    p = CERTS / "secrets.json"
    if not p.exists():
        raise RuntimeError(f"{p} not found - the lab12-certs container creates it (docker compose up -d lab12)")
    return json.loads(p.read_text())


def connect(user: str | None = None, *, password: str | None = None, tls: bool = True, ca: bool = True,
            cert: str | None = "client.pem", mech: str | None = None, timeout: int = 4000, app: str = "lab12") -> MongoClient:
    """A new client. Default = what every Kavach service uses: TLS + CA + client certificate + SCRAM-SHA-256."""
    h, p = host_port()
    kw = dict(host=h, port=p, directConnection=True, serverSelectionTimeoutMS=timeout, connectTimeoutMS=timeout,
              socketTimeoutMS=60000, appname=app, tz_aware=True, retryWrites=False)
    if tls:
        kw["tls"] = True
        if ca:
            kw["tlsCAFile"] = str(CERTS / "ca.crt")
        if cert:
            kw["tlsCertificateKeyFile"] = str(CERTS / cert)
    if user:
        kw.update(username=user, password=password if password is not None else secrets_()[user], authSource="admin")
        if mech:
            kw["authMechanism"] = mech
    return MongoClient(**kw)


def as_user(user: str) -> MongoClient:
    """Cached client per user (the website keeps one connection pool per persona). When the lab re-creates a user
    (dropUser + createUser in another process), MongoDB logs out that user's open connections - so a cached client is
    re-checked with connectionStatus (at most every 2 s) and rebuilt when it is no longer authenticated."""
    with _lock:
        c, checked = _clients.get(user, (None, 0.0))
        if c is not None and time.time() - checked > 2:
            try:
                ok = bool(c.admin.command("connectionStatus")["authInfo"]["authenticatedUsers"])
            except PyMongoError:
                ok = False
            if not ok:
                try:
                    c.close()
                except Exception:                          # noqa: BLE001
                    pass
                c = None
        if c is None:
            c = connect(user, app=f"lab12-{user}")
        _clients[user] = (c, time.time())
        return c


def admin() -> MongoClient:
    return as_user("kavach_admin")


def reset_clients():
    with _lock:
        for c, _ in _clients.values():
            try:
                c.close()
            except Exception:                              # noqa: BLE001
                pass
        _clients.clear()


def available() -> dict:
    if not (CERTS / "ca.crt").exists():
        return {"available": False, "reason": f"no certificates in {CERTS} (the lab12-certs container makes them)"}
    try:
        admin().admin.command("ping")
        return {"available": True}
    except Exception as exc:                              # noqa: BLE001
        reset_clients()
        return {"available": False, "reason": f"secure MongoDB not reachable at {SECURE_HOST}: {str(exc)[:160]}"}


def _j(x):
    return json.loads(json.dumps(x, default=str))


# ============================================================================ outcome of one operation
def attempt(fn) -> dict:
    """Run fn and classify the result the way the lab asks: allowed / denied (not authorized) / rejected ..."""
    t0 = time.perf_counter()
    try:
        r = fn()
        return {"result": "allowed", "detail": str(r)[:220], "ms": round(1000 * (time.perf_counter() - t0), 1)}
    except OperationFailure as e:
        name = (e.details or {}).get("codeName", "")
        kind = {13: "denied", 121: "rejected", 18: "auth-failed", 166: "read-only view", 334: "auth-failed",
                2: "invalid"}.get(e.code, "error")
        msg = (e.details or {}).get("errmsg", str(e))
        return {"result": kind, "code": e.code, "codeName": name, "detail": msg[:220],
                "ms": round(1000 * (time.perf_counter() - t0), 1)}
    except PyMongoError as e:
        return {"result": "refused", "detail": f"{type(e).__name__}: {str(e)[:200]}",
                "ms": round(1000 * (time.perf_counter() - t0), 1)}
    except Exception as e:                                # noqa: BLE001
        return {"result": "error", "detail": f"{type(e).__name__}: {str(e)[:200]}",
                "ms": round(1000 * (time.perf_counter() - t0), 1)}


def _drop_role(adm, name):
    try:
        adm.command("dropRole", name)
    except OperationFailure:
        pass


def _drop_user(adm, name):
    try:
        adm.command("dropUser", name)
    except OperationFailure:
        pass


def _create_user(adm, user, roles, pw, **extra):
    _drop_user(adm, user)
    adm.command("createUser", user, pwd=pw, roles=roles, mechanisms=["SCRAM-SHA-256"], **extra)


def _create_role(adm, name, privileges, roles=None):
    _drop_role(adm, name)
    adm.command("createRole", name, privileges=privileges, roles=roles or [])


# ============================================================================ Lab 12.1 - definitions
CLASSROOM_SNIPPET = """use admin
db.createRole({
  role: "analyticsReader",
  privileges: [ { resource: { db: "analytics", collection: "" }, actions: ["find", "aggregate"] } ],
  roles: []
})
db.createUser({ user: "analytics_app", pwd: passwordPrompt(),
  roles: [ { role: "analyticsReader", db: "admin" } ] })"""

ROLES_121 = [
    {"role": "analyticsReader", "note": "the classroom snippet, corrected: 'aggregate' is not a privilege action - an aggregation "
                                        "needs 'find' on the collections it reads",
     "privileges": [{"resource": {"db": ANALYTICS, "collection": ""}, "actions": ["find", "listCollections"]}]},
    {"role": "appRead", "note": "app_user_read: find (and therefore aggregate) on products and orders only",
     "privileges": [{"resource": {"db": COMPANY, "collection": "products"}, "actions": ["find"]},
                    {"resource": {"db": COMPANY, "collection": "orders"}, "actions": ["find"]}]},
    {"role": "appWrite", "note": "app_user_write: find, insert, update, remove on orders; find on products",
     "privileges": [{"resource": {"db": COMPANY, "collection": "orders"}, "actions": ["find", "insert", "update", "remove"]},
                    {"resource": {"db": COMPANY, "collection": "products"}, "actions": ["find"]}]},
    {"role": "hrManager", "note": "HR: the real employees collection (salary included) + the public view",
     "privileges": [{"resource": {"db": COMPANY, "collection": "employees"}, "actions": ["find", "insert", "update"]},
                    {"resource": {"db": COMPANY, "collection": "employees_public"}, "actions": ["find"]},
                    {"resource": {"db": COMPANY, "collection": "employees_directory"}, "actions": ["find"]}]},
    {"role": "staffReader", "note": "everyone else: only the redacted views - no privilege on employees at all",
     "privileges": [{"resource": {"db": COMPANY, "collection": "employees_public"}, "actions": ["find"]},
                    {"resource": {"db": COMPANY, "collection": "employees_directory"}, "actions": ["find"]}]},
]
USERS_121 = [
    {"user": "kavach_admin", "roles": [{"role": "root", "db": "admin"}], "purpose": "administrator (created at first boot "
     "through the localhost exception, before --auth was enforced over the network)"},
    {"user": "sec_admin", "roles": [{"role": "userAdminAnyDatabase", "db": "admin"}],
     "purpose": "security officer: manages users and roles but cannot read any data (separation of duties)"},
    {"user": "app_user_read", "roles": [{"role": "appRead", "db": "admin"}], "purpose": "read-only application user"},
    {"user": "app_user_write", "roles": [{"role": "appWrite", "db": "admin"}], "purpose": "read/write application user"},
    {"user": "analytics_app", "roles": [{"role": "analyticsReader", "db": "admin"}], "purpose": "the classroom snippet's user"},
    {"user": "hr_user", "roles": [{"role": "hrManager", "db": "admin"}], "purpose": "Activity: has the HR role"},
    {"user": "staff_user", "roles": [{"role": "staffReader", "db": "admin"}], "purpose": "Activity: no HR role"},
]

DEPTS = ["Engineering", "Sales", "Finance", "Security", "Support", "HR"]
FIRST = ["Aarav", "Diya", "Ishaan", "Kavya", "Rohan", "Ananya", "Vihaan", "Meera", "Arjun", "Saanvi", "Kabir", "Nisha",
         "Rahul", "Priya", "Aditya", "Sneha", "Karthik", "Lakshmi", "Varun", "Pooja"]
LAST = ["Sharma", "Reddy", "Iyer", "Nair", "Rao", "Gupta", "Menon", "Patel", "Kumar", "Hegde"]


def _company_data(rng):
    emps = []
    for i in range(1, 41):
        f, l = rng.choice(FIRST), rng.choice(LAST)
        d = rng.choice(DEPTS)
        emps.append({"_id": i, "emp_id": f"E{1000 + i}", "name": f"{f} {l}", "dept": d,
                     "email": f"{f.lower()}.{l.lower()}{i}@kavach.example", "title": rng.choice(["Engineer", "Analyst", "Lead", "Manager"]),
                     "joined": datetime(2018 + rng.randint(0, 7), rng.randint(1, 12), rng.randint(1, 28), tzinfo=UTC),
                     "salary": rng.randrange(600_000, 4_800_000, 25_000)})
    products = [{"_id": f"P{i:03}", "name": n, "price": p} for i, (n, p) in
                enumerate([("Threat feed - Basic", 999), ("Threat feed - Pro", 4999), ("IP reputation API", 1999),
                           ("Classifier seat", 100), ("Incident retainer", 99999)], 1)]
    orders = [{"_id": i, "product": rng.choice(products)["_id"], "qty": rng.randint(1, 5), "status": "paid",
               "at": datetime(2026, rng.randint(1, 10), rng.randint(1, 28), tzinfo=UTC)} for i in range(1, 61)]
    events = [{"_id": i, "page": rng.choice(["/threats", "/vulns", "/ip", "/classify"]), "ms": rng.randint(20, 900),
               "at": datetime(2026, 10, rng.randint(1, 30), tzinfo=UTC)} for i in range(1, 201)]
    return emps, products, orders, events


def setup_121(log=print) -> dict:
    """Create the Lab 12.1 data, roles and users (idempotent: drops and recreates them)."""
    c = admin()
    adm = c.admin
    rng = random.Random(12)
    emps, products, orders, events = _company_data(rng)
    co = c[COMPANY]
    for name in ("employees_public", "employees_directory"):
        co.drop_collection(name)
    for coll, docs in (("employees", emps), ("products", products), ("orders", orders)):
        co.drop_collection(coll)
        co[coll].insert_many(docs)
    c[ANALYTICS].drop_collection("events")
    c[ANALYTICS].events.insert_many(events)
    log(f"   data: {COMPANY}.employees {len(emps)} (with salary), products {len(products)}, orders {len(orders)}, "
        f"{ANALYTICS}.events {len(events)}")

    # the classroom snippet exactly as given
    _drop_role(adm, "analyticsReader")
    snippet = attempt(lambda: adm.command("createRole", "analyticsReader", privileges=[
        {"resource": {"db": ANALYTICS, "collection": ""}, "actions": ["find", "aggregate"]}], roles=[]))
    log(f"   classroom snippet createRole(actions: ['find', 'aggregate']) -> {snippet['result']}: {snippet['detail'][:90]}")
    delete_try = attempt(lambda: adm.command("createRole", "tmpDeleteRole", privileges=[
        {"resource": {"db": COMPANY, "collection": "orders"}, "actions": ["find", "delete"]}], roles=[]))
    _drop_role(adm, "tmpDeleteRole")
    log(f"   solution text 'find/insert/update/delete' -> action 'delete': {delete_try['result']}: {delete_try['detail'][:60]}"
        " (the action is called 'remove')")

    # Activity: redacting views (created before the roles that point at them)
    co.command("create", "employees_public", viewOn="employees", pipeline=[{"$project": {"salary": 0}}])
    co.command("create", "employees_directory", viewOn="employees",
               pipeline=[{"$project": {"_id": 0, "emp_id": 1, "name": 1, "dept": 1, "title": 1, "email": 1}}])
    log("   db.createView('employees_public', 'employees', [{$project: {salary: 0}}])          (exclusion, as asked)")
    log("   db.createView('employees_directory', 'employees', [{$project: {emp_id:1, name:1, ...}}]) (allow-list)")

    for r in ROLES_121:
        _create_role(adm, r["role"], r["privileges"])
        log(f"   createRole {r['role']:<16} " + "; ".join(f"{p['resource']['db']}.{p['resource']['collection'] or '*'}: "
                                                         f"{','.join(p['actions'])}" for p in r["privileges"]))
    pw = secrets_()
    for u in USERS_121:
        if u["user"] == "kavach_admin":
            continue
        _create_user(adm, u["user"], u["roles"], pw[u["user"]])
        log(f"   createUser {u['user']:<15} roles {', '.join(r['role'] for r in u['roles'])}  (SCRAM-SHA-256)")
    reset_clients()
    return {"snippet": {**snippet, "source": CLASSROOM_SNIPPET}, "delete_action": delete_try,
            "roles": ROLES_121, "users": USERS_121,
            "data": {"employees": len(emps), "products": len(products), "orders": len(orders), "events": len(events)}}


def _ops_121():
    m = lambda u: as_user(u)                              # noqa: E731
    return [
        ("find products", lambda u: m(u)[COMPANY].products.find_one()),
        ("aggregate orders", lambda u: list(m(u)[COMPANY].orders.aggregate([{"$group": {"_id": "$product", "n": {"$sum": 1}}}]))),
        ("insert order", lambda u: m(u)[COMPANY].orders.insert_one({"_id": f"t-{u}-{time.time_ns()}", "product": "P001", "qty": 1, "_lab12_test": True}).inserted_id),
        ("update order", lambda u: m(u)[COMPANY].orders.update_one({"_id": 1}, {"$set": {"checked_by": u}}).modified_count),
        ("delete order", lambda u: m(u)[COMPANY].orders.delete_one({"_lab12_test": True, "never": True}).deleted_count),
        ("read employees (salary)", lambda u: m(u)[COMPANY].employees.find_one({}, {"name": 1, "salary": 1})),
        ("read employees_public", lambda u: m(u)[COMPANY].employees_public.find_one()),
        ("read analytics.events", lambda u: m(u)[ANALYTICS].events.count_documents({})),
        ("write analytics.events", lambda u: m(u)[ANALYTICS].events.insert_one({"page": "/x", "_lab12_test": True}).inserted_id),
        ("createUser (escalate)", lambda u: _escalate(m(u))),
    ]


def _escalate(c):
    c.admin.command("createUser", "mallory", pwd="Mallory-1234567890", roles=["root"], mechanisms=["SCRAM-SHA-256"])
    _drop_user(admin().admin, "mallory")          # only reached when the user really may create users
    return "created (and removed again)"


EXPECT_121 = {   # what least privilege SHOULD give; the matrix passes when the measurement equals this
    "app_user_read": {"find products", "aggregate orders"},
    "app_user_write": {"find products", "aggregate orders", "insert order", "update order", "delete order"},
    "analytics_app": {"read analytics.events"},
    "hr_user": {"read employees (salary)", "read employees_public"},
    "staff_user": {"read employees_public"},
    "sec_admin": {"createUser (escalate)"},
    "kavach_admin": None,                                  # root: everything
}


def matrix(users, ops, expect, log=print) -> dict:
    rows, ok_all = [], True
    for u in users:
        cells = {}
        for name, fn in ops:
            out = attempt(lambda: fn(u))
            allowed = out["result"] == "allowed"
            exp = expect.get(u)
            want = True if exp is None else name in exp
            out["expected"] = "allowed" if want else "blocked"
            out["ok"] = allowed == want
            ok_all &= out["ok"]
            cells[name] = out
        rows.append({"user": u, "cells": cells})
        log(f"   {u:<15} " + " ".join(("A" if c["result"] == "allowed" else "x") + ("" if c["ok"] else "!") for c in cells.values()))
    return {"ops": [o[0] for o in ops], "rows": rows, "pass": ok_all}


def matrix_121(log=print) -> dict:
    log("   columns: " + " | ".join(o[0] for o in _ops_121()) + "    (A = allowed, x = not authorized, ! = unexpected)")
    r = matrix(["kavach_admin", "sec_admin", "app_user_read", "app_user_write", "analytics_app", "hr_user", "staff_user"],
               _ops_121(), EXPECT_121, log)
    c = admin()
    c[COMPANY].orders.delete_many({"_lab12_test": True})
    c[COMPANY].orders.update_one({"_id": 1}, {"$unset": {"checked_by": ""}})
    c[ANALYTICS].events.delete_many({"_lab12_test": True})
    return r


# ============================================================================ Activity - field-level redaction
def redaction(log=print) -> dict:
    hr, staff = as_user("hr_user")[COMPANY], as_user("staff_user")[COMPANY]
    out = {}
    out["hr_collection"] = attempt(lambda: hr.employees.find_one({"_id": 1}, {"_id": 0, "name": 1, "dept": 1, "salary": 1}))
    out["staff_view"] = attempt(lambda: staff.employees_public.find_one({"_id": 1}))
    out["staff_collection"] = attempt(lambda: staff.employees.find_one({"_id": 1}))
    out["staff_filter_salary"] = attempt(lambda: staff.employees_public.count_documents({"salary": {"$gt": 0}}))
    out["staff_sort_salary"] = attempt(lambda: [d["name"] for d in staff.employees_public.find({}, {"name": 1}).sort("salary", -1).limit(3)])
    out["staff_lookup"] = attempt(lambda: list(staff.employees_public.aggregate([
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "_id", "as": "raw"}}, {"$limit": 1}])))
    out["staff_insert_view"] = attempt(lambda: staff.employees_public.insert_one({"name": "x"}))
    out["staff_avg_salary"] = attempt(lambda: list(staff.employees_public.aggregate([{"$group": {"_id": None, "avg": {"$avg": "$salary"}}}])))
    out["hr_avg_salary"] = attempt(lambda: list(hr.employees.aggregate([{"$group": {"_id": "$dept", "avg": {"$avg": "$salary"}}}, {"$sort": {"_id": 1}}])))
    sample_hr = hr.employees.find_one({"_id": 1})
    sample_staff = staff.employees_public.find_one({"_id": 1})
    out["fields_hr"], out["fields_staff"] = sorted(sample_hr.keys()), sorted(sample_staff.keys())
    out["sample_hr"], out["sample_staff"] = _j(sample_hr), _j(sample_staff)

    # measured weakness: HR adds a NEW sensitive field -> the exclusion view shows it, the allow-list view does not
    hr.employees.update_one({"_id": 1}, {"$set": {"bank_account": "XXXX-XXXX-4321", "pan": "ABCDE1234F"}})
    leak_pub = staff.employees_public.find_one({"_id": 1})
    leak_dir = staff.employees_directory.find_one({"emp_id": sample_hr["emp_id"]})
    out["leak"] = {"exclusion_view_fields": sorted(leak_pub.keys()), "allowlist_view_fields": sorted(leak_dir.keys()),
                   "exclusion_leaks": "bank_account" in leak_pub, "allowlist_leaks": "bank_account" in leak_dir}
    admin()[COMPANY].employees.update_one({"_id": 1}, {"$unset": {"bank_account": "", "pan": ""}})
    out["view_definition"] = _j(admin()[COMPANY].command("listCollections", filter={"type": "view"})["cursor"]["firstBatch"])
    checks = {
        "HR reads salary from the collection": out["hr_collection"]["result"] == "allowed" and "salary" in out["fields_hr"],
        "staff read the view and get no salary": out["staff_view"]["result"] == "allowed" and "salary" not in out["fields_staff"],
        "staff cannot read the collection (not authorized)": out["staff_collection"]["result"] == "denied",
        "a filter on salary through the view finds nothing": out["staff_filter_salary"].get("detail") == "0",
        "$lookup from the view back to employees is refused": out["staff_lookup"]["result"] == "denied",
        "the view is read-only for staff": out["staff_insert_view"]["result"] != "allowed",
        "allow-list view does not leak a new field": not out["leak"]["allowlist_leaks"],
    }
    out["checks"] = checks
    out["pass"] = all(checks.values())
    for k, v in checks.items():
        log(f"   [{'PASS' if v else 'FAIL'}] {k}")
    log(f"   measured: exclusion view leaks a field added later (bank_account): {out['leak']['exclusion_leaks']}; "
        f"allow-list view: {out['leak']['allowlist_leaks']}")
    return out


# ============================================================================ Lab 12.2 - TLS
def _probe(label, expect_ok, **kw):
    t0 = time.perf_counter()
    c = None
    try:
        c = connect(timeout=2500, **kw)
        c.admin.command("ping")
        res, detail = "connected", "ping ok"
    except PyMongoError as e:
        res, detail = "refused", str(e).split(" (configured timeouts")[0].split(", Timeout:")[0][:200]
    finally:
        if c is not None:
            c.close()
    ok = (res == "connected") == expect_ok
    return {"test": label, "result": res, "detail": detail, "expected": "connected" if expect_ok else "refused", "ok": ok,
            "ms": round(1000 * (time.perf_counter() - t0))}


def handshake() -> dict:
    """Raw TLS handshake like `openssl s_client`: protocol, cipher and the certificate the server presents."""
    h, p = host_port()
    ctx = ssl.create_default_context(cafile=str(CERTS / "ca.crt"))
    ctx.load_cert_chain(str(CERTS / "client.pem"))
    with socket.create_connection((h, p), timeout=5) as s:
        with ctx.wrap_socket(s, server_hostname=h) as t:
            cert = t.getpeercert()
            return {"protocol": t.version(), "cipher": t.cipher()[0], "bits": t.cipher()[2],
                    "server_subject": ", ".join("=".join(x) for rdn in cert.get("subject", ()) for x in rdn),
                    "server_issuer": ", ".join("=".join(x) for rdn in cert.get("issuer", ()) for x in rdn),
                    "san": [f"{k}:{v}" for k, v in cert.get("subjectAltName", ())], "hostname_checked": h}


def chain_info() -> dict:
    import importlib.util
    spec = importlib.util.spec_from_file_location("lab12_certs", Path(__file__).resolve().parents[2] / "labs/lab12/setup/certs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                          # type: ignore[union-attr]
    return {"certs": [mod.describe(CERTS / f) for f in ("ca.crt", "server.crt", "client.crt", "rogue-ca.crt") if (CERTS / f).exists()],
            "verify": mod.verify_chain(CERTS)}


def tls_tests(log=print) -> dict:
    pw = secrets_()["analyst_app"] if (CERTS / "secrets.json").exists() else ""
    tests = [
        _probe("1. Plain TCP, no TLS  (mongosh --host ... --port ... without --tls)", False, tls=False),
        _probe("2. TLS, but the client does not trust our CA", False, ca=False),
        _probe("3. TLS + CA, but NO client certificate", False, cert=None),
        _probe("4. TLS + CA + a client certificate signed by a rogue CA", False, cert="rogue-client.pem"),
        _probe("5. TLS + CA + client certificate  (tls=true&tlsCAFile&tlsCertificateKeyFile)", True),
    ]
    for t in tests:
        log(f"   [{'PASS' if t['ok'] else 'FAIL'}] {t['test']:<78} -> {t['result']}: {t['detail'][:90]}")
    c = connect("analyst_app", password=pw, timeout=4000)
    t0 = time.perf_counter()
    found = True
    try:
        doc = c[CTI].reports.find_one({}, {"title": 1, "published_at": 1}, sort=[("published_at", -1)]) or {"title": "(no reports yet)"}
    except PyMongoError as exc:
        doc, found = {"title": f"{type(exc).__name__}: {str(exc)[:80]} (run the Kavach part first)"}, False
    find_ms = round(1000 * (time.perf_counter() - t0), 1)
    c.close()
    tests.append({"test": "6. Encrypted connection + analyst_app: find_one() on cti_secure.reports", "result": "connected",
                  "detail": doc.get("title", "ok"), "expected": "connected", "ok": found, "ms": find_ms})
    log(f"   [{'PASS' if found else 'FAIL'}] {tests[-1]['test']} -> {str(tests[-1]['detail'])[:70]}")
    hs = handshake()
    log(f"   handshake: {hs['protocol']}, {hs['cipher']} ({hs['bits']} bit), server certificate {hs['server_subject']}")
    ss = admin().admin.command("serverStatus")
    opts = admin().admin.command("getCmdLineOpts")["parsed"]
    ch = chain_info()
    for v in ch["verify"]:
        log(f"   chain: {v['cert']:<17} issuer = CA: {v['issuer_matches_ca']}, signature by CA: {v['signature_by_ca']}")
    return {"tests": tests, "handshake": hs, "transport_security": ss.get("transportSecurity", {}),
            "server_tls": opts.get("net", {}).get("tls", {}),
            "auth": {"keyFile": "keyFile" in opts.get("security", {}),
                     "mechanisms": (opts.get("setParameter") or {}).get("authenticationMechanisms", "default")},
            "chain": ch, "pass": all(t["ok"] for t in tests) and all(v["trusted"] == (v["cert"] != "rogue-client.pem") for v in ch["verify"])}


# ============================================================================ Lab 12 applied to Lab 7 (Kavach)
KAVACH_ROLES = [
    {"role": "kavachAnalyst", "service": "website (threat feed, vulnerabilities, IP pages) - analyst_app",
     "privileges": [{"resource": {"db": CTI, "collection": c}, "actions": ["find"]}
                    for c in ("reports", "indicators", "vulnerabilities", "threat_actors", "sources")] +
                   [{"resource": {"db": BILL, "collection": "accounts_masked"}, "actions": ["find"]}]},
    {"role": "kavachIngest", "service": "5-minute auto-updater (feeds, GCVE) - ingest_app",
     "privileges": [{"resource": {"db": CTI, "collection": c}, "actions": ["find", "insert", "update"]}
                    for c in ("reports", "indicators", "vulnerabilities")] +
                   [{"resource": {"db": CTI, "collection": "ingest_runs"}, "actions": ["insert", "find"]}]},
    {"role": "kavachFinance", "service": "classifier billing (Lab 10 paywall) - finance_app",
     "privileges": [{"resource": {"db": BILL, "collection": c}, "actions": ["find", "insert", "update"]} for c in ("accounts", "payments")] +
                   [{"resource": {"db": BILL, "collection": "accounts_masked"}, "actions": ["find"]},
                    {"resource": {"db": "encryption", "collection": "__keyVault"}, "actions": ["find"]}]},
]
PRIVATE_NETS = ["127.0.0.1", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
KAVACH_USERS = [
    {"user": "analyst_app", "role": "kavachAnalyst", "service": "website"},
    {"user": "ingest_app", "role": "kavachIngest", "service": "auto-updater"},
    {"user": "finance_app", "role": "kavachFinance", "service": "billing", "clientSource": PRIVATE_NETS},
    {"user": "ip_locked", "role": "kavachAnalyst", "service": "IP allow-list demo", "clientSource": ["10.99.99.99/32"]},
]
UPI_BANKS = ["okaxis", "oksbi", "okhdfcbank", "ybl", "paytm", "ibl"]


def _source_db():
    from .db import get_db
    return get_db()


def _billing_source():
    """Accounts/payments of the Lab 10 paywall wherever they live now (rs0, or the shards after Lab 11)."""
    try:
        from . import billing
        b = billing.backend(refresh=True)
        return b["client"][billing.DB], b["label"]
    except Exception as exc:                              # noqa: BLE001
        return None, f"billing not reachable ({str(exc)[:80]})"


def _copy(src, dst, limit, sort, log, name):
    dst.drop()
    n, batch = 0, []
    cur = src.find({}).sort(sort).limit(limit) if sort else src.find({}).limit(limit)
    for d in cur:
        batch.append(d)
        if len(batch) == 1000:
            dst.insert_many(batch, ordered=False); n += len(batch); batch = []
    if batch:
        dst.insert_many(batch, ordered=False); n += len(batch)
    ids_src = sorted(str(d["_id"]) for d in (src.find({}, {"_id": 1}).sort(sort).limit(limit) if sort else src.find({}, {"_id": 1}).limit(limit)))
    ids_dst = sorted(str(d["_id"]) for d in dst.find({}, {"_id": 1}))
    ok = hashlib.sha256("|".join(ids_src).encode()).hexdigest() == hashlib.sha256("|".join(ids_dst).encode()).hexdigest()
    log(f"   copied {name:<16} {n:>7,} documents  (id checksum {'matches' if ok else 'DIFFERS'})")
    return {"collection": name, "copied": n, "source_total": src.estimated_document_count(), "verified": ok}


def _demo_accounts(rng, n=12):
    now = datetime.now(UTC)
    accs, pays = [], []
    for i in range(n):
        f, l = rng.choice(FIRST), rng.choice(LAST)
        email = f"{f.lower()}.{l.lower()}{i}@example.in"
        paid = rng.random() < 0.6
        accs.append({"_id": email, "name": f"{f} {l}", "wallet": rng.choice([0, 50, 120, 300, 900]), "free_used": rng.randint(0, 3),
                     "uses_total": rng.randint(0, 40), "created_at": now - timedelta(days=rng.randint(1, 120)),
                     "plan": {"expires_at": now + timedelta(days=rng.randint(1, 29))} if paid else None, "demo": True})
        if paid:
            pays.append({"user_id": email, "kind": "subscription", "amount": 100, "idem": f"demo-{i}", "at": now - timedelta(days=3)})
        pays.append({"user_id": email, "kind": "topup", "amount": 200, "idem": f"demo-top-{i}", "at": now - timedelta(days=5),
                     "method": "UPI"})
    return accs, pays


def _csfle(client):
    from pymongo.encryption import ClientEncryption
    from bson.codec_options import CodecOptions
    from bson.binary import STANDARD
    key = (CERTS / "master.key").read_bytes()
    return ClientEncryption({"local": {"key": key}}, KEYVAULT, client, CodecOptions(uuid_representation=STANDARD))


def _data_key(ce, client):
    k = client.encryption["__keyVault"].find_one({"keyAltNames": "kavach-upi"})
    return k["_id"] if k else ce.create_data_key("local", key_alt_names=["kavach-upi"])


def setup_kavach(quick: bool = False, log=print) -> dict:
    """Lab 12 applied to Lab 7: copy the Kavach data to the secure server and lock it down."""
    c = admin()
    adm = c.admin
    src = _source_db()
    lim = {"reports": 3000 if quick else 20000, "indicators": 5000 if quick else 30000, "vulnerabilities": 2000 if quick else 10000,
           "threat_actors": 5000, "sources": 1000}
    copies = []
    # _id as tie-breaker: thousands of indicators share one last_seen (one feed download), so without it the copy and the
    # verification could pick different documents at the limit boundary and the checksum would differ
    for name, sort in (("reports", [("published_at", -1), ("_id", 1)]), ("indicators", [("last_seen", -1), ("_id", 1)]),
                       ("vulnerabilities", None), ("threat_actors", None), ("sources", None)):
        copies.append(_copy(src[name], c[CTI][name], lim[name], sort, log, name))
    c[CTI].reports.create_index([("published_at", DESCENDING)])
    c[CTI].indicators.create_index([("type", ASCENDING), ("value", ASCENDING)])
    c[CTI].drop_collection("ingest_runs")
    c[CTI].create_collection("ingest_runs")

    # integrity: schema validation (applied after the copy; every later insert/update is checked)
    # every IOC type the platform knows (not only the types that happen to be in the copied subset)
    types = sorted({t for t in c[CTI].indicators.distinct("type") if isinstance(t, str)} |
                   {"ipv4", "ipv6", "cidr", "domain", "url", "sha256", "sha1", "md5"})
    c[CTI].command("collMod", "indicators", validator={"$jsonSchema": {
        "bsonType": "object", "required": ["type", "value"],
        "properties": {"type": {"enum": types}, "value": {"bsonType": "string", "minLength": 1, "maxLength": 2048}}}},
        validationLevel="moderate", validationAction="error")
    log(f"   $jsonSchema validator on {CTI}.indicators: type in {types[:6]}{'...' if len(types) > 6 else ''}, value string")

    # billing (Lab 10 paywall) - the sensitive part of the platform
    bsrc, blabel = _billing_source()
    accs, pays = [], []
    if bsrc is not None:
        try:
            accs = list(bsrc.accounts.find({}))
            pays = list(bsrc.payments.find({}))
        except PyMongoError as exc:
            blabel = f"{blabel}: {str(exc)[:80]}"
    real = len(accs)
    rng = random.Random(7)
    if real < 5:
        da, dp = _demo_accounts(rng)
        accs += [a for a in da if a["_id"] not in {x["_id"] for x in accs}]
        pays += dp
    for p in pays:
        p.pop("_id", None)
        if p.get("kind") == "topup" or p.get("purpose") == "topup" or p.get("method") == "UPI":   # billing.py names it "purpose"
            p["upi_vpa"] = f"{str(p['user_id']).split('@')[0][:12]}@{rng.choice(UPI_BANKS)}"
    b = c[BILL]
    for n in ("accounts_masked", "accounts", "payments"):
        b.drop_collection(n)
    b.create_collection("accounts", validator={"$jsonSchema": {"bsonType": "object", "required": ["_id"], "properties": {
        "wallet": {"bsonType": ["int", "long", "double"], "minimum": 0},
        "free_used": {"bsonType": ["int", "long"], "minimum": 0, "maximum": 3}}}})
    b.command("collMod", "accounts", changeStreamPreAndPostImages={"enabled": True})
    if accs:
        b.accounts.insert_many(accs)

    # confidentiality: Client-Side Field Level Encryption of the UPI id (the server only ever sees ciphertext)
    c.encryption.drop_collection("__keyVault")
    c.encryption["__keyVault"].create_index("keyAltNames", unique=True, partialFilterExpression={"keyAltNames": {"$exists": True}})
    ce = _csfle(c)
    kid = _data_key(ce, c)
    enc = 0
    for p in pays:
        if p.get("upi_vpa"):
            p["upi_vpa"] = ce.encrypt(p["upi_vpa"], "AEAD_AES_256_CBC_HMAC_SHA_512-Random", key_id=kid)
            enc += 1
    if pays:
        b.payments.insert_many(pays)
    b.payments.create_index([("user_id", ASCENDING), ("at", DESCENDING)])
    ce.close()
    log(f"   billing: {len(accs)} accounts ({real} real from {blabel}, {len(accs) - real} demo), {len(pays)} payments; "
        f"{enc} UPI ids encrypted client-side (CSFLE, AEAD_AES_256_CBC_HMAC_SHA_512-Random)")

    # field-level redaction for the analysts: allow-list view, e-mail masked, wallet never leaves the server
    b.command("create", "accounts_masked", viewOn="accounts", pipeline=[{"$project": {
        "_id": 0, "customer": {"$concat": ["c-", {"$substrCP": [{"$toString": {"$abs": {"$toHashedIndexKey": "$_id"}}}, 0, 8]}]}, "name": 1,
        "email": {"$concat": [{"$substrCP": ["$_id", 0, 1]}, "***@", {"$arrayElemAt": [{"$split": ["$_id", "@"]}, 1]}]},
        "free_used": 1, "uses_total": 1, "plan_active": {"$gt": ["$plan.expires_at", "$$NOW"]}, "created_at": 1}}])
    log("   view kavach_billing.accounts_masked: allow-list $project, e-mail masked, wallet and payments not visible")

    for r in KAVACH_ROLES:
        _create_role(adm, r["role"], r["privileges"])
        log(f"   createRole {r['role']:<14} for the {r['service']}")
    pw = secrets_()
    for u in KAVACH_USERS:
        extra = {"authenticationRestrictions": [{"clientSource": u["clientSource"]}]} if u.get("clientSource") else {}
        _create_user(adm, u["user"], [{"role": u["role"], "db": "admin"}], pw[u["user"]], **extra)
        log(f"   createUser {u['user']:<12} role {u['role']}" + (f"  clientSource {u['clientSource']}" if extra else ""))

    # audit: the profiler of kavach_billing records every operation with the authenticated user
    b.command("profile", 0)
    try:
        b.drop_collection("system.profile")
        b.command("create", "system.profile", capped=True, size=4 * 1024 * 1024)
    except PyMongoError:
        pass
    b.command("profile", 2)
    log("   audit: profiler level 2 on kavach_billing (who read or changed customer data, with the user name)")
    reset_clients()
    return {"copies": copies, "billing": {"accounts": len(accs), "real_accounts": real, "payments": len(pays),
                                           "encrypted_upi": enc, "source": blabel},
            "validator_types": types, "roles": KAVACH_ROLES, "users": KAVACH_USERS}


# ----------------------------------------------------------------------------- the Kavach permission matrix
def _k(u):
    return as_user(u)


def _kavach_ops():
    return [
        ("read threat feed", lambda u: [d.get("title", "")[:40] for d in _k(u)[CTI].reports.find({}, {"title": 1}).sort("published_at", -1).limit(3)]),
        ("search IOCs", lambda u: _k(u)[CTI].indicators.count_documents({"type": {"$exists": True}}, limit=500)),
        ("masked customers", lambda u: _k(u)[BILL].accounts_masked.find_one()),
        ("read wallets", lambda u: _k(u)[BILL].accounts.find_one({}, {"wallet": 1})),
        ("insert IOC", lambda u: _k(u)[CTI].indicators.insert_one({"type": "domain", "value": f"lab12-{u}.test", "_lab12_test": True}).inserted_id),
        ("update report", lambda u: _k(u)[CTI].reports.update_one({}, {"$set": {"lab12_checked": True}}).matched_count),
        ("delete report", lambda u: _k(u)[CTI].reports.delete_one({"_lab12_test": True, "never": True}).deleted_count),
        ("drop collection", lambda u: _drop_canary(_k(u))),
        ("top up a wallet", lambda u: _k(u)[BILL].accounts.update_one({}, {"$inc": {"wallet": 0}}).matched_count),
        ("createUser", lambda u: _escalate(_k(u))),
        ("read HR salaries", lambda u: _k(u)[COMPANY].employees.find_one({}, {"salary": 1})),
    ]


def _drop_canary(c):
    admin()[CTI].canary.insert_one({"x": 1})
    c[CTI].drop_collection("canary")
    return "dropped (canary collection)"


EXPECT_K = {
    "analyst_app": {"read threat feed", "search IOCs", "masked customers"},
    "ingest_app": {"read threat feed", "search IOCs", "insert IOC", "update report"},
    "finance_app": {"masked customers", "read wallets", "top up a wallet"},
    "sec_admin": {"createUser"},
    "kavach_admin": None,
}


def matrix_kavach(log=print) -> dict:
    log("   columns: " + " | ".join(o[0] for o in _kavach_ops()))
    r = matrix(["kavach_admin", "sec_admin", "analyst_app", "ingest_app", "finance_app"], _kavach_ops(), EXPECT_K, log)
    c = admin()
    c[CTI].indicators.delete_many({"_lab12_test": True})
    c[CTI].reports.update_many({"lab12_checked": True}, {"$unset": {"lab12_checked": ""}})
    c[CTI].drop_collection("canary")
    return r


def integrity(log=print) -> dict:
    """Schema validation stops bad data even from users who ARE allowed to write."""
    ing, fin = as_user("ingest_app"), as_user("finance_app")
    out = {
        "bad_indicator_type": attempt(lambda: ing[CTI].indicators.insert_one({"type": "pwned", "value": "x", "_lab12_test": True})),
        "indicator_without_value": attempt(lambda: ing[CTI].indicators.insert_one({"type": "domain", "_lab12_test": True})),
        "good_indicator": attempt(lambda: ing[CTI].indicators.insert_one({"type": admin()[CTI].indicators.find_one({}, {"type": 1}).get("type", "domain"),
                                                                           "value": "integrity-check.example", "_lab12_test": True}).inserted_id),
        "negative_wallet": attempt(lambda: fin[BILL].accounts.update_one({}, {"$set": {"wallet": -500}})),
        "fourth_free_trial": attempt(lambda: fin[BILL].accounts.update_one({}, {"$set": {"free_used": 4}})),
    }
    admin()[CTI].indicators.delete_many({"_lab12_test": True})
    out["pass"] = (out["bad_indicator_type"]["result"] == "rejected" and out["indicator_without_value"]["result"] == "rejected"
                   and out["good_indicator"]["result"] == "allowed" and out["negative_wallet"]["result"] == "rejected"
                   and out["fourth_free_trial"]["result"] == "rejected")
    for k, v in out.items():
        if isinstance(v, dict):
            log(f"   {k:<24} -> {v['result']}" + (f" (code {v.get('code')} {v.get('codeName', '')})" if v.get("code") else ""))
    return out


def ip_allowlist(log=print) -> dict:
    pw = secrets_()
    locked = attempt(lambda: connect("ip_locked", password=pw["ip_locked"], timeout=3000).admin.command("connectionStatus"))
    fin = attempt(lambda: as_user("finance_app").admin.command("connectionStatus")["authInfo"]["authenticatedUsers"])
    weak = [{"user": u, "password": p, **attempt(lambda u=u, p=p: connect(u, password=p, timeout=3000).admin.command("ping"))}
            for u, p in (("admin", "admin"), ("root", "password"), ("kavach_admin", "kavach123"))]
    sha1 = attempt(lambda: connect("analyst_app", mech="SCRAM-SHA-1", timeout=3000).admin.command("ping"))
    anon = attempt(lambda: connect(None, timeout=3000).list_database_names())
    out = {"ip_locked": locked, "finance_from_private_net": fin, "weak_passwords": weak, "scram_sha1": sha1, "anonymous": anon,
           "pass": locked["result"] == "auth-failed" and fin["result"] == "allowed" and all(w["result"] == "auth-failed" for w in weak)
           and sha1["result"] == "auth-failed" and anon["result"] == "denied"}
    log(f"   ip_locked (clientSource 10.99.99.99/32) from this container -> {locked['result']}")
    log(f"   finance_app (clientSource private networks) -> {fin['result']}")
    log("   default passwords " + ", ".join(f"{w['user']}/{w['password']} -> {w['result']}" for w in weak))
    log(f"   SCRAM-SHA-1 log-in -> {sha1['result']}   |   no credentials: listDatabases -> {anon['result']}")
    return out


def csfle_demo(log=print) -> dict:
    """What an over-privileged insider (root) sees vs what the billing service with the key sees."""
    c = admin()
    raw = c[BILL].payments.find_one({"upi_vpa": {"$exists": True}})
    if not raw:
        return {"available": False}
    ce = _csfle(as_user("finance_app"))
    dec = as_user("finance_app")[BILL].payments.find_one({"_id": raw["_id"]})
    plain = ce.decrypt(dec["upi_vpa"])
    ce.close()
    v = raw["upi_vpa"]
    out = {"available": True, "payment_id": str(raw["_id"]), "root_sees": f"Binary(subtype {v.subtype}, {len(v)} bytes): "
           + bytes(v)[:24].hex() + "...", "finance_decrypts": plain, "algorithm": "AEAD_AES_256_CBC_HMAC_SHA_512-Random",
           "query_by_plaintext": c[BILL].payments.count_documents({"upi_vpa": plain}),
           "key_vault": KEYVAULT, "pass": isinstance(v, Binary) and v.subtype == 6 and "@" in plain}
    log(f"   kavach_admin (root) reads payments.upi_vpa -> {out['root_sees'][:70]}")
    log(f"   finance_app with the master key decrypts  -> {plain}")
    log(f"   search by the plaintext on the server finds {out['query_by_plaintext']} documents (randomised encryption)")
    return out


def audit(log=print) -> dict:
    """Integrity + accountability: change stream with pre-images (what changed) + profiler (who did what)."""
    c = admin()
    acc = c[BILL].accounts.find_one({}, {"_id": 1, "wallet": 1})
    if not acc:
        return {"available": False}
    fin = as_user("finance_app")[BILL]
    event = None
    with c[BILL].accounts.watch(full_document="updateLookup", full_document_before_change="whenAvailable",
                                max_await_time_ms=2000) as stream:
        fin.accounts.update_one({"_id": acc["_id"]}, {"$inc": {"wallet": 25}})
        t0 = time.time()
        while time.time() - t0 < 5 and event is None:
            event = stream.try_next()
    fin.accounts.update_one({"_id": acc["_id"]}, {"$inc": {"wallet": -25}})
    list(as_user("analyst_app")[BILL].accounts_masked.find().limit(2))
    attempt(lambda: as_user("analyst_app")[BILL].accounts.find_one())
    prof = list(c[BILL]["system.profile"].find({"ns": {"$regex": f"^{BILL}\\.(accounts|payments)"}},
                                              {"ts": 1, "op": 1, "ns": 1, "user": 1, "command": 1, "nreturned": 1, "nModified": 1})
                .sort("ts", -1).limit(40))
    rows = [{"at": p.get("ts"), "user": p.get("user", "-"), "op": p.get("op"), "ns": p.get("ns"),
             "detail": json.dumps({k: v for k, v in (p.get("command") or {}).items() if k in ("filter", "u", "q", "updates", "pipeline")},
                                  default=str)[:120], "n": p.get("nreturned", p.get("nModified"))} for p in prof]
    by_user = {}
    for r in rows:
        by_user[r["user"]] = by_user.get(r["user"], 0) + 1
    ev = None
    if event:
        ev = {"operation": event["operationType"], "document": str(event["documentKey"]["_id"]),
              "wallet_before": (event.get("fullDocumentBeforeChange") or {}).get("wallet"),
              "wallet_after": (event.get("fullDocument") or {}).get("wallet"),
              "change": event.get("updateDescription", {}).get("updatedFields"), "cluster_time": str(event.get("clusterTime"))}
    log(f"   change stream on accounts: {ev['operation'] if ev else 'no event'} wallet {ev and ev['wallet_before']} -> {ev and ev['wallet_after']}")
    log("   profiler entries by user: " + ", ".join(f"{k} {v}" for k, v in by_user.items()))
    return {"available": True, "change_event": ev, "profile": _j(rows), "by_user": by_user,
            "pass": bool(ev and ev["wallet_before"] is not None and ev["wallet_after"] == ev["wallet_before"] + 25)
            and any(r["user"].startswith("finance_app") for r in rows)}


def sync_secure(src_db, limit: int = 300, log=None) -> dict:
    """Called by the Lab 7 auto-updater after every 5-minute cycle once Lab 12 is set up: the newest reports and
    indicators are pushed to the secure server AS ingest_app (TLS + least privilege, no delete/drop), and a
    heartbeat goes to cti_secure.ingest_runs. Indicators the schema validator refuses are counted, not copied."""
    if not (CERTS / "secrets.json").exists():
        return {"skipped": "Lab 12 not set up"}
    c = as_user("ingest_app")[CTI]
    t0 = time.perf_counter()
    rep = ind = bad = 0
    for d in src_db.reports.find({}).sort("published_at", -1).limit(limit):
        c.reports.replace_one({"_id": d["_id"]}, d, upsert=True)
        rep += 1
    for d in src_db.indicators.find({}).sort("last_seen", -1).limit(limit):
        try:
            c.indicators.replace_one({"_id": d["_id"]}, d, upsert=True)
            ind += 1
        except OperationFailure as exc:
            if exc.code != 121:
                raise
            bad += 1
    hb = {"kind": "secure_sync", "at": datetime.now(UTC), "by": "ingest_app", "reports": rep, "indicators": ind,
          "rejected_by_validator": bad, "seconds": round(time.perf_counter() - t0, 2)}
    c.ingest_runs.insert_one(hb)
    if log:
        log(f"secure sync as ingest_app over TLS: {rep} reports, {ind} indicators, {bad} rejected by the validator")
    return _j(hb)


def analyst_feed(n: int = 8) -> dict:
    """What the Kavach website shows when it reads through the least-privilege analyst account over TLS."""
    c = as_user("analyst_app")
    t0 = time.perf_counter()
    reps = list(c[CTI].reports.find({}, {"title": 1, "published_at": 1, "classification.verdict": 1, "classification.category": 1,
                                         "classification.severity": 1, "url": 1}).sort("published_at", -1).limit(n))
    ms = round(1000 * (time.perf_counter() - t0), 1)
    cust = list(c[BILL].accounts_masked.find().limit(6))
    who = c.admin.command("connectionStatus")["authInfo"]
    return _j({"reports": reps, "customers": cust, "ms": ms, "as": who["authenticatedUsers"], "roles": who["authenticatedUserRoles"]})


PERSONAS = ["analyst_app", "ingest_app", "finance_app", "sec_admin", "kavach_admin", "staff_user", "hr_user", "app_user_read",
            "app_user_write", "analytics_app"]


def try_action(persona: str, action: str) -> dict:
    ops = dict(_kavach_ops() + _ops_121())
    if persona not in PERSONAS or action not in ops:
        raise ValueError("unknown persona or action")
    out = attempt(lambda: ops[action](persona))
    c = admin()
    c[CTI].indicators.delete_many({"_lab12_test": True})
    c[CTI].reports.update_many({"lab12_checked": True}, {"$unset": {"lab12_checked": ""}})
    c[CTI].drop_collection("canary")
    c[COMPANY].orders.delete_many({"_lab12_test": True})
    c[ANALYTICS].events.delete_many({"_lab12_test": True})
    return {"persona": persona, "action": action, **out}


# ============================================================================ status + threat model
def status() -> dict:
    av = available()
    if not av["available"]:
        return av
    c = admin()
    hello = c.admin.command("hello")
    opts = c.admin.command("getCmdLineOpts")["parsed"]
    ss = c.admin.command("serverStatus")
    users = c.admin.command("usersInfo", showCredentials=False)["users"]
    roles = c.admin.command("rolesInfo", 1, showPrivileges=True)["roles"]
    dbs = {}
    for d in (COMPANY, ANALYTICS, CTI, BILL):
        try:
            dbs[d] = {n: c[d][n].estimated_document_count() for n in c[d].list_collection_names(filter={"type": "collection"})
                      if not n.startswith("system.")}
        except PyMongoError:
            dbs[d] = {}
    return _j({"available": True, "host": SECURE_HOST, "version": c.server_info()["version"], "set": hello.get("setName"),
               "tls_mode": opts.get("net", {}).get("tls", {}).get("mode"), "client_certs_required": "CAFile" in opts.get("net", {}).get("tls", {})
               and not opts.get("net", {}).get("tls", {}).get("allowConnectionsWithoutCertificates", False),
               "auth": bool(opts.get("security", {}).get("keyFile") or opts.get("security", {}).get("authorization") == "enabled"),
               "mechanisms": (opts.get("setParameter") or {}).get("authenticationMechanisms", "default"),
               "transport_security": ss.get("transportSecurity", {}), "connections": ss.get("connections", {}),
               "users": [{"user": u["user"], "roles": [f"{r['role']}@{r['db']}" for r in u["roles"]], "mechanisms": u.get("mechanisms"),
                          "restrictions": u.get("authenticationRestrictions", [])} for u in users],
               "roles": [{"role": r["role"], "privileges": [{"ns": f"{p['resource'].get('db', '')}.{p['resource'].get('collection', '') or '*'}",
                                                             "actions": p["actions"]} for p in r.get("privileges", [])]} for r in roles],
               "databases": dbs, "uptime": ss.get("uptime")})


THREATS = [
    {"id": "unauth", "threat": "Unauthenticated access", "cause": "No auth enabled", "control": "SCRAM-SHA authentication",
     "cia": "Confidentiality"},
    {"id": "weak", "threat": "Weak credentials", "cause": "Default password", "control": "Role-Based Access Control + strong passwords",
     "cia": "Confidentiality"},
    {"id": "sniff", "threat": "Network eavesdropping", "cause": "No TLS", "control": "TLS/SSL encryption", "cia": "Confidentiality"},
    {"id": "insider", "threat": "Insider threat", "cause": "Over-privileged user", "control": "Audit logging + least privilege",
     "cia": "Confidentiality / Integrity"},
]
CONTROLS = [
    ("scram", "SCRAM-SHA authentication", "Confidentiality"), ("rbac", "Role-Based Access Control", "Confidentiality"),
    ("tls", "TLS/SSL encryption", "Confidentiality"), ("audit", "Audit logging", "Integrity"),
    ("fle", "Encryption at rest (field level)", "Confidentiality"), ("ip", "IP whitelisting", "Confidentiality"),
    ("schema", "Schema validation", "Integrity"), ("streams", "Change streams auditing", "Integrity"),
    ("redact", "Field-level redaction (views)", "Confidentiality"), ("avail", "Replica sets + failover", "Availability"),
]


def threat_model(res: dict) -> dict:
    """The mentor's 'Threat Modelling' picture with the measured evidence for every control."""
    t, k = res.get("lab_12_2") or {}, res.get("kavach") or {}
    ipw, integ, fle, aud = k.get("ip_weak") or {}, k.get("integrity") or {}, k.get("csfle") or {}, k.get("audit") or {}
    m121, red, mk = res.get("lab_12_1", {}).get("matrix") or {}, res.get("redaction") or {}, k.get("matrix") or {}
    tests = {x["test"][:2]: x for x in t.get("tests", [])}
    ev = {
        "scram": (ipw.get("anonymous", {}).get("result") == "denied" and ipw.get("scram_sha1", {}).get("result") == "auth-failed",
                  "no credentials -> listDatabases not authorized; only SCRAM-SHA-256 accepted (SHA-1 refused)"),
        "rbac": (bool(m121.get("pass")) and bool(mk.get("pass")), "every user x operation matched least privilege (12.1 and Kavach matrices)"),
        "tls": (bool(t.get("pass")), f"plain/untrusted/no-cert/rogue refused; {t.get('handshake', {}).get('protocol', '?')} "
                                     f"{t.get('handshake', {}).get('cipher', '')}"),
        "audit": (any(str(u).startswith("finance_app") for u in (aud.get("by_user") or {})),
                  "profiler on kavach_billing records the user of every read and write"),
        "fle": (bool(fle.get("pass")), "UPI ids are CSFLE ciphertext on the server; even root reads Binary subtype 6"),
        "ip": (ipw.get("ip_locked", {}).get("result") == "auth-failed", "user with clientSource 10.99.99.99/32 cannot log in from here"),
        "schema": (bool(integ.get("pass")), "bad IOC type, missing value, negative wallet, 4th free trial -> DocumentValidationFailure"),
        "streams": (bool(aud.get("pass")), "change stream with pre-image shows wallet before and after each change"),
        "redact": (bool(red.get("pass")), "staff see employees_public without salary; analysts see masked customers"),
        "avail": (None, "rs0 (3 members, Lab 8 failover) serves Kavach; rs12 here is one member for the security lab"),
    }
    controls = [{"id": cid, "control": name, "cia": cia, "ok": ev[cid][0], "evidence": ev[cid][1]} for cid, name, cia in CONTROLS]
    threats = [{**th, "mitigated": {"unauth": ev["scram"][0], "weak": ev["rbac"][0] and bool(ipw.get("weak_passwords"))
                                    and all(w["result"] == "auth-failed" for w in ipw.get("weak_passwords", [])),
                                    "sniff": ev["tls"][0], "insider": ev["audit"][0] and ev["fle"][0] and ev["redact"][0]}[th["id"]]}
               for th in THREATS]
    return {"threats": threats, "controls": controls,
            "cia": {"Confidentiality": ["Encryption at rest/transit", "Field-level encryption", "RBAC"],
                    "Integrity": ["Schema validation", "Change streams auditing", "Digital signatures (TLS certificates)"],
                    "Availability": ["Replica sets", "Automatic failover", "Rate limiting"]}}

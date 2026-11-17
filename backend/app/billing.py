"""Classifier billing - Lab 10 (ACID transactions) applied to the Lab 7 classifier, sharded by Lab 11.

Business rule (mentor's update): every user gets 3 free classifications; after that the classifier costs
Rs 100 per month (30 days). Money moves through a wallet that the user tops up (simulated UPI payment).

Every state change is ONE multi-document ACID transaction (lab10.run_txn: snapshot read, majority write,
automatic retry of write conflicts):

    consume()    read account -> free trial left? (conditional $inc) or active plan? -> run the classifier
                 -> insert usage record -> commit.  The use is only counted if the classification succeeded.
    subscribe()  wallet >= 100? -> debit wallet -> extend plan by 30 days -> insert payment (unique per
                 user + idempotency key) -> credit platform revenue -> commit.  All or nothing.
    topup()      insert payment record -> credit wallet -> commit.

Where the data lives (Lab 10 -> Lab 11):
    replica set rs0 (Lab 10)     database cti_billing on mongo1/2/3
    sharded cluster (Lab 11)     database cti_billing on mongos, after `scale_out()` copied the data:
        accounts  {_id: 'hashed'}                  one user = one shard
        usage     {user_id: 'hashed', at: 1}       the compound key Lab 11.1 recommended as refinement
        payments  {user_id: 'hashed'}              unique {user_id, idem}: a unique index on idem alone is
                                                   REFUSED on a sharded collection (observed on 7.0)
        revenue   {_id: 'hashed'}                  8 bucket documents instead of one hot counter
    A marker document cti_billing.meta {_id: 'backend'} on rs0 says which one is live.
"""
from __future__ import annotations

import hashlib
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure, PyMongoError

from .lab10 import StaleVersion, run_txn

UTC = timezone.utc
DB = "cti_billing"
FREE_TRIALS = 3
PRICE = 100                 # rupees per period
PERIOD_DAYS = 30
BUCKETS = 8                 # revenue counter buckets (avoids one hot document)
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,24}$", re.I)


class BillingError(Exception):
    status = 400


class NotSignedIn(BillingError):
    status = 401


class PaymentRequired(BillingError):
    status = 402


class InsufficientFunds(BillingError):
    status = 402


class SimulatedCrash(BillingError):
    status = 500


class Migrating(BillingError):
    status = 503


# ============================================================================ where the data lives
_marker = {"at": 0.0, "value": None}


def _rs0() -> MongoClient:
    from .db import get_db
    return get_db().client


def backend(refresh: bool = False) -> dict:
    """{'name': 'replica_set'|'sharded'|'migrating', 'label', 'client'} - marker cached for 3 s."""
    now = time.time()
    if refresh or now - _marker["at"] > 3:
        doc = None
        try:
            doc = _rs0()[DB].meta.find_one({"_id": "backend"})
        except PyMongoError:
            pass
        _marker.update({"at": now, "value": doc or {"value": "replica_set"}})
    name = _marker["value"].get("value", "replica_set")
    if name == "sharded":
        from . import lab11
        return {"name": name, "label": "sharded cluster (Lab 11)", "client": lab11.client(),
                "since": _marker["value"].get("at")}
    return {"name": name, "label": "replica set rs0 (Lab 10)" if name == "replica_set" else "moving to the sharded cluster",
            "client": _rs0(), "since": _marker["value"].get("at")}


def _db(client: MongoClient | None = None):
    b = backend()
    if b["name"] == "migrating" and client is None:
        raise Migrating("Billing data is being moved to the sharded cluster (Lab 11) - try again in a few seconds.")
    return (client or b["client"])[DB]


def ensure_schema(client: MongoClient, sharded: bool = False, log=print) -> None:
    """Collections and indexes (and shard keys on the cluster). Safe to call repeatedly."""
    db = client[DB]
    have = set(db.list_collection_names())
    for c in ("accounts", "usage", "payments", "revenue"):
        if c not in have:
            db.create_collection(c)          # created up front: transactions should not create collections
    db.usage.create_index([("user_id", ASCENDING), ("at", DESCENDING)])
    db.payments.create_index([("user_id", ASCENDING), ("idem", ASCENDING)], unique=True, name="user_idem_unique")
    db.payments.create_index([("user_id", ASCENDING), ("at", DESCENDING)])
    if sharded:
        keys = {"accounts": {"_id": "hashed"}, "usage": {"user_id": "hashed", "at": 1},
                "payments": {"user_id": "hashed"}, "revenue": {"_id": "hashed"}}
        try:
            client.admin.command("enableSharding", DB)
        except OperationFailure:
            pass
        sharded_now = {c["_id"] for c in client.config.collections.find({"_id": {"$regex": f"^{DB}\\."}})}
        for c, key in keys.items():
            if f"{DB}.{c}" not in sharded_now:
                client.admin.command("shardCollection", f"{DB}.{c}", key=key)
                log(f"   sh.shardCollection('{DB}.{c}', {key})")
    for i in range(BUCKETS):
        db.revenue.update_one({"_id": f"rev-{i}"}, {"$setOnInsert": {"amount": 0, "payments": 0}}, upsert=True)


def bucket_of(user_id: str) -> str:
    return f"rev-{int(hashlib.md5(user_id.encode()).hexdigest(), 16) % BUCKETS}"


# ============================================================================ accounts
def normalise(email: str) -> str:
    e = (email or "").strip().lower()
    if not EMAIL.match(e):
        raise BillingError("Enter a valid e-mail address to sign in.")
    return e


def public(acc: dict | None) -> dict | None:
    if not acc:
        return None
    now = datetime.now(UTC)
    exp = (acc.get("plan") or {}).get("expires_at")
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    active = bool(exp and exp > now)
    return {"user_id": acc["_id"], "name": acc.get("name"), "wallet": acc.get("wallet", 0),
            "free_used": acc.get("free_used", 0), "free_left": max(0, FREE_TRIALS - acc.get("free_used", 0)),
            "free_limit": FREE_TRIALS, "uses_total": acc.get("uses_total", 0),
            "plan": {"active": active, "expires_at": exp, "price": PRICE, "period_days": PERIOD_DAYS},
            "can_classify": active or acc.get("free_used", 0) < FREE_TRIALS, "created_at": acc.get("created_at")}


def sign_in(email: str, name: str = "", client: MongoClient | None = None, synthetic: bool = False) -> dict:
    """Create the account on first sign-in (single-document upsert: already atomic, no transaction needed)."""
    uid = normalise(email)
    db = _db(client)
    now = datetime.now(UTC)
    db.accounts.update_one({"_id": uid}, {"$setOnInsert": {"name": (name or uid.split("@")[0])[:60], "wallet": 0,
                                                            "free_used": 0, "uses_total": 0, "plan": {},
                                                            "created_at": now, "version": 0, "synthetic": synthetic}},
                           upsert=True)
    return public(db.accounts.find_one({"_id": uid}))


def account(user_id: str, client: MongoClient | None = None) -> dict | None:
    return public(_db(client).accounts.find_one({"_id": user_id}))


def history(user_id: str, client: MongoClient | None = None) -> dict:
    db = _db(client)
    return {"usage": list(db.usage.find({"user_id": user_id}, {"_id": 0}).sort("at", -1).limit(15)),
            "payments": list(db.payments.find({"user_id": user_id}, {"_id": 0}).sort("at", -1).limit(10))}


# ============================================================================ the three transactions
def consume(user_id: str, work, *, client: MongoClient | None = None, note: str = "") -> dict:
    """One classifier use. `work()` runs INSIDE the transaction: if it fails, the use is not counted."""
    c = client or backend()["client"]
    if client is None:
        _db()
    db = c[DB]

    def body(s):
        acc = db.accounts.find_one({"_id": user_id}, session=s)
        if not acc:
            raise NotSignedIn("Sign in to use the classifier (3 free classifications, then Rs 100 per month).")
        p = public(acc)
        if p["plan"]["active"]:
            kind, trial = "subscription", None
            db.accounts.update_one({"_id": user_id}, {"$inc": {"uses_total": 1, "version": 1}}, session=s)
        elif acc.get("free_used", 0) < FREE_TRIALS:
            r = db.accounts.update_one({"_id": user_id, "free_used": acc.get("free_used", 0)},
                                       {"$inc": {"free_used": 1, "uses_total": 1, "version": 1}}, session=s)
            if r.modified_count != 1:                      # someone else used a trial between read and write
                raise StaleVersion()
            kind, trial = "free", acc.get("free_used", 0) + 1
        else:
            raise PaymentRequired(f"All {FREE_TRIALS} free classifications are used. Subscribe for Rs {PRICE} per "
                                  f"month to continue.")
        result = work()
        db.usage.insert_one({"user_id": user_id, "at": datetime.now(UTC), "kind": kind, "trial": trial,
                             "verdict": (result or {}).get("verdict"), "category": (result or {}).get("category"),
                             "severity": (result or {}).get("severity"), "note": note[:80]}, session=s)
        return {"kind": kind, "trial": trial, "result": result}

    out = run_txn(c, body, tag="consume")
    acc = public(db.accounts.find_one({"_id": user_id}))
    return {**out["value"], "account": acc, "attempts": out["attempts"], "conflicts": out["conflicts"]}


def subscribe(user_id: str, idem: str, *, client: MongoClient | None = None, crash_after_debit: bool = False) -> dict:
    """Rs 100 for 30 days. Same idem twice (double click, retry after a timeout) charges only once."""
    c = client or backend()["client"]
    if client is None:
        _db()
    db = c[DB]
    idem = str(idem or "")[:80] or f"auto-{time.time_ns()}"

    def body(s):
        acc = db.accounts.find_one({"_id": user_id}, session=s)
        if not acc:
            raise NotSignedIn("Sign in first.")
        if acc.get("wallet", 0) < PRICE:
            raise InsufficientFunds(f"Wallet balance Rs {acc.get('wallet', 0)} is less than Rs {PRICE}. Add money first.")
        now = datetime.now(UTC)
        exp = (acc.get("plan") or {}).get("expires_at")
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        start = exp if exp and exp > now else now
        end = start + timedelta(days=PERIOD_DAYS)
        r = db.accounts.update_one({"_id": user_id, "wallet": {"$gte": PRICE}},
                                   {"$inc": {"wallet": -PRICE, "version": 1},
                                    "$set": {"plan": {"name": "monthly", "started_at": start, "expires_at": end}}},
                                   session=s)
        if r.modified_count != 1:
            raise StaleVersion()
        if crash_after_debit:
            raise SimulatedCrash("Simulated crash after the wallet was debited - the transaction is aborted.")
        db.payments.insert_one({"user_id": user_id, "idem": idem, "purpose": "subscription", "amount": PRICE,
                                "currency": "INR", "period": {"from": start, "to": end}, "at": now,
                                "status": "captured"}, session=s)
        db.revenue.update_one({"_id": bucket_of(user_id)}, {"$inc": {"amount": PRICE, "payments": 1}}, session=s)
        return {"charged": PRICE, "period": {"from": start, "to": end}}

    try:
        out = run_txn(c, body, tag="subscribe")
        return {**out["value"], "duplicate": False, "account": public(db.accounts.find_one({"_id": user_id})),
                "attempts": out["attempts"], "conflicts": out["conflicts"]}
    except (DuplicateKeyError, OperationFailure) as exc:
        if isinstance(exc, DuplicateKeyError) or getattr(exc, "code", None) == 11000:
            prev = db.payments.find_one({"user_id": user_id, "idem": idem}, {"_id": 0})
            return {"charged": 0, "duplicate": True, "payment": prev,
                    "account": public(db.accounts.find_one({"_id": user_id}))}
        raise


def topup(user_id: str, amount: int, idem: str, *, client: MongoClient | None = None) -> dict:
    c = client or backend()["client"]
    if client is None:
        _db()
    db = c[DB]
    amount = int(amount)
    if not 1 <= amount <= 10000:
        raise BillingError("Top-up amount must be between Rs 1 and Rs 10,000.")
    idem = str(idem or "")[:80] or f"auto-{time.time_ns()}"

    def body(s):
        if not db.accounts.find_one({"_id": user_id}, {"_id": 1}, session=s):
            raise NotSignedIn("Sign in first.")
        db.payments.insert_one({"user_id": user_id, "idem": idem, "purpose": "topup", "amount": amount,
                                "currency": "INR", "method": "UPI (simulated)", "at": datetime.now(UTC),
                                "status": "captured"}, session=s)
        db.accounts.update_one({"_id": user_id}, {"$inc": {"wallet": amount, "version": 1}}, session=s)
        return {"credited": amount}

    try:
        out = run_txn(c, body, tag="topup")
        return {**out["value"], "duplicate": False, "account": public(db.accounts.find_one({"_id": user_id}))}
    except (DuplicateKeyError, OperationFailure) as exc:
        if isinstance(exc, DuplicateKeyError) or getattr(exc, "code", None) == 11000:
            return {"credited": 0, "duplicate": True, "account": public(db.accounts.find_one({"_id": user_id}))}
        raise


# ============================================================================ checks used by Lab 10 and Lab 11
def invariants(client: MongoClient) -> dict:
    """Money is never created or lost: top-ups - subscriptions == wallets, and revenue == subscriptions."""
    db = client[DB]
    agg = {d["_id"]: d["s"] for d in db.payments.aggregate([{"$group": {"_id": "$purpose", "s": {"$sum": "$amount"}}}])}
    wallets = next(db.accounts.aggregate([{"$group": {"_id": None, "s": {"$sum": "$wallet"}}}]), {"s": 0})["s"]
    revenue = next(db.revenue.aggregate([{"$group": {"_id": None, "s": {"$sum": "$amount"}}}]), {"s": 0})["s"]
    top, sub = agg.get("topup", 0), agg.get("subscription", 0)
    return {"topups": top, "subscriptions": sub, "wallets": wallets, "revenue": revenue,
            "money_conserved": top - sub == wallets, "revenue_matches": revenue == sub}


def _fake_work(ms: float = 5):
    time.sleep(ms / 1000)
    return {"verdict": "Potential threat", "category": "Malware", "severity": "medium"}


def acid_checks(client: MongoClient, *, threads: int = 10, log=print) -> dict:
    """Lab 10 applied to the classifier: the four ACID properties, each shown with a real experiment."""
    tag = f"{int(time.time())}{random.randint(100, 999)}"
    out = {}

    # A + I: 10 classifications at the same moment by a brand-new user -> exactly 3 free, 7 refused
    u = sign_in(f"race-{tag}@lab.test", "Race test", client=client, synthetic=True)["user_id"]
    res, lock = [], threading.Lock()
    barrier = threading.Barrier(threads)

    def one(i):
        barrier.wait()
        try:
            r = consume(u, lambda: _fake_work(20), client=client, note=f"race {i}")
            with lock:
                res.append(("ok", r["kind"], r["attempts"]))
        except BillingError as exc:
            with lock:
                res.append(("refused", type(exc).__name__, 1))

    ths = [threading.Thread(target=one, args=(i,)) for i in range(threads)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    acc = account(u, client)
    ok = sum(1 for r in res if r[0] == "ok")
    out["free_trial_race"] = {"user": u, "requests": threads, "granted": ok,
                              "refused": sum(1 for r in res if r[0] == "refused"),
                              "retries": sum(r[2] - 1 for r in res), "free_used": acc["free_used"],
                              "usage_records": client[DB].usage.count_documents({"user_id": u}),
                              "pass": ok == FREE_TRIALS and acc["free_used"] == FREE_TRIALS}
    log(f"[A+I] {threads} simultaneous classifications by a new user: {ok} granted, "
        f"{out['free_trial_race']['refused']} refused (PaymentRequired), {out['free_trial_race']['retries']} write-conflict "
        f"retries, free_used = {acc['free_used']}, usage records = {out['free_trial_race']['usage_records']}")

    # A: crash after the wallet was debited -> nothing changed
    u2 = sign_in(f"crash-{tag}@lab.test", "Crash test", client=client, synthetic=True)["user_id"]
    topup(u2, PRICE, "seed", client=client)
    before = account(u2, client)
    try:
        subscribe(u2, "crash-1", client=client, crash_after_debit=True)
        crashed = False
    except SimulatedCrash:
        crashed = True
    after = account(u2, client)
    pays = client[DB].payments.count_documents({"user_id": u2, "purpose": "subscription"})
    out["crash_after_debit"] = {"user": u2, "crashed": crashed, "wallet_before": before["wallet"],
                                "wallet_after": after["wallet"], "plan_active_after": after["plan"]["active"],
                                "subscription_payments": pays,
                                "pass": crashed and after["wallet"] == before["wallet"] and not after["plan"]["active"] and pays == 0}
    log(f"[A]   crash after debit: wallet Rs {before['wallet']} -> Rs {after['wallet']}, plan active "
        f"{after['plan']['active']}, payments {pays}  (rolled back)")

    # C: double click on Pay (same idempotency key, two threads at once) -> charged once
    u3 = sign_in(f"double-{tag}@lab.test", "Double click", client=client, synthetic=True)["user_id"]
    topup(u3, 250, "seed", client=client)
    rr = []
    b2 = threading.Barrier(2)

    def pay():
        b2.wait()
        try:
            rr.append(subscribe(u3, "pay-button-1", client=client))
        except BillingError as exc:
            rr.append({"error": str(exc)})

    t1, t2 = threading.Thread(target=pay), threading.Thread(target=pay)
    t1.start(); t2.start(); t1.join(); t2.join()
    a3 = account(u3, client)
    n3 = client[DB].payments.count_documents({"user_id": u3, "purpose": "subscription"})
    out["double_click"] = {"user": u3, "wallet_before": 250, "wallet_after": a3["wallet"], "subscription_payments": n3,
                           "responses": [("duplicate" if r.get("duplicate") else "charged" if r.get("charged") else "error")
                                         for r in rr],
                           "pass": n3 == 1 and a3["wallet"] == 250 - PRICE and a3["plan"]["active"]}
    log(f"[C]   double click on Pay (same idempotency key): {out['double_click']['responses']}, wallet Rs 250 -> "
        f"Rs {a3['wallet']}, subscription payments {n3}")

    # after paying, classifications continue under the plan
    sub_uses = [consume(u3, _fake_work, client=client)["kind"] for _ in range(3)]
    out["after_subscribing"] = {"kinds": sub_uses, "pass": all(k == "subscription" for k in sub_uses)}

    # D: commits use write concern "majority"; with writeConcernMajorityJournalDefault they are also journaled
    jd = None
    try:
        jd = client.admin.command("replSetGetConfig")["config"].get("writeConcernMajorityJournalDefault", True)
    except PyMongoError:
        try:
            from . import lab11
            hosts = lab11.shard_specs()[0][1]
            jd = lab11.direct(lab11._primary_of(hosts)).admin.command("replSetGetConfig")["config"].get(
                "writeConcernMajorityJournalDefault", True)
        except Exception:                              # noqa: BLE001
            jd = None
    out["durability"] = {"write_concern": "majority", "journal_default": jd, "pass": jd is not False}
    log(f"[D]   every commit uses writeConcern majority; writeConcernMajorityJournalDefault = {jd} "
        f"(a commit is on disk on a majority before the user sees it)")
    out["invariants"] = invariants(client)
    log(f"[C]   invariants: top-ups Rs {out['invariants']['topups']} - subscriptions Rs {out['invariants']['subscriptions']} "
        f"= wallets Rs {out['invariants']['wallets']} -> {out['invariants']['money_conserved']}; revenue = subscriptions -> "
        f"{out['invariants']['revenue_matches']}")
    out["pass"] = all(v.get("pass", True) for v in out.values() if isinstance(v, dict)) and \
        out["invariants"]["money_conserved"] and out["invariants"]["revenue_matches"]
    return out


def summary(client: MongoClient | None = None) -> dict:
    b = backend()
    c = client or b["client"]
    db = c[DB]
    now = datetime.now(UTC)
    try:
        inv = invariants(c)
        return {"backend": b["name"], "backend_label": b["label"], "since": b.get("since"),
                "accounts": db.accounts.estimated_document_count(),
                "real_accounts": db.accounts.count_documents({"synthetic": {"$ne": True}}),
                "subscribers": db.accounts.count_documents({"plan.expires_at": {"$gt": now}}),
                "uses": db.usage.estimated_document_count(), "invariants": inv,
                "price": PRICE, "free_trials": FREE_TRIALS, "period_days": PERIOD_DAYS}
    except PyMongoError as exc:
        return {"backend": b["name"], "backend_label": b["label"], "error": str(exc)[:200]}


# ============================================================================ Lab 11 applied to Lab 10
def _commit_types(client: MongoClient) -> dict:
    t = client.admin.command("serverStatus").get("transactions", {}).get("commitTypes", {})
    return {k: int(v.get("successful", 0)) for k, v in t.items()}


def scale_out(log=print) -> dict:
    """Move cti_billing from rs0 to the sharded cluster once, verify the copy, then switch the live marker.
    Repeated calls only make sure the sharded schema exists."""
    from . import lab11
    rs, sh = _rs0(), lab11.client()
    meta = rs[DB].meta
    state = (meta.find_one({"_id": "backend"}) or {}).get("value", "replica_set")
    ensure_schema(rs, sharded=False, log=log)
    ensure_schema(sh, sharded=True, log=log)
    if state == "sharded":
        log("   billing already lives on the sharded cluster")
        backend(refresh=True)
        return {"already": True, "moved": {}}
    now = datetime.now(UTC)
    meta.update_one({"_id": "backend"}, {"$set": {"value": "migrating", "at": now}}, upsert=True)
    log("   marker -> migrating (the website answers 503 for billing writes for a few seconds)")
    time.sleep(3.5)                                   # every web process re-reads the marker within 3 s
    moved = {}
    try:
        for c in ("accounts", "usage", "payments"):
            docs = list(rs[DB][c].find())
            if docs:
                try:
                    sh[DB][c].insert_many(docs, ordered=False)
                except PyMongoError as exc:           # re-run after a crash: keep the documents already there
                    if "E11000" not in str(exc):
                        raise
            moved[c] = len(docs)
            n_sh = sh[DB][c].count_documents({})
            if n_sh < len(docs):
                raise RuntimeError(f"copy of {c} incomplete: {n_sh} of {len(docs)}")
            log(f"   copied {c}: {len(docs):,} documents (sharded cluster now has {n_sh:,})")
        for d in rs[DB].revenue.find():
            sh[DB].revenue.update_one({"_id": d["_id"]}, {"$set": {"amount": d.get("amount", 0),
                                                                   "payments": d.get("payments", 0)}}, upsert=True)
        moved["revenue"] = BUCKETS
        inv_rs, inv_sh = invariants(rs), invariants(sh)
        if (inv_rs["wallets"], inv_rs["revenue"]) != (inv_sh["wallets"], inv_sh["revenue"]):
            raise RuntimeError(f"verification failed: rs0 {inv_rs} vs sharded {inv_sh}")
        meta.update_one({"_id": "backend"}, {"$set": {"value": "sharded", "at": datetime.now(UTC), "moved": moved}})
        log(f"   verified (wallet total Rs {inv_sh['wallets']}, revenue Rs {inv_sh['revenue']} on both) -> marker = sharded")
    except Exception:
        meta.update_one({"_id": "backend"}, {"$set": {"value": "replica_set", "at": datetime.now(UTC)}})
        backend(refresh=True)
        raise
    backend(refresh=True)
    return {"already": False, "moved": moved, "verified": True}


def load_test(n_users: int = 300, threads: int = 8, log=print) -> dict:
    """Many users at once on the sharded billing data: sign in, use free trials, top up, subscribe, keep using."""
    from concurrent.futures import ThreadPoolExecutor
    from . import lab11
    sh = lab11.client()
    tag = f"{int(time.time()) % 100000:05d}"
    stats = {"ok": 0, "refused": 0, "subscribed": 0, "retries": 0, "errors": 0}
    lock = threading.Lock()
    ct0 = _commit_types(sh)

    def journey(i):
        R = random.Random(i)
        uid = sign_in(f"load{tag}-{i:04d}@lab11.test", f"Load user {i}", client=sh, synthetic=True)["user_id"]
        local = {"ok": 0, "refused": 0, "subscribed": 0, "retries": 0, "errors": 0}
        try:
            for _ in range(R.randint(2, 5)):
                try:
                    r = consume(uid, lambda: _fake_work(1), client=sh)
                    local["ok"] += 1
                    local["retries"] += r["attempts"] - 1
                except PaymentRequired:
                    local["refused"] += 1
            if R.random() < 0.5:
                topup(uid, 200, "upi-1", client=sh)
                subscribe(uid, "pay-1", client=sh)
                local["subscribed"] += 1
                for _ in range(R.randint(1, 3)):
                    consume(uid, lambda: _fake_work(1), client=sh)
                    local["ok"] += 1
        except Exception:                              # noqa: BLE001
            local["errors"] += 1
        with lock:
            for k, v in local.items():
                stats[k] += v

    t0 = time.perf_counter()
    with ThreadPoolExecutor(threads) as ex:
        list(ex.map(journey, range(n_users)))
    secs = time.perf_counter() - t0
    ct1 = _commit_types(sh)
    commit = {k: ct1.get(k, 0) - ct0.get(k, 0) for k in ct1}
    dist = {c: {s: v["count"] for s, v in lab11.shard_counts(DB, c).items()} for c in ("accounts", "usage", "payments")}
    some = f"load{tag}-0007@lab11.test"
    targeting = {
        "account by _id": lab11.explain_query(DB, "accounts", {"_id": some}, runs=3),
        "usage of one user": lab11.explain_query(DB, "usage", {"user_id": some}, runs=3),
        "payments of one user": lab11.explain_query(DB, "payments", {"user_id": some}, runs=3),
        "all active subscribers (admin report)": lab11.explain_query(DB, "accounts",
                                                                     {"plan.expires_at": {"$gt": datetime.now(UTC)}}, runs=3),
    }
    txns = sum(commit.values())
    out = {"users": n_users, "threads": threads, "seconds": round(secs, 2), **stats,
           "transactions": txns, "txn_per_s": round(txns / secs, 1) if secs else None, "commit_types": commit,
           "distribution": dist, "targeting": {k: {kk: v[kk] for kk in ("n_shards", "total_shards", "kind", "stage", "shards")}
                                               for k, v in targeting.items()},
           "invariants": invariants(sh)}
    log(f"   {n_users} users x {threads} threads in {secs:.1f} s: {stats['ok']} classifications, {stats['refused']} refused "
        f"after the free trials, {stats['subscribed']} subscriptions, {stats['retries']} conflict retries, "
        f"{stats['errors']} errors")
    log(f"   mongos commit types: " + ", ".join(f"{k} {v}" for k, v in commit.items() if v))
    for c, d in dist.items():
        log(f"   {c:<9} by shard: " + ", ".join(f"{s} {n:,}" for s, n in d.items()))
    for k, v in targeting.items():
        log(f"   explain {k:<38} -> {v['n_shards']} of {v['total_shards']} shards ({v['kind']})")
    inv = out["invariants"]
    log(f"   invariants on the sharded data: money conserved {inv['money_conserved']}, revenue matches {inv['revenue_matches']}")
    return out

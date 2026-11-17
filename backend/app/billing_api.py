"""REST API of the classifier billing (Lab 10 applied to Lab 7; Lab 11 shards it). Mounted under /api by labs_api.py."""
from __future__ import annotations

import json
import threading

from fastapi import APIRouter, Body, HTTPException
from pymongo.errors import PyMongoError

from . import billing as B

router = APIRouter(prefix="/billing")
_ready: set[str] = set()
_lock = threading.Lock()


def _j(x):
    return json.loads(json.dumps(x, default=str))


def ready():
    """Create the billing collections on the live backend once per process."""
    b = B.backend()
    if b["name"] not in _ready and b["name"] != "migrating":
        with _lock:
            B.ensure_schema(b["client"], sharded=b["name"] == "sharded", log=lambda *a: None)
            _ready.add(b["name"])
    return b


def call(fn, *a, **kw):
    try:
        ready()
        return _j(fn(*a, **kw))
    except B.BillingError as exc:
        raise HTTPException(exc.status, str(exc))
    except PyMongoError as exc:
        raise HTTPException(503, f"Billing database unavailable: {type(exc).__name__}: {str(exc)[:160]}")


@router.post("/signin")
def signin(p: dict = Body(...)):
    return call(B.sign_in, p.get("email", ""), p.get("name", ""))


@router.post("/me")
def me(p: dict = Body(...)):
    uid = str(p.get("user", ""))
    acc = call(B.account, uid)
    if not acc:
        raise HTTPException(401, "Unknown user - sign in again.")
    b = B.backend()
    return {"account": acc, "history": call(B.history, uid), "backend": b["label"]}


@router.post("/topup")
def topup(p: dict = Body(...)):
    return call(B.topup, str(p.get("user", "")), int(p.get("amount", 0) or 0), str(p.get("idem", "")))


@router.post("/subscribe")
def subscribe(p: dict = Body(...)):
    return call(B.subscribe, str(p.get("user", "")), str(p.get("idem", "")),
                crash_after_debit=bool(p.get("crash_after_debit")))


@router.get("/summary")
def summary():
    try:
        ready()
    except PyMongoError:
        pass
    return _j(B.summary())


@router.post("/acid-checks")
def acid_checks():
    """Lab 10 page: run the four ACID experiments on the live billing backend (about 1 s)."""
    b = ready()
    lines: list[str] = []
    try:
        return _j({**B.acid_checks(b["client"], log=lines.append), "log": lines, "backend": b["label"]})
    except PyMongoError as exc:
        raise HTTPException(503, str(exc)[:200])


@router.get("/shards")
def shards():
    """Lab 11 page: where the billing data sits and how transactions committed (mongos serverStatus)."""
    from . import lab11
    av = lab11.available()
    if not av["available"]:
        return {"available": False, "reason": av["reason"], "backend": B.backend()["label"]}
    try:
        sh = lab11.client()
        dist = {c: {s: v["count"] for s, v in lab11.shard_counts(B.DB, c).items()} for c in ("accounts", "usage", "payments", "revenue")}
        keys = {c["_id"].split(".", 1)[1]: lab11.key_text(c["key"]) for c in sh.config.collections.find({"_id": {"$regex": f"^{B.DB}\\."}})}
        return _j({"available": True, "backend": B.backend()["label"], "backend_name": B.backend()["name"],
                   "distribution": dist, "keys": keys, "commit_types": B._commit_types(sh)})
    except PyMongoError as exc:
        return {"available": False, "reason": str(exc)[:200], "backend": B.backend()["label"]}


@router.post("/scale-out")
def scale_out(p: dict = Body(default={})):
    """Lab 11 page: move billing to the shards (once) and run a load test with N users (default 120)."""
    from . import lab11
    if not lab11.available()["available"]:
        raise HTTPException(503, "Start the sharded cluster first: docker compose up -d lab11")
    if not lab11.HEAVY.acquire(blocking=False):
        raise HTTPException(409, "Another Lab 11 experiment is running.")
    try:
        lines: list[str] = []
        mig = B.scale_out(log=lines.append)
        _ready.discard("sharded")
        lt = B.load_test(max(20, min(int(p.get("users", 120)), 600)), 8, log=lines.append)
        return _j({"migration": mig, "load": lt, "log": lines})
    except PyMongoError as exc:
        raise HTTPException(500, str(exc)[:200])
    finally:
        lab11.HEAVY.release()

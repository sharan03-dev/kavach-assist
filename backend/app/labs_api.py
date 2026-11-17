"""REST API for the lab-control pages: dependency chain, Lab 8 (replica set), Lab 9 (write concern), Lab 10 (transactions).
Labs 11, 12, 13 and 14 have their own routers (lab11_api.py ... lab14_api.py), mounted at the end of this file."""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.concurrency import run_in_threadpool
from pymongo import MongoClient, ReadPreference
from pymongo.errors import PyMongoError

from . import chain, config, lab10 as L
from .db import client, get_db
from .labs import to_jsonable as J

router = APIRouter(prefix="/api")
ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
UTC = timezone.utc
_heavy = threading.Lock()          # one long benchmark at a time


def _need_rs():
    try:
        hello = client().admin.command("hello")
    except PyMongoError as exc:
        raise HTTPException(503, f"MongoDB not reachable: {str(exc)[:140]}")
    if not hello.get("setName"):
        raise HTTPException(409, "This MongoDB is a standalone server. Multi-document transactions and the replica-set "
                                 "labs need the Docker stack:  docker compose up -d lab10")
    return hello


# ======================================================================= dependency chain
@router.get("/chain/status")
def chain_status():
    db = get_db()
    snap = chain.snapshot(db)
    hb = db.ingest_runs.find_one({"kind": "auto_update"}, sort=[("started_at", -1)])
    interval = int(os.getenv("AUTO_UPDATE_INTERVAL", "300"))
    snap["updater"] = {"interval_s": interval, "last": (hb or {}).get("started_at"),
                       "alive": bool(hb and (datetime.now(UTC) - hb["started_at"]).total_seconds() < interval * 2 + 60),
                       "runs": db.ingest_runs.count_documents({"kind": "auto_update"})}
    return J(snap)


@router.get("/chain/plan/{lab}")
def chain_plan(lab: int):
    try:
        return {"lab": lab, "order": chain.chain_for(lab)}
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/chain/run")
def chain_run(payload: dict = Body(...)):
    lab = int(payload.get("lab", 0))
    try:
        order = chain.chain_for(lab)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    if chain.snapshot(get_db(), tail=0)["running"]:
        raise HTTPException(409, "A lab run is already in progress - wait for it to finish.")
    cmd = [sys.executable, str(ROOT / "orchestrator" / "labctl.py"), "run", str(lab)]
    if payload.get("full"):
        cmd.append("--full")
    if payload.get("full_all"):
        cmd.append("--full-all")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = open(RESULTS / "labctl_web.log", "ab")
    subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    chain.reset_for_run(get_db(), order, lab, {n: "full" if payload.get("full_all") or (payload.get("full") and n == lab) or (n == lab and lab >= 10) else "quick" for n in order})
    return {"started": True, "order": order}


# ======================================================================= Lab 8 - replica set
def _rs_summary():
    c = client()
    st = c.admin.command("replSetGetStatus", read_preference=ReadPreference.PRIMARY_PREFERRED)
    cfg = c.admin.command("replSetGetConfig", read_preference=ReadPreference.PRIMARY_PREFERRED)["config"]
    cm = {m["host"]: m for m in cfg["members"]}
    prim = next((m for m in st["members"] if m["stateStr"] == "PRIMARY"), None)
    ref = (prim or {}).get("optimeDate")
    rows = []
    for m in st["members"]:
        conf = cm.get(m["name"], {})
        lag = None
        if ref is not None and m.get("optimeDate") is not None and m.get("health") == 1:
            lag = max(0.0, (ref - m["optimeDate"]).total_seconds())
        rows.append({"name": m["name"], "state": m["stateStr"], "health": m.get("health"), "uptime": m.get("uptime"),
                     "lag_s": lag, "sync_source": m.get("syncSourceHost") or None, "ping_ms": m.get("pingMs"),
                     "priority": conf.get("priority"), "votes": conf.get("votes"), "arbiter": conf.get("arbiterOnly", False),
                     "election_date": m.get("electionDate")})
    voters = sum(1 for m in cfg["members"] if m.get("votes", 0) > 0)
    return {"set": st["set"], "term": st.get("term"), "members": rows, "primary": prim["name"] if prim else None,
            "voters": voters, "majority": voters // 2 + 1, "write_majority_count": st.get("writeMajorityCount"),
            "journal_majority_default": cfg.get("writeConcernMajorityJournalDefault"), "at": datetime.now(UTC)}


@router.get("/rs/status")
def rs_status():
    try:
        hello = client().admin.command("hello")
    except PyMongoError as exc:
        return {"available": False, "reason": f"MongoDB not reachable: {str(exc)[:120]}"}
    if not hello.get("setName"):
        return {"available": False, "reason": "standalone server - start the Docker stack for Labs 8-10"}
    try:
        return J({"available": True, **_rs_summary()})
    except PyMongoError as exc:
        return {"available": False, "reason": str(exc)[:160]}


@router.post("/rs/stepdown")
def rs_stepdown(payload: dict = Body(default={})):
    _need_rs()
    secs = max(10, min(int(payload.get("seconds", 30)), 120))
    before = _rs_summary()
    t0 = time.perf_counter()
    try:
        client().admin.command("replSetStepDown", secs, secondaryCatchUpPeriodSecs=10)
    except PyMongoError:
        pass                                              # the connection is closed by the step-down: expected
    after = None
    while time.perf_counter() - t0 < 25:
        try:
            s = _rs_summary()
            if s["primary"] and s["primary"] != before["primary"]:
                after = s
                break
        except PyMongoError:
            pass
        time.sleep(0.25)
    return J({"old_primary": before["primary"], "new_primary": after["primary"] if after else None,
              "election_ms": round((time.perf_counter() - t0) * 1000), "term_before": before["term"],
              "term_after": after["term"] if after else None, "stepdown_seconds": secs, "status": after or _safe_rs()})


def _safe_rs():
    try:
        return _rs_summary()
    except PyMongoError:
        return None


@router.post("/lab8/verify")
def lab8_verify():
    """Lab 8.1 in one click: insert with w:3, then read the document directly from every secondary."""
    _need_rs()
    from pymongo import WriteConcern
    c = client()
    coll = c["labdb"].get_collection("replication_test", write_concern=WriteConcern(w=3, wtimeout=5000))
    t0 = time.perf_counter()
    _id = coll.insert_one({"msg": "Hello from the Kavach website (Lab 8.1)", "createdAt": datetime.now(UTC)}).inserted_id
    ack_ms = round((time.perf_counter() - t0) * 1000, 1)
    seen = []
    for host, port in sorted(c.secondaries):
        d = MongoClient(host, port, directConnection=True, read_preference=ReadPreference.SECONDARY_PREFERRED,
                        serverSelectionTimeoutMS=3000)
        try:
            doc = d["labdb"]["replication_test"].find_one({"_id": _id})
            seen.append({"member": f"{host}:{port}", "found": bool(doc)})
        finally:
            d.close()
    return J({"inserted_id": str(_id), "acknowledged_by": 3, "ack_ms": ack_ms, "primary": "%s:%s" % c.primary,
              "secondaries": seen})


# ======================================================================= Lab 9 - write concern
def _read_json(name):
    p = RESULTS / name
    try:
        return json.loads(p.read_text())
    except Exception:                                     # noqa: BLE001
        return None


@router.get("/lab9/results")
def lab9_results():
    tl = RESULTS / "lab9_2_timeline.md"
    return J({"w": _read_json("lab9_1_results.json"), "journal": _read_json("lab9_1_journal_results.json"),
              "timeline": tl.read_text()[:6000] if tl.exists() else None})


@router.post("/lab9/quick")
def lab9_quick(payload: dict = Body(default={})):
    hello = _need_rs()
    docs = max(20, min(int(payload.get("docs", 100)), 300))
    members = len(hello.get("hosts", []))
    if not _heavy.acquire(blocking=False):
        raise HTTPException(409, "another benchmark is running")
    try:
        rows = []
        for label, w in (("w:1", 1), ("w:majority", "majority"), (f"w:{members} (all)", members)):
            c = MongoClient(config.MONGO_URI, w=w, wTimeoutMS=10000, serverSelectionTimeoutMS=5000)
            coll = c["lab9_web"]["wc_test"]
            coll.insert_one({"warm": True})
            lat = []
            for i in range(docs):
                t0 = time.perf_counter()
                coll.insert_one({"i": i, "at": datetime.now(UTC), "pad": "x" * 100})
                lat.append((time.perf_counter() - t0) * 1000)
            c.close()
            lat.sort()
            rows.append({"label": label, "docs": docs, "avg_ms": round(statistics.fmean(lat), 3),
                         "p50_ms": round(lat[len(lat) // 2], 3), "p95_ms": round(lat[int(len(lat) * .95) - 1], 3),
                         "writes_per_s": round(1000 / statistics.fmean(lat))})
        client()["lab9_web"]["wc_test"].drop()
        return {"rows": rows}
    finally:
        _heavy.release()


# ======================================================================= Lab 10 - transactions
@router.get("/lab10/state")
def lab10_state():
    _need_rs()
    return J({"bank": L.bank_state(client()), "shop": L.shop_state(client())})


@router.post("/lab10/reset")
def lab10_reset():
    _need_rs()
    c = client()
    return J({"bank": L.seed_bank(c), "shop": L.seed_shop(c)})


@router.post("/lab10/transfer")
def lab10_transfer(p: dict = Body(...)):
    _need_rs()
    amount = int(p.get("amount", 0))
    if amount <= 0:
        raise HTTPException(422, "amount must be a positive number")
    r = L.transfer(client(), p.get("src", "ACC-001"), p.get("dst", "ACC-002"), amount,
                   crash_after_debit=bool(p.get("crash")))
    return J({"result": r, "bank": L.bank_state(client())})


@router.post("/lab10/conflict-demo")
def lab10_conflict():
    _need_rs()
    return J(L.conflict_demo(client()))


@router.post("/lab10/sample-bug")
def lab10_sample_bug():
    _need_rs()
    c = client()
    L.seed_bank(c)
    start = L.bank_state(c)["total"]
    info = L.unsafe_transfer_like_sample(c, "ACC-001", "ACC-002", 5000)
    state = L.bank_state(c)
    L.seed_bank(c)
    return J({"total_before": start, "total_after": state["total"], "debit_matched": info["debit_matched"],
              "accounts_after": state["accounts"]})


@router.post("/lab10/reserve")
def lab10_reserve(p: dict = Body(default={})):
    _need_rs()
    c = client()
    if L.shop_state(c)["inventory"] is None:
        L.seed_shop(c)
    r = L.reserve_inventory(c, "P-100", int(p.get("qty", 1)), p.get("customer", "web-user-" + uuid.uuid4().hex[:4]))
    return J({"result": r, "shop": L.shop_state(c)})


@router.post("/lab10/concurrency")
async def lab10_concurrency(p: dict = Body(default={})):
    _need_rs()
    threads = max(2, min(int(p.get("threads", 8)), 32))
    stock = max(1, min(int(p.get("stock", 5)), 100))
    return J(await run_in_threadpool(L.concurrency_test, client(), threads=threads, stock=stock, retry=bool(p.get("retry", True))))


@router.post("/lab10/atomicity")
async def lab10_atomicity():
    _need_rs()
    return J(await run_in_threadpool(L.atomicity_demo, client()))


@router.post("/lab10/throughput")
async def lab10_throughput(p: dict = Body(default={})):
    _need_rs()
    if not _heavy.acquire(blocking=False):
        raise HTTPException(409, "another benchmark is running")
    try:
        ops = max(20, min(int(p.get("ops", 60)), 300))
        return J(await run_in_threadpool(lambda: L.throughput(client(), ops=ops, log=lambda *_: None)))
    finally:
        _heavy.release()


@router.get("/lab10/results")
def lab10_results():
    doc = get_db().lab_results.find_one({"_id": "lab_10"}) or _read_json("lab10_results.json") or {}
    return J(doc)


@router.get("/lab10/report")
def lab10_report():
    p = RESULTS / "Exercise-10_Report.pdf"
    if not p.exists():
        raise HTTPException(404, "The report has not been generated yet. Run: docker compose exec lab10 lab10 report")
    return FileResponse(p, media_type="application/pdf", filename="Exercise-10_Report.pdf")


# ======================================================================= Lab 11 - sharding (own module)
from .lab11_api import router as _lab11_router  # noqa: E402

router.include_router(_lab11_router)

from .billing_api import router as _billing_router  # noqa: E402

router.include_router(_billing_router)

from .lab12_api import router as _lab12_router  # noqa: E402  (Lab 12: database security on mongo-secure)

router.include_router(_lab12_router)

from .lab13_api import router as _lab13_router  # noqa: E402  (Lab 13: containers, Kubernetes and the cloud)

router.include_router(_lab13_router)

from .lab14_api import router as _lab14_router  # noqa: E402  (Lab 14: monitoring, alerts, backups, recovery)

router.include_router(_lab14_router)

from .lab15_api import router as _lab15_router  # noqa: E402  (Lab 15: multi-tenant SaaS)

router.include_router(_lab15_router)

from .lab161718_api import router as _polyglot_router  # noqa: E402  (Labs 16, 17, 18: Cassandra, Neo4j, vector search, capstone)

router.include_router(_polyglot_router)

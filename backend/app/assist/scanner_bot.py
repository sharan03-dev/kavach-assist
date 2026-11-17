"""The scanner bot - a background service (its own container: `docker compose up -d scanner-bot`).

    loop:  heartbeat -> run the next confirmed job (fix / rollback / reset / scan now) -> scan every SCAN_INTERVAL seconds

It talks to the chat only through MongoDB (database kavach_assist):
    scan_findings   one document per finding (status open -> fixed / failed / resolved / ignored)
    scan_runs       history of scans            scan_jobs   work requested and CONFIRMED by a user in the chat
    notifications   "the bot found something new" messages shown in the chat
The bot never fixes anything without a job, and a job is only created after the user answered "yes" in the conversation.

Run:  python -m backend.app.assist.scanner_bot            (once: --once)
"""
from __future__ import annotations

import secrets
import signal
import socket
import sys
import time

from pymongo import ReturnDocument, UpdateOne
from pymongo.errors import PyMongoError

from . import scanner
from . import settings as S
from . import store

STOP = {"now": False}


def log(msg: str) -> None:
    print(f"[scanner-bot] {msg}", flush=True)


def beat(extra: dict | None = None) -> None:
    try:
        open("/tmp/scanner-alive", "w").close()          # container health check: this file must be younger than 2 minutes
    except OSError:
        pass
    store.adb().scanner_state.update_one({"_id": "bot"}, {"$set": {"last_beat": store.now(), "host": socket.gethostname(), "target": S.SCAN_TARGET,
                                                                  "interval_s": S.SCAN_INTERVAL, "system_scan": S.SCAN_SYSTEM, **(extra or {})}}, upsert=True)


def exploited_map() -> dict:
    """CVE -> status for everything Kavach knows to be exploited (used to raise the priority of a finding)."""
    try:
        return {t["_id"]: t["status"] for t in store.adb().threats.find({"kind": "vulnerability", "exploited": True}, {"status": 1})}
    except PyMongoError:
        return {}


def run_scan(reason: str = "schedule") -> dict:
    d = store.adb()
    t0 = time.time()
    res = scanner.scan(exploited=exploited_map())
    now = store.now()
    ids = [f["_id"] for f in res["findings"]]
    known = {x["_id"]: x.get("status") for x in d.scan_findings.find({"_id": {"$in": ids}}, {"status": 1})}
    ops = []
    for f in res["findings"]:
        fid = f.pop("_id")
        upd = {"$set": {**f, "last_seen": now}, "$setOnInsert": {"first_seen": now, "status": "open"}}
        ops.append(UpdateOne({"_id": fid}, upd, upsert=True))
    if ops:
        d.scan_findings.bulk_write(ops, ordered=False)
    # found again although it was fixed / resolved / rolled back -> open again
    reopened = d.scan_findings.update_many({"_id": {"$in": ids}, "status": {"$in": ["fixed", "resolved", "rolled_back"]}},
                                           {"$set": {"status": "open", "reopened_at": now}}).modified_count
    # not found any more -> resolved (a fix job marks its own findings "fixed" before this runs)
    gone = d.scan_findings.update_many({"_id": {"$nin": ids}, "status": {"$in": ["open", "failed"]}},
                                       {"$set": {"status": "resolved", "resolved_at": now}}).modified_count
    new = [i for i in ids if i not in known]
    counts = {s: d.scan_findings.count_documents({"status": "open", "severity": s}) for s in ("Critical", "High", "Medium", "Low")}
    open_n = sum(counts.values())
    run = {"_id": "run_" + secrets.token_hex(6), "at": now, "reason": reason, "seconds": round(time.time() - t0, 1), "found": len(ids), "new": len(new),
           "reopened": reopened, "resolved": gone, "open": open_n, "by_severity": counts, "notes": res["notes"], "target": res["target"],
           "platform": res["platform"]}
    d.scan_runs.insert_one(run)
    if new or reopened:
        fixable = d.scan_findings.count_documents({"status": "open", "fixable": True})
        top = list(d.scan_findings.find({"_id": {"$in": new}}, {"title": 1, "severity": 1}).sort("severity_rank", -1).limit(3))
        d.notifications.insert_one({"ts": now, "kind": "scan", "run": run["_id"], "new": len(new) + reopened, "open": open_n, "fixable": fixable,
                                    "by_severity": counts, "top": [t["title"] for t in top],
                                    "text": f"Scanner bot: {len(new) + reopened} new issue(s) on {res['platform'].split()[0]} "
                                            f"({open_n} open, {fixable} can be fixed automatically). Ask me to \"show findings\" or \"fix all\"."})
    beat({"last_scan": now, "last_run": run["_id"], "open": open_n})
    log(f"scan ({reason}): {len(ids)} finding(s), {len(new)} new, {gone} resolved, {open_n} open in {run['seconds']} s"
        + (f"; notes: {'; '.join(res['notes'])}" if res["notes"] else ""))
    return run


def run_job(job: dict) -> None:
    d = store.adb()
    results, summary = [], ""
    try:
        if job["type"] == "fix":
            for f in d.scan_findings.find({"_id": {"$in": job.get("finding_ids", [])}}):
                if f.get("status") not in ("open", "failed") or not f.get("fixable"):
                    results.append({"finding": f["_id"], "title": f["title"], "ok": False, "message": "not open or not fixable"})
                    continue
                r = scanner.apply(f, job["_id"])
                results.append({"finding": f["_id"], "title": f["title"], **r})
                d.scan_findings.update_one({"_id": f["_id"]}, {"$set": {"status": "fixed" if r["ok"] else "failed", "fixed_at": store.now(),
                                                                         "fixed_by": job.get("requested_by"), "job": job["_id"],
                                                                         "fix_message": r["message"]}})
            ok = sum(1 for r in results if r["ok"])
            verify = run_scan("verify after fix")
            still = {x["_id"] for x in d.scan_findings.find({"_id": {"$in": job.get("finding_ids", [])}, "status": "open"}, {"_id": 1})}
            for r in results:
                r["verified"] = r["ok"] and r["finding"] not in still
            summary = f"{ok} of {len(results)} fix(es) applied; verified by a new scan: {sum(1 for r in results if r.get('verified'))}; {verify['open']} issue(s) still open"
        elif job["type"] == "rollback":
            r = scanner.rollback(job["rollback_of"])
            results.append(r)
            if r["ok"]:
                d.scan_findings.update_many({"job": job["rollback_of"], "kind": {"$ne": "os-package"}}, {"$set": {"status": "rolled_back"}})
                d.scan_jobs.update_one({"_id": job["rollback_of"]}, {"$set": {"rolled_back": True}})
            run_scan("verify after rollback")
            summary = r["message"]
        elif job["type"] == "reset":
            scanner.seed_demo(force=True)
            d.scan_findings.delete_many({"scope": "project"})
            run_scan("demo project reset")
            summary = "demo project restored to its original (vulnerable) state"
        elif job["type"] == "scan":
            run = run_scan("requested in chat")
            summary = f"scan finished: {run['open']} open issue(s), {run['new']} new"
        status = "done"
    except Exception as exc:                           # noqa: BLE001
        status, summary = "failed", f"{type(exc).__name__}: {str(exc)[:200]}"
    d.scan_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": status, "finished_at": store.now(), "results": results, "summary": summary}})
    store.audit(f"scanner.{job['type']}", actor=job.get("requested_by"), job=job["_id"], status=status, summary=summary)
    log(f"job {job['_id']} ({job['type']}) {status}: {summary}")


def next_job():
    return store.adb().scan_jobs.find_one_and_update({"status": "queued"}, {"$set": {"status": "running", "started_at": store.now()}},
                                                     sort=[("created_at", 1)], return_document=ReturnDocument.AFTER)


def main(once: bool = False) -> None:
    signal.signal(signal.SIGTERM, lambda *_: STOP.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: STOP.update(now=True))
    log(f"starting: target {S.SCAN_TARGET}, every {S.SCAN_INTERVAL} s, system scan {'on' if S.SCAN_SYSTEM else 'off'}")
    while not STOP["now"]:
        try:
            store.ensure_indexes()
            break
        except PyMongoError as exc:
            log(f"waiting for MongoDB ({type(exc).__name__})")
            time.sleep(5)
    if scanner.seed_demo():
        log("the scan folder was empty: copied the demo project into it")
    last = 0.0
    while not STOP["now"]:
        try:
            beat()
            job = next_job()
            if job:
                run_job(job)
                last = time.time()
            elif time.time() - last >= S.SCAN_INTERVAL:
                run_scan("first scan" if last == 0 else "schedule")
                last = time.time()
        except PyMongoError as exc:
            log(f"MongoDB problem, retrying: {type(exc).__name__}: {str(exc)[:120]}")
            time.sleep(5)
        except Exception as exc:                       # noqa: BLE001 - the bot must never die
            log(f"error: {type(exc).__name__}: {str(exc)[:200]}")
            last = time.time()
        if once:
            break
        for _ in range(4):
            if STOP["now"]:
                break
            time.sleep(0.5)
    store.adb().scanner_state.update_one({"_id": "bot"}, {"$set": {"stopped_at": store.now()}})
    store.close()
    log("stopped cleanly")


if __name__ == "__main__":
    main(once="--once" in sys.argv)

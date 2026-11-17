"""REST API of the website's "Operations lab 14" page. Mounted under /api by labs_api.py.

Every action calls the same functions as the command line (backend/app/lab14.py -> labs/lab14/*.py). The built-in
webhook receiver (/api/lab14/webhook/slack|discord) stands in for a Slack / Discord channel so the alert activity works
without an internet account; LAB14_WEBHOOK_URL adds a real one."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pymongo.errors import PyMongoError

from . import lab14 as L

router = APIRouter(prefix="/lab14")
UTC = timezone.utc


def _j(x):
    return json.loads(json.dumps(x, default=str))


async def _run(fn, *a, part: str | None = None, **kw):
    av = await run_in_threadpool(L.L13.available)
    if not av["available"]:
        raise HTTPException(503, av["reason"] + "  - start it with:  docker compose up -d lab14")
    if not L.HEAVY.acquire(blocking=False):
        raise HTTPException(409, "Another Lab 14 task is running - wait for it to finish.")
    try:
        lines: list[str] = []
        out = await run_in_threadpool(fn, *a, log=lines.append, **kw)
        if part:
            await run_in_threadpool(L.save_part, part, out)
        return {**_j(out), "log": lines} if isinstance(out, dict) else {"result": _j(out), "log": lines}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)[:300])
    except PyMongoError as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {str(exc)[:220]}")
    finally:
        L.HEAVY.release()


@router.get("/status")
async def status():
    try:
        return await run_in_threadpool(L.status)
    except Exception as exc:                          # noqa: BLE001
        return {"available": False, "reason": str(exc)[:200]}


@router.get("/results")
async def results():
    return _j(await run_in_threadpool(L.load_results))


@router.get("/live")
async def live():
    try:
        return await run_in_threadpool(L.LIVE.sample)
    except Exception as exc:                          # noqa: BLE001
        raise HTTPException(503, f"{type(exc).__name__}: {str(exc)[:200]}")


@router.get("/history")
async def history(minutes: int = 60):
    return await run_in_threadpool(L.history, max(5, min(minutes, 1440)))


@router.get("/metrics-log", response_class=PlainTextResponse)
async def metrics_log(lines: int = 60):
    return await run_in_threadpool(L.metrics_log_tail, max(10, min(lines, 400)))


@router.get("/alerts")
async def alerts(limit: int = 60):
    def q():
        return [_j(a) for a in L.kdb()["lab14_alerts"].find({}, {"_id": 0}).sort("at", -1).limit(limit)]
    return await run_in_threadpool(q)


# ---------------------------------------------------------------- the built-in "channel" (webhook receiver)
@router.post("/webhook/{channel}")
async def webhook(channel: str, request: Request):
    if channel not in ("slack", "discord"):
        raise HTTPException(404, "channel must be slack or discord")
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "invalid_payload")      # what Slack answers to a non-JSON body
    valid = ("text" in payload) if channel == "slack" else ("content" in payload or "embeds" in payload)
    if not valid:
        raise HTTPException(400, "missing text" if channel == "slack" else "Cannot send an empty message")
    doc = {"at": datetime.now(UTC), "channel": channel, "payload": payload,
           "from": request.client.host if request.client else None}
    await run_in_threadpool(lambda: L.kdb()["lab14_inbox"].insert_one(doc))
    return PlainTextResponse("ok") if channel == "slack" else Response(status_code=204)   # what the real services reply


@router.get("/inbox")
async def inbox(limit: int = 40):
    def q():
        return [_j(a) for a in L.kdb()["lab14_inbox"].find({}, {"_id": 0}).sort("at", -1).limit(limit)]
    return await run_in_threadpool(q)


@router.post("/webhook-test")
async def webhook_test(p: dict = Body(default={})):
    return await _run(L.test_alert, p.get("format", "both"))


# ---------------------------------------------------------------- drills + labs
@router.post("/drill/lag")
async def drill_lag(p: dict = Body(default={})):
    return await _run(L.lag_drill, int(max(16, min(int(p.get("seconds", 24)), 60))), part="lag_drill")


@router.post("/drill/cache")
async def drill_cache():
    return await _run(L.cache_drill, part="cache_drill")


@router.get("/backups")
async def backups():
    def q():
        hist = [_j(b) for b in L.kdb()["lab14_backups"].find({}, {"_id": 0}).sort("at", -1).limit(30)]
        return {"folders": L.list_all_backups(), "history": hist, "daemon": L.daemon_status(),
                "backup_dir": str(L.BACKUP_DIR), "targets": L.TARGETS}
    return _j(await run_in_threadpool(q))


@router.post("/backup")
async def backup(p: dict = Body(default={})):
    t = p.get("target", "billing")
    if t not in ("billing", "kavach", "tasktracker", "prod-full"):
        raise HTTPException(422, "target must be billing, kavach or prod-full")
    return await _run(L.backup_now, t)


@router.post("/retention")
async def retention():
    return await _run(L.retention_demo, part="retention")


@router.post("/verify")
async def verify():
    return await _run(L.verify, part="verify")


@router.post("/pitr")
async def pitr():
    return await _run(L.pitr, part="pitr")


@router.post("/benchmark")
async def benchmark(p: dict = Body(default={})):
    sizes = (1000, 10000, 100000) if p.get("full", True) else (1000, 10000)
    return await _run(L.benchmark, sizes, part="benchmark")


@router.post("/billing-dr")
async def billing_dr():
    return await _run(L.billing_dr, part="billing_dr")


@router.post("/users")
async def users():
    return await _run(L.ensure_users, part="users")


FILES = {"monitor": "monitor_dashboard.py", "backup": "backup_verify.py", "daemon": "ops_daemon.py",
         "runner": "run_lab14.py", "readme": "README.md"}


@router.get("/file/{name}")
def lab_file(name: str):
    if name not in FILES:
        raise HTTPException(404, "unknown file")
    return PlainTextResponse((L.LAB / FILES[name]).read_text())


@router.get("/report")
def report():
    p = L.RESULTS / "Exercise-14_Report.pdf"
    if not p.exists():
        raise HTTPException(404, "The report has not been generated yet. Run: docker compose exec lab14 lab14 all")
    return FileResponse(p, media_type="application/pdf", filename="Exercise-14_Report.pdf")

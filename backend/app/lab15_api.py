"""REST API of the website's "SaaS lab 15" page (mounted under /api by labs_api.py). The TaskTracker product itself is
mounted separately at /tasktracker (Swagger UI: /tasktracker/docs) by main.py."""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from pymongo.errors import PyMongoError

from . import lab15 as L

router = APIRouter(prefix="/lab15")


async def _run(fn, *a, part=None, **kw):
    av = await run_in_threadpool(L.L13.available)
    if not av["available"]:
        raise HTTPException(503, av["reason"] + "  - start it with:  docker compose up -d lab15")
    if not L.HEAVY.acquire(blocking=False):
        raise HTTPException(409, "Another Lab 15 task is running - wait for it to finish.")
    try:
        lines: list[str] = []
        out = await run_in_threadpool(fn, *a, log=lines.append, **kw)
        if part:
            await run_in_threadpool(L.save_part, part, out)
        return {**json.loads(json.dumps(out, default=str)), "log": lines}
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
        return {"available": False, "reason": str(exc)[:200], "demo_users": L.demo_users(), "password": L.DEMO_PW}


@router.get("/results")
async def results():
    return await run_in_threadpool(L.load_results)


@router.post("/seed")
async def seed():
    async def both(log=print):
        return {"user": L.ensure_user(log), **L.seed_demo(log)}
    return await _run(both)


@router.post("/run/{part}")
async def run(part: str, p: dict = Body(default={})):
    fns = {"crud": (L.run_151, "lab_15_1"), "rate": (L.run_rate, "rate"), "rbac": (L.run_rbac, "rbac"),
           "applied": (L.run_applied, "applied"), "migration": (L.run_migration, "lab_15_2")}
    if part not in fns:
        raise HTTPException(404, "part must be crud, rate, rbac, applied or migration")
    fn, name = fns[part]
    if part == "migration":
        return await _run(fn, bool(p.get("quick", False)), part=name)
    return await _run(fn, part=name)


FILES = {"app": "tasktracker/app.py", "migrate": "migrate_tenants.py", "sample": "sample_app.py", "runner": "run_lab15.py",
         "readme": "README.md"}


@router.get("/file/{name}")
def lab_file(name: str):
    if name not in FILES:
        raise HTTPException(404, "unknown file")
    return PlainTextResponse((L.LAB / FILES[name]).read_text())


@router.get("/report")
def report():
    p = L.RESULTS / "Exercise-15_Report.pdf"
    if not p.exists():
        raise HTTPException(404, "The report has not been generated yet. Run: docker compose exec lab15 lab15 all")
    return FileResponse(p, media_type="application/pdf", filename="Exercise-15_Report.pdf")

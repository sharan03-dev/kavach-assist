"""REST API of the website's "Containers & cloud lab 13" page. Mounted under /api by labs_api.py.

Every action uses the same functions as the command-line script (backend/app/lab13.py)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse
from pymongo.errors import PyMongoError

from . import lab13 as L
from .db import get_db

router = APIRouter(prefix="/lab13")
ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))


def _j(x):
    return json.loads(json.dumps(x, default=str))


async def _run(fn, *a, heavy=True, need_prod=True, **kw):
    if need_prod:
        av = await run_in_threadpool(L.available)
        if not av["available"]:
            raise HTTPException(503, av["reason"] + "  - start it with:  docker compose up -d lab13")
    if heavy and not L.HEAVY.acquire(blocking=False):
        raise HTTPException(409, "Another Lab 13 task is running - wait for it to finish.")
    try:
        lines: list[str] = []
        out = await run_in_threadpool(fn, *a, log=lines.append, **kw)
        return {**_j(out), "log": lines} if isinstance(out, dict) else {"result": _j(out), "log": lines}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)[:300])
    except PyMongoError as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {str(exc)[:220]}")
    finally:
        if heavy:
            L.HEAVY.release()


@router.get("/status")
async def status():
    try:
        return await run_in_threadpool(L.status)
    except Exception as exc:                          # noqa: BLE001
        return {"available": False, "reason": str(exc)[:200], "containers": []}


@router.get("/results")
def results():
    doc = None
    try:
        doc = get_db().lab_results.find_one({"_id": "lab_13"}, {"_id": 0})
    except PyMongoError:
        pass
    if not doc:
        p = RESULTS / "lab13_results.json"
        doc = json.loads(p.read_text()) if p.exists() else {}
    return _j(doc or {})


@router.get("/metrics")
async def metrics():
    return await run_in_threadpool(L.live_metrics)


@router.post("/failover")
async def failover(p: dict = Body(default={})):
    ev = p.get("event", "stepdown")
    if ev not in ("stepdown", "crash"):
        raise HTTPException(422, "event must be stepdown or crash")
    return await _run(L.failover, ev)


@router.post("/verify")
async def verify():
    return await _run(L.verify_deployment)


@router.post("/billing")
async def billing(p: dict = Body(default={})):
    ev = p.get("event", "stepdown")
    if ev not in ("stepdown", "crash"):
        raise HTTPException(422, "event must be stepdown or crash")
    return await _run(L.billing_failover, ev, users=int(max(4, min(int(p.get("users", 16)), 40))))


@router.post("/monitoring")
async def monitoring():
    return await _run(L.monitoring, restart_test=False)


@router.get("/k8s")
async def k8s():
    def snap():
        if L.K8S_MODE == "off":
            return {"ready": False, "reason": "LAB13_K8S=off"}
        r = L.kubectl("get", "nodes", "-o", "json", timeout=10)
        if r.rc != 0:
            return {"ready": False, "reason": (r.err or r.out).strip()[-200:] or "Kubernetes not reachable"}
        out = {"ready": True, "pods": L.pods_summary()}
        try:
            out["pvcs"] = [{"name": x["metadata"]["name"], "status": x["status"].get("phase"),
                            "volume": x["spec"].get("volumeName"), "size": x["spec"]["resources"]["requests"]["storage"]}
                           for x in L.kjson("get", "pvc", "-n", L.NS, timeout=15)["items"]]
            ev = L.kjson("get", "events", "-n", L.NS, timeout=15)["items"]
            ev.sort(key=lambda x: x.get("lastTimestamp") or x.get("eventTime") or "")
            out["events"] = [{"at": x.get("lastTimestamp") or x.get("eventTime"), "object": x["involvedObject"].get("name"),
                              "reason": x.get("reason"), "message": (x.get("message") or "")[:160]} for x in ev[-25:]]
            out["rs"] = L.k8s_rs_state() if any(p["pod"].startswith("mongo-") and p["ready"] for p in out["pods"]) else {}
        except Exception as exc:                      # noqa: BLE001
            out["error"] = str(exc)[:200]
        return out
    return _j(await run_in_threadpool(snap))


@router.post("/k8s/delete-pod")
async def k8s_delete(p: dict = Body(default={})):
    pod = str(p.get("pod", "mongo-2"))
    if pod not in ("mongo-0", "mongo-1", "mongo-2"):
        raise HTTPException(422, "pod must be mongo-0, mongo-1 or mongo-2")
    return await _run(L.k8s_resiliency, pod, need_prod=False)


@router.get("/file/{name}")
def lab_file(name: str):
    files = {"compose": "compose/docker-compose.production.yml", "statefulset": "k8s/mongo-statefulset.yaml",
             "service": "k8s/mongo-headless-svc.yaml", "classroom": "k8s/classroom-variant/classroom-statefulset.yaml",
             "terraform": "terraform/main.tf", "terraform-reference": "terraform/reference/main.tf",
             "compose-reference": "compose/reference-docker-compose-production.yml",
             "prometheus": "monitoring/prometheus.yml", "alerts": "monitoring/alerts.yml", "transfers": "mongosh/k8s_transfers.js"}
    if name not in files:
        raise HTTPException(404, "unknown file")
    return PlainTextResponse((L.LAB / files[name]).read_text())


@router.get("/report")
def report():
    p = RESULTS / "Exercise-13_Report.pdf"
    if not p.exists():
        raise HTTPException(404, "The report has not been generated yet. Run: docker compose exec lab13 lab13 all")
    return FileResponse(p, media_type="application/pdf", filename="Exercise-13_Report.pdf")

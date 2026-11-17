"""REST API of the website's "Sharding lab 11" page. Mounted under /api by labs_api.py.

Every heavy action uses the same functions as the command-line scripts (backend/app/lab11.py)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse
from pymongo.errors import PyMongoError

from . import lab11 as L
from .db import get_db

router = APIRouter(prefix="/lab11")
ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))


def _j(x):
    return json.loads(json.dumps(x, default=str))


def _need_cluster():
    av = L.available()
    if not av["available"]:
        raise HTTPException(503, av["reason"] + "  - start it with:  docker compose up -d lab11")


def _heavy(fn, *a, **kw):
    if not L.HEAVY.acquire(blocking=False):
        raise HTTPException(409, "Another Lab 11 experiment is running - wait for it to finish.")
    try:
        return fn(*a, **kw)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except PyMongoError as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        L.HEAVY.release()


@router.get("/status")
def status():
    try:
        return _j(L.cluster_status())
    except PyMongoError as exc:
        return {"available": False, "reason": str(exc)[:200]}


@router.get("/results")
def results():
    doc = None
    try:
        doc = get_db().lab_results.find_one({"_id": "lab_11"})
    except PyMongoError:
        pass
    if not doc:
        p = RESULTS / "lab11_results.json"
        doc = json.loads(p.read_text()) if p.exists() else {}
    for c in (doc.get("lab_11_1") or {}).get("candidates", []):     # results saved by an older version
        if "cells" not in c:
            try:
                c["cells"] = L.matrix_cells(c)
            except (KeyError, TypeError):
                c["cells"] = {}
    return _j(doc)


@router.post("/eval")
def run_eval(p: dict = Body(default={})):
    """Lab 11.1 live (4,000-24,000 orders, about 5-30 s)."""
    _need_cluster()
    n = max(4000, min(int(p.get("orders", 6000)), 24000))
    lines: list[str] = []
    r = _heavy(L.evaluate_shard_keys, n, log=lines.append)
    return _j({**r, "log": lines})


@router.post("/hotspot")
def run_hotspot():
    """Lab 11.2 live: 10,000 documents into db.hotspot_data and db.even_data (about 5-10 s)."""
    _need_cluster()
    lines: list[str] = []
    r = _heavy(L.hotspot_experiment, log=lines.append)
    return _j({**r, "log": lines})


@router.post("/insert")
def insert(p: dict = Body(default={})):
    """Insert more documents (continuing the timeline) and show which shard received them."""
    _need_cluster()
    n = max(100, min(int(p.get("n", 1000)), 5000))
    return _j(_heavy(L.insert_more, p.get("collection", "hotspot_data"), n))


@router.get("/distribution")
def distribution(coll: str = Query("hotspot_data")):
    _need_cluster()
    if coll not in ("hotspot_data", "even_data"):
        raise HTTPException(422, "coll must be hotspot_data or even_data")
    counts = L.shard_counts(L.HOT_DB, coll)
    return _j({"collection": coll, "by_shard": counts, "chunks": L.chunk_map(L.HOT_DB, coll),
               "text": L.shard_distribution_text(L.HOT_DB, coll)})


@router.post("/explain")
def explain(p: dict = Body(default={})):
    _need_cluster()
    pid = str(p.get("preset", "H1"))
    if pid not in ("H1", "H2", "E1", "E2", "E3"):
        raise HTTPException(422, "preset must be H1, H2, E1, E2 or E3")
    try:
        return _j(L.explain_preset(pid))
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.post("/reshard")
def reshard_start(p: dict = Body(default={})):
    """Start the live resharding demo in the background (about 45-60 s); poll GET /api/lab11/reshard."""
    _need_cluster()
    if L.RESHARD_STATE.get("running"):
        raise HTTPException(409, "The resharding demo is already running.")
    if not L.HEAVY.acquire(blocking=False):
        raise HTTPException(409, "Another Lab 11 experiment is running - wait for it to finish.")
    n = max(4000, min(int(p.get("orders", 8000)), 20000))
    L.RESHARD_STATE.clear()
    L.RESHARD_STATE.update({"running": True, "phase": "starting", "log": []})

    def work():
        try:
            L.reshard_demo(n, log=L.RESHARD_STATE["log"].append, state=L.RESHARD_STATE)
        except Exception:                                 # noqa: BLE001 - state already holds the error
            pass
        finally:
            L.HEAVY.release()

    threading.Thread(target=work, daemon=True).start()
    return {"started": True, "orders": n}


@router.get("/reshard")
def reshard_state():
    return _j({**L.RESHARD_STATE, "steps": L.RESHARD_STEPS, "limits": L.RESHARD_LIMITS})


@router.get("/report")
def report():
    p = RESULTS / "Exercise-11_Report.pdf"
    if not p.exists():
        raise HTTPException(404, "The report has not been generated yet. Run: docker compose exec lab11 lab11 report")
    return FileResponse(p, media_type="application/pdf", filename="Exercise-11_Report.pdf")

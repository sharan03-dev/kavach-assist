"""REST API of the website's "Security lab 12" page. Mounted under /api by labs_api.py.

Every action uses the same functions as the command-line script (backend/app/lab12.py)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse
from pymongo.errors import PyMongoError

from . import lab12 as L
from .db import get_db

router = APIRouter(prefix="/lab12")
ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))


def _j(x):
    return json.loads(json.dumps(x, default=str))


def _need():
    av = L.available()
    if not av["available"]:
        raise HTTPException(503, av["reason"] + "  - start it with:  docker compose up -d lab12")


def _run(fn, *a, heavy=False, **kw):
    _need()
    if heavy and not L.HEAVY.acquire(blocking=False):
        raise HTTPException(409, "Another Lab 12 task is running - wait for it to finish.")
    try:
        return _j(fn(*a, **kw))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except (PyMongoError, RuntimeError) as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {str(exc)[:220]}")
    finally:
        if heavy:
            L.HEAVY.release()


@router.get("/status")
def status():
    try:
        return _j(L.status())
    except Exception as exc:                              # noqa: BLE001
        return {"available": False, "reason": str(exc)[:200]}


@router.get("/results")
def results():
    doc = None
    try:
        doc = get_db().lab_results.find_one({"_id": "lab_12"}, {"_id": 0})
    except PyMongoError:
        pass
    if not doc:
        p = RESULTS / "lab12_results.json"
        doc = json.loads(p.read_text()) if p.exists() else {}
    if doc and "threat_model" not in doc:
        doc["threat_model"] = L.threat_model(doc)
    return _j(doc or {})


@router.get("/threat-model")
def threat_model():
    return _j(L.threat_model(results()))


@router.post("/matrix")
def matrix(p: dict = Body(default={})):
    which = p.get("which", "kavach")
    lines: list[str] = []
    fn = L.matrix_kavach if which == "kavach" else L.matrix_121
    return {**_run(fn, log=lines.append, heavy=True), "log": lines}


@router.post("/redaction")
def redaction():
    lines: list[str] = []
    return {**_run(L.redaction, log=lines.append, heavy=True), "log": lines}


@router.post("/tls")
def tls():
    lines: list[str] = []
    return {**_run(L.tls_tests, log=lines.append, heavy=True), "log": lines}


@router.post("/integrity")
def integrity():
    return _run(L.integrity, log=lambda *_: None, heavy=True)


@router.post("/auth-hardening")
def auth_hardening():
    return _run(L.ip_allowlist, log=lambda *_: None, heavy=True)


@router.post("/csfle")
def csfle():
    return _run(L.csfle_demo, log=lambda *_: None)


@router.post("/audit")
def audit():
    return _run(L.audit, log=lambda *_: None, heavy=True)


@router.get("/feed")
def feed(n: int = 8):
    return _run(L.analyst_feed, max(1, min(n, 25)))


@router.post("/try")
def try_action(p: dict = Body(...)):
    return _run(L.try_action, str(p.get("persona", "")), str(p.get("action", "")), heavy=True)


@router.get("/actions")
def actions():
    return {"personas": L.PERSONAS, "kavach": [o[0] for o in L._kavach_ops()], "classroom": [o[0] for o in L._ops_121()]}


@router.post("/sync")
def sync():
    return _run(L.sync_secure, get_db(), heavy=True)


@router.get("/certs")
def certs():
    try:
        return _j(L.chain_info())
    except Exception as exc:                              # noqa: BLE001
        raise HTTPException(503, f"certificates not available: {str(exc)[:160]}")


@router.get("/report")
def report():
    p = RESULTS / "Exercise-12_Report.pdf"
    if not p.exists():
        raise HTTPException(404, "The report has not been generated yet. Run: docker compose exec lab12 lab12 report")
    return FileResponse(p, media_type="application/pdf", filename="Exercise-12_Report.pdf")

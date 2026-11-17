"""REST API of the website pages "Cassandra + Neo4j lab 16", "Vector search lab 17" and "Capstone lab 18" (mounted under /api)."""
from __future__ import annotations

import json
import threading

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse

from . import lab16 as L16
from . import lab17 as L17
from . import lab18 as L18
from . import polyglot as PG

router = APIRouter()
J = lambda x: json.loads(json.dumps(x, default=str))  # noqa: E731


async def _heavy(lock: threading.Lock, fn, *a, save=None, mod=None, **kw):
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Another task of this lab is running - wait for it to finish.")
    try:
        lines: list[str] = []
        out = await run_in_threadpool(fn, *a, log=lines.append, **kw)
        if save and mod:
            await run_in_threadpool(mod.save_part, save, out)
        return {**J(out), "log": lines}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)[:400])
    except Exception as exc:                          # noqa: BLE001
        raise HTTPException(500, f"{type(exc).__name__}: {str(exc)[:300]}")
    finally:
        lock.release()


async def _light(fn, *a, **kw):
    try:
        return J(await run_in_threadpool(fn, *a, **kw))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)[:400])
    except Exception as exc:                          # noqa: BLE001
        raise HTTPException(503, f"{type(exc).__name__}: {str(exc)[:300]}")


def _file(base, files, name):
    if name not in files:
        raise HTTPException(404, "unknown file")
    return PlainTextResponse((base / files[name]).read_text())


def _report(n):
    p = L16.RESULTS / f"Exercise-{n}_Report.pdf"
    if not p.exists():
        raise HTTPException(404, f"The report has not been generated yet. Run: docker compose exec lab{n} lab{n} all")
    return FileResponse(p, media_type="application/pdf", filename=f"Exercise-{n}_Report.pdf")


@router.get("/polyglot/health")
async def polyglot_health():
    return J(await run_in_threadpool(PG.availability, True))


# ============================================================================ Lab 16
@router.get("/lab16/status")
async def l16_status():
    return J(await run_in_threadpool(L16.status))


@router.get("/lab16/results")
async def l16_results():
    return await run_in_threadpool(L16.load_results)


@router.post("/lab16/run/{part}")
async def l16_run(part: str, p: dict = Body(default={})):
    q = bool(p.get("quick", False))
    fns = {"lab_16_1": lambda log: L16.run_161(q, log), "activity": lambda log: L16.run_activity(log),
           "lab_16_2": lambda log: L16.run_162(q, log), "applied": lambda log: L16.run_applied(log)}
    if part not in fns:
        raise HTTPException(404, "part must be lab_16_1, activity, lab_16_2 or applied")
    return await _heavy(L16.HEAVY, fns[part], save=part, mod=L16)


@router.post("/lab16/query")
async def l16_query(p: dict = Body(...)):
    name, arg = p.get("query"), str(p.get("arg", "")).strip()
    if name not in L16.QUERIES or not arg:
        raise HTTPException(422, "query must be one of " + ", ".join(L16.QUERIES) + " and arg is required")

    def run():
        out = {"query": name, "arg": arg, "db": {}}
        for dbn, fn in (("mongodb", L16.q_mongo), ("cassandra", L16.q_cass), ("neo4j", L16.q_neo)):
            r = PG.timed(lambda: fn(name, arg), runs=5)
            out["db"][dbn] = {"median_ms": r["median_ms"], "result": r["result"], "code": L16.QUERIES[name][dbn]}
        out["identical"] = out["db"]["mongodb"]["result"] == out["db"]["cassandra"]["result"] == out["db"]["neo4j"]["result"]
        return out
    return await _light(run)


@router.get("/lab16/prereqs/{course}")
async def l16_prereqs(course: str):
    return await _light(L16.prereq_chain, course)


@router.post("/lab16/enroll")
async def l16_enroll(p: dict = Body(...)):
    def run():
        r = L16.enroll(str(p.get("student", "")), str(p.get("course", "")), "website")
        r["audit"] = L16.audit_trail(str(p.get("student", "")), 10)
        return r
    return await _light(run)


@router.post("/lab16/complete")
async def l16_complete(p: dict = Body(...)):
    return await _light(lambda: (L16.complete(str(p["student"]), str(p["course"])), {"ok": True})[1])


_SVC: dict = {}


def _svc():
    if "s" not in _SVC:
        _SVC["s"] = L16.service()
    return _SVC["s"]


@router.get("/lab16/fraud/{user}")
async def l16_fraud(user: str, hops: int = 3):
    def run():
        s = _svc()
        r = s.analyze_user(user, hops)
        r["breakers"] = s.health()
        r["history"] = [h for b in s.breakers.values() for h in b.history][-12:]
        return r
    return await _light(run)


@router.post("/lab16/fault")
async def l16_fault(p: dict = Body(...)):
    b = p.get("backend")
    if b not in ("mongodb", "cassandra", "neo4j"):
        raise HTTPException(422, "backend must be mongodb, cassandra or neo4j")
    s = _svc()
    s.inject_fault(b, bool(p.get("on", True)))
    return {"faults": sorted(s.faults), "breakers": s.health()}


@router.get("/lab16/rings")
async def l16_rings():
    return await _light(lambda: (PG.mongo()[L16.FRAUD_DB].meta.find_one({"_id": "rings"}) or {}).get("rings", []))


L16_FILES = {"service": "fraud_service.py", "tests": "test_fraud_service.py", "cql": "schema.cql", "cypher": "schema.cypher",
             "queries": "queries.md", "runner": "run_lab16.py", "readme": "README.md"}


@router.get("/lab16/file/{name}")
def l16_file(name: str):
    return _file(L16.LAB, L16_FILES, name)


@router.get("/lab16/report")
def l16_report():
    return _report(16)


# ============================================================================ Lab 17
@router.get("/lab17/status")
async def l17_status():
    return J(await run_in_threadpool(L17.status))


@router.get("/lab17/results")
async def l17_results():
    return await run_in_threadpool(L17.load_results)


@router.post("/lab17/run/{part}")
async def l17_run(part: str, p: dict = Body(default={})):
    q = bool(p.get("quick", False))
    fns = {"lab_17_1": lambda log: L17.run_171(log), "memory": lambda log: L17.run_memory(log),
           "lab_17_2": lambda log: L17.run_172(q, log), "applied": lambda log: L17.run_applied(log)}
    if part not in fns:
        raise HTTPException(404, "part must be lab_17_1, memory, lab_17_2 or applied")
    return await _heavy(L17.HEAVY, fns[part], save=part, mod=L17)


@router.post("/lab17/ask")
async def l17_ask(p: dict = Body(...)):
    q = str(p.get("question", "")).strip()
    if not q:
        raise HTTPException(422, "question is required")
    return await _light(L17.ask, str(p.get("user_id") or "web-user")[:60], q[:500])


@router.get("/lab17/history/{user}")
async def l17_history(user: str):
    return await _light(L17.history, user, 5)


@router.post("/lab17/forget/{user}")
async def l17_forget(user: str):
    return await _light(lambda: {"deleted": L17.vclient()[L17.RAG_DB].conversations.delete_many({"user_id": user}).deleted_count})


@router.post("/lab17/retrieve")
async def l17_retrieve(p: dict = Body(...)):
    return await _light(L17.retrieve, str(p.get("query", ""))[:500], int(p.get("k", 3)), int(p.get("num_candidates", 20)))


@router.post("/lab17/kavach")
async def l17_kavach(p: dict = Body(...)):
    return await _light(L17.ask_kavach, str(p.get("question", ""))[:500])


@router.post("/lab17/search")
async def l17_search(p: dict = Body(...)):
    return await _light(L17.search_live, str(p.get("query", ""))[:300])


L17_FILES = {"pipeline": "../../backend/app/lab17.py", "ai": "../../backend/app/ai.py", "runner": "run_lab17.py", "readme": "README.md"}


@router.get("/lab17/file/{name}")
def l17_file(name: str):
    return _file(L17.LAB, L17_FILES, name)


@router.get("/lab17/doc/{name}")
def l17_doc(name: str):
    p = L17.DOCS / name
    if p.parent != L17.DOCS or not p.exists():
        raise HTTPException(404, "unknown document")
    return PlainTextResponse(p.read_text())


@router.get("/lab17/report")
def l17_report():
    return _report(17)


# ============================================================================ Lab 18
@router.get("/lab18/status")
async def l18_status():
    return J(await run_in_threadpool(L18.status))


@router.get("/lab18/results")
async def l18_results():
    return await run_in_threadpool(L18.load_results)


@router.post("/lab18/run")
async def l18_run(p: dict = Body(default={})):
    q = bool(p.get("quick", True))
    return await _heavy(L18.HEAVY, lambda log: L18.run_181(q, log), save="lab_18_1", mod=L18)


@router.get("/lab18/investigate/{indicator:path}")
async def l18_investigate(indicator: str):
    return await _light(L18.investigate, indicator)


@router.get("/lab18/history/{source:path}")
async def l18_history(source: str):
    return await _light(L18.history, source)


L18_FILES = {"cql": "schema.cql", "cypher": "schema.cypher", "runner": "run_lab18.py", "readme": "README.md", "logic": "../../backend/app/lab18.py"}


@router.get("/lab18/file/{name}")
def l18_file(name: str):
    return _file(L18.LAB, L18_FILES, name)


@router.get("/lab18/report")
def l18_report():
    return _report(18)

"""HTTP API of Kavach Assist (mounted into the Kavach website by backend/app/main.py).

    POST /chat                         the assignment's endpoint: {message, session_id} -> {answer, ...}   (signed-in users)
    POST /api/assist/chat/stream       the same turn as a Server-Sent-Events stream (tokens as they are generated)
    GET  /health                       container health check              GET /metrics   Prometheus text format
    /api/auth/*                        register, login (password / Google), logout, me, change password
    /api/assist/*                      status, threat board, conversations (memory), scanner findings, notifications
    /api/admin/*                       administrator only: users, usage metrics, knowledge base, scanner, audit log, settings
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pymongo.errors import PyMongoError

from .. import ai
from .. import db as core
from ..labs import to_jsonable as J
from . import auth, engine, knowledge, llm, metrics, ratelimit, threats
from . import settings as S
from . import store

root = APIRouter(tags=["assist"])
PUBLIC_PREFIXES = ("/api/auth/", "/api/health", "/api/lab14/webhook/")
_bg: dict = {"thread": None, "stop": threading.Event()}


def _iso(o):
    return o.isoformat() if hasattr(o, "isoformat") else str(o)


def is_public(path: str) -> bool:
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _set_cookie(response: Response, request: Request, user: dict) -> str:
    token = auth.make_token(user)
    response.set_cookie(S.COOKIE, token, max_age=S.TOKEN_HOURS * 3600, httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")
    return token


# ============================================================================ auth
@root.get("/api/auth/config")
def auth_config():
    try:
        open_, approval = store.setting("signup_open", True), store.setting("signup_needs_approval", False)
    except PyMongoError:
        open_, approval = True, False
    return {"google_client_id": S.GOOGLE_CLIENT_ID or None, "signup_open": open_, "signup_needs_approval": approval, "auth_required": S.AUTH_REQUIRED}


@root.post("/api/auth/register")
def register(request: Request, response: Response, body: dict = Body(...)):
    auth._throttle(f"register:{_ip(request)}", 10, 3600)
    user = auth.register(body.get("email", ""), body.get("password", ""), body.get("name", ""))
    if not user.get("approved", True):
        return {"pending": True, "detail": "Account created. The administrator has to approve it before you can sign in."}
    auth._finish_login(user, _ip(request))
    metrics.login_event("registered")
    return {"user": J(auth.public(user)), "token": _set_cookie(response, request, user)}


@root.post("/api/auth/login")
def login(request: Request, response: Response, body: dict = Body(...)):
    try:
        user = auth.login(body.get("email", ""), body.get("password", ""), _ip(request))
    except HTTPException as exc:
        metrics.login_event("failed" if exc.status_code == 401 else "refused")
        raise
    metrics.login_event("ok")
    return {"user": J(auth.public(user)), "token": _set_cookie(response, request, user)}


@root.post("/api/auth/google")
def login_google(request: Request, response: Response, body: dict = Body(...)):
    try:
        user = auth.login_google(body.get("credential", ""), _ip(request))
    except HTTPException as exc:
        metrics.login_event("google_failed" if exc.status_code == 401 else "refused")
        raise
    metrics.login_event("google_ok")
    return {"user": J(auth.public(user)), "token": _set_cookie(response, request, user)}


@root.post("/api/auth/logout")
def logout(request: Request, response: Response):
    user = auth.user_from_request(request)
    if user:
        store.audit("logout", actor=user["email"])
    response.delete_cookie(S.COOKIE, path="/")
    return {"ok": True}


@root.get("/api/auth/me")
def me(user: dict = Depends(auth.current_user)):
    return {"user": J(auth.public(user))}


@root.post("/api/auth/password")
def password(request: Request, response: Response, body: dict = Body(...), user: dict = Depends(auth.current_user)):
    auth.change_password(user, body.get("current", ""), body.get("new", ""))
    fresh = store.adb().users.find_one({"_id": user["_id"]})
    _set_cookie(response, request, fresh)
    return {"ok": True}


# ============================================================================ health + metrics (A.7)
@root.get("/health")
def health():
    out = {"status": "ok", "mongodb": False, "vector_store": None, "llm": None, "knowledge_chunks": None}
    try:
        core.client().admin.command("ping")
        out["mongodb"] = True
        out["vector_store"] = store.vector_where()
        out["knowledge_chunks"] = store.knowledge().estimated_document_count()
        out["threats"] = store.adb().threats.estimated_document_count()
    except Exception as exc:                           # noqa: BLE001
        out.update(status="unhealthy", error=f"{type(exc).__name__}")
        return JSONResponse(out, status_code=503)
    b = llm.backend()
    out["llm"] = {"provider": b["provider"], "model": b["model"], "note": b.get("note")}
    return out


@root.get("/metrics", response_class=PlainTextResponse)
def prom():
    return metrics.prometheus()


# ============================================================================ chat
def _check_rate(user: dict) -> None:
    r = ratelimit.hit(f"chat:{user['_id']}", S.CHAT_RATE_PER_MIN, 60)
    if not r["allowed"]:
        metrics.record(user, 429, 0, {"intent": "rate_limited"})
        raise HTTPException(429, f"Rate limit: {S.CHAT_RATE_PER_MIN} questions per minute. Try again in {r['retry_after']} seconds.",
                            headers={"Retry-After": str(r["retry_after"])})


def _message(body: dict) -> str:
    text = str(body.get("message") or "").strip()
    if not text:
        raise HTTPException(422, "The message is empty.")
    if len(text) > 2000:
        raise HTTPException(422, "The message is too long (2,000 characters at most).")
    return text


@root.post("/chat")
@root.post("/api/assist/chat")
def chat(body: dict = Body(...), user: dict = Depends(auth.current_user)):
    """{message, session_id?, platform?} -> {answer, conversation_id, sources, cards, provider, tokens, ms}"""
    text = _message(body)
    _check_rate(user)
    t0 = time.perf_counter()
    try:
        out = engine.chat(user, text, body.get("session_id") or body.get("conversation_id"), body.get("platform"))
    except PyMongoError as exc:
        metrics.record(user, 503, (time.perf_counter() - t0) * 1000, error=type(exc).__name__)
        raise HTTPException(503, "The database is not reachable right now. Try again in a few seconds.") from None
    except Exception as exc:                           # noqa: BLE001
        metrics.record(user, 500, (time.perf_counter() - t0) * 1000, error=f"{type(exc).__name__}: {str(exc)[:120]}")
        raise HTTPException(500, "Something went wrong while answering. It was logged.") from None
    metrics.record(user, 200, out.get("ms", 0), out)
    out.pop("type", None)
    return J(out)


@root.post("/api/assist/chat/stream")
def chat_stream(body: dict = Body(...), user: dict = Depends(auth.current_user)):
    text = _message(body)
    _check_rate(user)

    def gen():
        t0 = time.perf_counter()
        try:
            for ev in engine.chat_events(user, text, body.get("conversation_id") or body.get("session_id"), body.get("platform")):
                if ev["type"] == "done":
                    metrics.record(user, 200, ev.get("ms", 0), ev)
                yield f"data: {json.dumps(J(ev), default=_iso)}\n\n"
        except PyMongoError as exc:
            metrics.record(user, 503, (time.perf_counter() - t0) * 1000, error=type(exc).__name__)
            yield f"data: {json.dumps({'type': 'error', 'detail': 'The database is not reachable right now. Try again in a few seconds.'})}\n\n"
        except Exception as exc:                       # noqa: BLE001
            metrics.record(user, 500, (time.perf_counter() - t0) * 1000, error=f"{type(exc).__name__}: {str(exc)[:120]}")
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Something went wrong while answering. It was logged.'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Content-Encoding": "identity"})   # identity: never gzip-buffer the stream


@root.get("/api/assist/status")
def status(user: dict = Depends(auth.current_user)):
    b = llm.backend()
    e = ai.embedder()
    return J({"llm": b, "ollama": llm.ollama_status() if S.OLLAMA_URL else None, "embedder": {"provider": e.name, "model": getattr(e, "model", e.name), "dims": e.dims},
              "knowledge": knowledge.stats(), "bot": engine.bot_state(), "board": threats.board(None), "platforms": S.PLATFORMS, "statuses": S.STATUSES,
              "suggestions": engine.SUGGESTIONS, "rate_limit_per_min": S.CHAT_RATE_PER_MIN,
              "open_findings": store.adb().scan_findings.count_documents({"status": {"$in": ["open", "failed"]}})})


@root.get("/api/assist/board")
def board(platform: str | None = None, user: dict = Depends(auth.current_user)):
    return J(threats.board(platform if platform in S.PLATFORMS else None))


@root.get("/api/assist/threats")
def list_threats(platform: str | None = None, status: str | None = None, limit: int = 20, user: dict = Depends(auth.current_user)):
    items = threats.find(platform if platform in S.PLATFORMS else None, status if status in S.STATUSES else None, limit=max(1, min(limit, 50)))
    return J({"items": [engine.card(t) for t in items]})


@root.get("/api/assist/conversations")
def conversations(user: dict = Depends(auth.current_user)):
    rows = store.adb().conversations.find({"user_id": user["_id"]}, {"title": 1, "updated_at": 1, "messages": 1}).sort("updated_at", -1).limit(40)
    return J({"items": [{"id": r["_id"], "title": r.get("title"), "updated_at": r.get("updated_at"), "messages": r.get("messages", 0)} for r in rows]})


@root.get("/api/assist/conversations/{cid}")
def conversation(cid: str, user: dict = Depends(auth.current_user)):
    c = store.adb().conversations.find_one({"_id": cid, "user_id": user["_id"]})
    if not c:
        raise HTTPException(404, "Conversation not found.")
    msgs = list(store.adb().messages.find({"conversation_id": cid}).sort("ts", 1).limit(400))
    return J({"id": cid, "title": c.get("title"), "pending": bool(c.get("pending")),
              "messages": [{"id": m["_id"], "role": m["role"], "content": m["content"], "ts": m["ts"], "meta": m.get("meta") or {}} for m in msgs]})


@root.delete("/api/assist/conversations/{cid}")
def delete_conversation(cid: str, user: dict = Depends(auth.current_user)):
    d = store.adb()
    if not d.conversations.delete_one({"_id": cid, "user_id": user["_id"]}).deleted_count:
        raise HTTPException(404, "Conversation not found.")
    d.messages.delete_many({"conversation_id": cid})
    return {"ok": True}


@root.get("/api/assist/notifications")
def notifications(since: str | None = None, user: dict = Depends(auth.current_user)):
    q = {}
    if since:
        try:
            from datetime import datetime
            q["ts"] = {"$gt": datetime.fromisoformat(since.replace("Z", "+00:00"))}
        except ValueError:
            pass
    rows = list(store.adb().notifications.find(q).sort("ts", -1).limit(5))
    return J({"items": rows[::-1], "bot": engine.bot_state()})


@root.get("/api/assist/findings")
def findings(user: dict = Depends(auth.current_user)):
    return J({"items": [engine._fpub(f) for f in engine._open_findings()], "bot": engine.bot_state()})


# ============================================================================ administrator
admin = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(auth.admin_user)])


@admin.get("/overview")
def admin_overview():
    d = store.adb()
    return J({"users": d.users.count_documents({}), "active_users": d.users.count_documents({"active": True, "approved": True}),
              "waiting_approval": d.users.count_documents({"approved": False}), "google_users": d.users.count_documents({"provider": "google"}),
              "conversations": d.conversations.estimated_document_count(), "messages": d.messages.estimated_document_count(),
              "threats": d.threats.estimated_document_count(), "open_findings": d.scan_findings.count_documents({"status": {"$in": ["open", "failed"]}}),
              "llm": llm.backend(), "bot": engine.bot_state(), "metrics": metrics.summary(24), "google_configured": bool(S.GOOGLE_CLIENT_ID),
              "settings": {"signup_open": store.setting("signup_open", True), "signup_needs_approval": store.setting("signup_needs_approval", False)}})


@admin.get("/users")
def admin_users():
    d = store.adb()
    usage = {x["_id"]: x for x in d.messages.aggregate([{"$match": {"role": "user"}}, {"$group": {"_id": "$user_id", "questions": {"$sum": 1}, "last": {"$max": "$ts"}}}])}
    rows = []
    for u in d.users.find({}, {"password": 0}).sort([("role", 1), ("created_at", 1)]):
        rows.append({**auth.public(u), "logins": u.get("logins", 0), "questions": (usage.get(u["_id"]) or {}).get("questions", 0),
                     "last_question": (usage.get(u["_id"]) or {}).get("last")})
    return J({"items": rows})


def _target(uid: str, me: dict) -> dict:
    u = store.adb().users.find_one({"_id": uid})
    if not u:
        raise HTTPException(404, "User not found.")
    if u["_id"] == me["_id"] or u.get("role") == "admin":
        raise HTTPException(400, "The administrator account cannot be changed here.")
    return u


@admin.patch("/users/{uid}")
def admin_update_user(uid: str, body: dict = Body(...), me: dict = Depends(auth.admin_user)):
    u = _target(uid, me)
    upd = {k: bool(body[k]) for k in ("active", "approved") if k in body}
    if isinstance(body.get("name"), str) and body["name"].strip():
        upd["name"] = body["name"].strip()[:80]
    if not upd:
        raise HTTPException(422, "Nothing to change.")
    inc = {"session_version": 1} if upd.get("active") is False else {}
    store.adb().users.update_one({"_id": uid}, {"$set": upd, **({"$inc": inc} if inc else {})})
    store.audit("admin.user.updated", actor=me["email"], user=u["email"], changes=upd)
    return J({"user": auth.public(store.adb().users.find_one({"_id": uid}))})


@admin.post("/users/{uid}/reset-password")
def admin_reset_password(uid: str, me: dict = Depends(auth.admin_user)):
    u = _target(uid, me)
    temp = secrets.token_urlsafe(8) + "4k"
    store.adb().users.update_one({"_id": uid}, {"$set": {"password": auth.hash_password(temp)}, "$inc": {"session_version": 1}})
    store.audit("admin.user.password_reset", actor=me["email"], user=u["email"])
    return {"temporary_password": temp, "note": "Shown once. The user is signed out everywhere and should change it after signing in."}


@admin.post("/users/{uid}/signout")
def admin_signout(uid: str, me: dict = Depends(auth.admin_user)):
    u = _target(uid, me)
    store.adb().users.update_one({"_id": uid}, {"$inc": {"session_version": 1}})
    store.audit("admin.user.signed_out", actor=me["email"], user=u["email"])
    return {"ok": True}


@admin.delete("/users/{uid}")
def admin_delete_user(uid: str, me: dict = Depends(auth.admin_user)):
    u = _target(uid, me)
    d = store.adb()
    convs = [c["_id"] for c in d.conversations.find({"user_id": uid}, {"_id": 1})]
    d.messages.delete_many({"conversation_id": {"$in": convs}})
    d.conversations.delete_many({"user_id": uid})
    d.users.delete_one({"_id": uid})
    store.audit("admin.user.deleted", actor=me["email"], user=u["email"], conversations=len(convs))
    return {"ok": True}


@admin.post("/users")
def admin_create_user(body: dict = Body(...), me: dict = Depends(auth.admin_user)):
    was = store.setting("signup_open", True)
    store.set_setting("signup_open", True)
    try:
        u = auth.register(body.get("email", ""), body.get("password", ""), body.get("name", ""))
    finally:
        store.set_setting("signup_open", was)
    store.adb().users.update_one({"_id": u["_id"]}, {"$set": {"approved": True}})
    store.audit("admin.user.created", actor=me["email"], user=u["email"])
    return J({"user": auth.public({**u, "approved": True})})


@admin.get("/metrics")
def admin_metrics(hours: int = 24):
    return J(metrics.summary(max(1, min(hours, 24 * 14))))


@admin.get("/audit")
def admin_audit(limit: int = 100):
    return J({"items": list(store.adb().audit.find({}, {"_id": 0}).sort("ts", -1).limit(max(1, min(limit, 500))))})


@admin.get("/knowledge")
def admin_knowledge():
    return J({**knowledge.stats(), "threats_built": store.setting("threats_built"), "embedder": ai.embedder_info()})


@admin.post("/knowledge/ingest")
def admin_ingest(me: dict = Depends(auth.admin_user)):
    store.audit("admin.knowledge.ingest", actor=me["email"])
    threading.Thread(target=knowledge.ingest, daemon=True).start()
    return {"started": True}


@admin.post("/settings")
def admin_settings(body: dict = Body(...), me: dict = Depends(auth.admin_user)):
    changed = {}
    for k in ("signup_open", "signup_needs_approval"):
        if k in body:
            store.set_setting(k, bool(body[k]))
            changed[k] = bool(body[k])
    store.audit("admin.settings", actor=me["email"], changes=changed)
    return {"signup_open": store.setting("signup_open", True), "signup_needs_approval": store.setting("signup_needs_approval", False)}


@admin.get("/scanner")
def admin_scanner():
    d = store.adb()
    return J({"bot": engine.bot_state(), "runs": list(d.scan_runs.find({}).sort("at", -1).limit(15)),
              "jobs": list(d.scan_jobs.find({}).sort("created_at", -1).limit(15)),
              "findings": list(d.scan_findings.find({}, {"fix": 0}).sort([("status", 1), ("severity_rank", -1)]).limit(120))})


root.include_router(admin)


# ============================================================================ lifecycle
def _background(log=print):
    """Start-up work that must not block the website: indexes, the administrator, then the knowledge base (kept fresh)."""
    stop = _bg["stop"]
    while not stop.is_set():
        try:
            store.ensure_indexes()
            auth.ensure_admin(log=log)
            break
        except Exception as exc:                       # noqa: BLE001 - MongoDB still starting
            log(f"[assist] waiting for MongoDB: {type(exc).__name__}")
            stop.wait(5)
    if os.getenv("ASSIST_INGEST", "1") in ("0", "false", "no"):
        return
    refresh = int(os.getenv("ASSIST_REFRESH_MIN", "30")) * 60
    while not stop.is_set():
        wait = refresh
        try:
            res = knowledge.ingest(log=log)
            if not (res.get("threats") or {}).get("vulnerabilities") and not (res.get("threats") or {}).get("reports"):
                wait = 60                              # the Kavach database is still being built: look again soon
            if S.OLLAMA_URL:
                llm.ollama_status(force=True)          # starts the model download on first start
        except Exception as exc:                       # noqa: BLE001
            log(f"[assist] ingestion problem: {type(exc).__name__}: {str(exc)[:160]}")
            wait = 60
        stop.wait(wait)


def startup(log=print) -> None:
    if _bg["thread"] is None:
        _bg["stop"].clear()
        _bg["thread"] = threading.Thread(target=_background, kwargs={"log": log}, daemon=True, name="assist-background")
        _bg["thread"].start()


def shutdown() -> None:
    """Graceful shutdown: stop the background work, close the HTTP and MongoDB connection pools."""
    _bg["stop"].set()
    llm.close()
    store.close()


async def gate(request: Request, call_next):
    """Every /api route of the website needs a signed-in user (except sign-in itself, health and internal webhooks)."""
    path = request.url.path
    if S.AUTH_REQUIRED and path.startswith("/api/") and not is_public(path) and request.method != "OPTIONS":
        try:
            user = await run_in_threadpool(auth.user_from_request, request)
        except PyMongoError:
            return JSONResponse({"detail": "The database is not reachable right now."}, status_code=503)
        if not user:
            return JSONResponse({"detail": "Please sign in."}, status_code=401)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response

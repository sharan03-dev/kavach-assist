"""Lab 15.1 - TaskTracker API: a multi-tenant task-management SaaS on FastAPI + MongoDB.

    uvicorn tasktracker.app:app --port 8100          # standalone (MONGO_URI from the environment, like the sample)
    http://localhost:8000/tasktracker/docs            # inside the Kavach deployment (mounted by backend/app/main.py)

Instructions -> code
  1. schema with tenant isolation   -> init_db(): tenants, users, tasks (JSON-schema validators), compound indexes
                                        (tenant_id, created_at) and (tenant_id, owner_id, created_at); unique (tenant_id, email)
  2. CRUD with tenant_id filtering   -> every query is built by scoped(): {"tenant_id": <from the JWT>, ...}. The tenant
     on every query                     NEVER comes from the URL or the body (the sample takes ?tenant_id= - see README).
  3. rate limiting, MongoDB as the   -> rate_limit(): one counter document per tenant per minute, find_one_and_update
     counter store                      (findAndModify) with $inc + upsert, TTL index on `ts` removes old windows.
  Activity: RBAC                     -> role in the user document (admin | member) and in the JWT; admins manage every
                                        task of their tenant, members read the team board but change only their own tasks.

The endpoints are plain `def` (not `async def`): PyMongo is synchronous, and FastAPI runs `def` endpoints in a thread pool.
An `async def` endpoint that calls PyMongo blocks the whole event loop (the sample does this).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import jwt
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import CollectionInvalid, DuplicateKeyError, OperationFailure, PyMongoError

UTC = timezone.utc
app = FastAPI(title="TaskTracker API", version="1.5",
              description="Lab 15 - multi-tenant task SaaS. Log in with POST /auth/login, then press Authorize and paste the token.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "Retry-After"])

PLANS = {"free": 60, "pro": 600, "enterprise": 6000}          # requests per minute per tenant
WINDOW_S = 60
TOKEN_TTL = timedelta(hours=8)
_cfg = {"uri": None, "db": os.getenv("TASKTRACKER_DB", "tasktracker"), "client": None, "secret": None}


# ============================================================================ connection
def configure(uri: str | None = None, db: str | None = None):
    """The Kavach deployment injects the production URI (least-privilege user tasktracker_app); standalone: MONGO_URI."""
    if _cfg["client"]:
        _cfg["client"].close()
    _cfg.update(uri=uri, client=None, secret=None)
    if db:
        _cfg["db"] = db


def get_db():
    if _cfg["client"] is None:
        uri = _cfg["uri"] or os.getenv("TASKTRACKER_MONGO_URI") or os.getenv("MONGO_URI", "mongodb://localhost:27017")
        _cfg["client"] = MongoClient(uri() if callable(uri) else uri, serverSelectionTimeoutMS=5000, w="majority",
                                     appname="tasktracker", tz_aware=True)
    return _cfg["client"][_cfg["db"]]


# ============================================================================ 1. schema
TASK_SCHEMA = {"bsonType": "object", "required": ["tenant_id", "owner_id", "title", "priority", "status", "created_at"],
               "properties": {"tenant_id": {"bsonType": "string", "minLength": 1},
                              "owner_id": {"bsonType": "string"},
                              "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
                              "description": {"bsonType": "string", "maxLength": 5000},
                              "priority": {"enum": ["low", "medium", "high"]},
                              "status": {"enum": ["open", "in_progress", "done"]},
                              "created_at": {"bsonType": "date"}, "updated_at": {"bsonType": "date"}}}
USER_SCHEMA = {"bsonType": "object", "required": ["tenant_id", "email", "role", "pw"],
               "properties": {"tenant_id": {"bsonType": "string"}, "email": {"bsonType": "string"},
                              "role": {"enum": ["admin", "member"]}, "pw": {"bsonType": "string"}}}
TENANT_SCHEMA = {"bsonType": "object", "required": ["_id", "name", "plan"],
                 "properties": {"_id": {"bsonType": "string", "pattern": "^[a-z0-9-]{2,32}$"}, "plan": {"enum": list(PLANS)}}}


def init_db(db=None) -> dict:
    db = db if db is not None else get_db()
    for name, schema in (("tasks", TASK_SCHEMA), ("users", USER_SCHEMA), ("tenants", TENANT_SCHEMA)):
        try:
            db.create_collection(name, validator={"$jsonSchema": schema}, validationAction="error")
        except (CollectionInvalid, OperationFailure):
            db.command("collMod", name, validator={"$jsonSchema": schema}, validationAction="error")
    ix = {
        "tasks": [db.tasks.create_index([("tenant_id", ASCENDING), ("created_at", DESCENDING)], name="tenant_created"),
                  db.tasks.create_index([("tenant_id", ASCENDING), ("owner_id", ASCENDING), ("created_at", DESCENDING)],
                                        name="tenant_owner_created")],
        "users": [db.users.create_index([("tenant_id", ASCENDING), ("email", ASCENDING)], unique=True, name="tenant_email")],
        "rate_limits": [db.rate_limits.create_index("ts", expireAfterSeconds=2 * WINDOW_S, name="ttl_ts")],
    }
    if not db.settings.find_one({"_id": "jwt"}):                    # signing key, created once (production: a secret manager)
        try:
            db.settings.insert_one({"_id": "jwt", "secret": secrets.token_hex(32), "created_at": datetime.now(UTC)})
        except DuplicateKeyError:
            pass
    return ix


# ============================================================================ passwords + JWT
def hash_pw(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    return salt.hex() + "$" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000).hex()


def check_pw(password: str, stored: str) -> bool:
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_pw(password, bytes.fromhex(salt)), stored)


def _secret() -> str:
    if _cfg["secret"] is None:
        doc = get_db().settings.find_one({"_id": "jwt"})
        if not doc:
            init_db()
            doc = get_db().settings.find_one({"_id": "jwt"})
        _cfg["secret"] = doc["secret"]
    return _cfg["secret"]


def make_token(user: dict) -> str:
    now = datetime.now(UTC)
    return jwt.encode({"sub": str(user["_id"]), "tenant_id": user["tenant_id"], "role": user["role"], "email": user["email"],
                       "iat": now, "exp": now + TOKEN_TTL}, _secret(), algorithm="HS256")


bearer = HTTPBearer(auto_error=False)


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    """The FastAPI dependency that extracts tenant_id from the JWT (solution text). The user is re-read from MongoDB, so a
    deleted user or a changed role takes effect immediately, and the token's tenant must match the stored one."""
    if cred is None:
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = jwt.decode(cred.credentials, _secret(), algorithms=["HS256"], options={"require": ["exp", "sub", "tenant_id"]})
        u = get_db().users.find_one({"_id": ObjectId(claims["sub"]), "tenant_id": claims["tenant_id"]}, {"pw": 0})
    except (jwt.PyJWTError, InvalidId) as exc:
        raise HTTPException(401, f"invalid token: {type(exc).__name__}", headers={"WWW-Authenticate": "Bearer"})
    if not u:
        raise HTTPException(401, "user no longer exists", headers={"WWW-Authenticate": "Bearer"})
    return {"id": str(u["_id"]), "tenant_id": u["tenant_id"], "role": u["role"], "email": u["email"], "name": u.get("name", "")}


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "admin role required")
    return user


# ============================================================================ 3. rate limiting (MongoDB counter store)
def check_rate(tenant_id: str, limit: int, now: float | None = None) -> dict:
    """Fixed one-minute window per tenant: findAndModify({_id: tenant:window}, {$inc: {count: 1}}, upsert, new=true).
    The increment and the read are ONE atomic operation, so concurrent requests can never both see the same count."""
    now = now or time.time()
    win = int(now // WINDOW_S) * WINDOW_S
    db = get_db()
    for _ in range(2):                         # two first requests of a window can race on the upsert: retry once
        try:
            doc = db.rate_limits.find_one_and_update(
                {"_id": f"{tenant_id}:{win}"},
                {"$inc": {"count": 1}, "$setOnInsert": {"tenant_id": tenant_id, "window": win, "limit": limit,
                                                        "ts": datetime.fromtimestamp(win, UTC)}},
                upsert=True, return_document=ReturnDocument.AFTER)
            break
        except DuplicateKeyError:
            continue
    return {"count": doc["count"], "limit": limit, "remaining": max(0, limit - doc["count"]), "reset": win + WINDOW_S,
            "allowed": doc["count"] <= limit}


_plans: dict = {}


def tenant_limit(tenant_id: str) -> int:
    hit = _plans.get(tenant_id)
    if hit and hit[1] > time.time():
        return hit[0]
    t = get_db().tenants.find_one({"_id": tenant_id}, {"plan": 1, "rate_limit": 1}) or {}
    lim = int(t.get("rate_limit") or PLANS.get(t.get("plan", "free"), 60))
    _plans[tenant_id] = (lim, time.time() + 10)
    return lim


def rate_limit(response: Response, user: dict = Depends(current_user)) -> dict:
    r = check_rate(user["tenant_id"], tenant_limit(user["tenant_id"]))
    hdr = {"X-RateLimit-Limit": str(r["limit"]), "X-RateLimit-Remaining": str(r["remaining"]), "X-RateLimit-Reset": str(r["reset"])}
    if not r["allowed"]:
        raise HTTPException(429, f"rate limit of {r['limit']} requests/minute exceeded for tenant {user['tenant_id']}",
                            headers={**hdr, "Retry-After": str(max(1, int(r["reset"] - time.time())))})
    response.headers.update(hdr)
    return user


# ============================================================================ 2. models + tenant-scoped queries
class Login(BaseModel):
    tenant_id: str
    email: str
    password: str


class Signup(BaseModel):
    tenant_id: str = Field(pattern=r"^[a-z0-9-]{2,32}$")
    tenant_name: str = Field(min_length=2, max_length=80)
    plan: Literal["free", "pro", "enterprise"] = "free"
    admin_email: str
    admin_name: str = ""
    password: str = Field(min_length=8)


class UserCreate(BaseModel):
    email: str
    name: str = ""
    password: str = Field(min_length=8)
    role: Literal["admin", "member"] = "member"


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    priority: Literal["low", "medium", "high"] = "medium"


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    priority: Optional[Literal["low", "medium", "high"]] = None
    status: Optional[Literal["open", "in_progress", "done"]] = None


class TaskResponse(TaskCreate):
    id: str
    tenant_id: str
    owner_id: str
    owner: str = ""
    status: str
    created_at: str
    updated_at: Optional[str] = None
    can_edit: bool = False


def scoped(user: dict, extra: dict | None = None, write: bool = False) -> dict:
    """THE tenant filter: every query of the API goes through here. tenant_id comes from the verified JWT only.
    Writes by a member are also restricted to the member's own tasks (RBAC activity)."""
    q = {"tenant_id": user["tenant_id"], **(extra or {})}
    if write and user["role"] != "admin":
        q["owner_id"] = user["id"]
    return q


def _oid(task_id: str) -> ObjectId:
    try:
        return ObjectId(task_id)
    except (InvalidId, TypeError):
        raise HTTPException(404, "task not found")


def out(t: dict, user: dict) -> dict:
    return {"id": str(t["_id"]), "tenant_id": t["tenant_id"], "owner_id": t["owner_id"], "owner": t.get("owner", ""),
            "title": t["title"], "description": t.get("description", ""), "priority": t["priority"], "status": t["status"],
            "created_at": t["created_at"].isoformat(), "updated_at": t["updated_at"].isoformat() if t.get("updated_at") else None,
            "can_edit": user["role"] == "admin" or t["owner_id"] == user["id"]}


def _deny_or_404(user: dict, oid: ObjectId):
    """Same tenant but not the owner -> 403. Other tenant or no such task -> 404 (never reveal another tenant's ids)."""
    if get_db().tasks.find_one(scoped(user, {"_id": oid}), {"_id": 1}):
        raise HTTPException(403, "members can only change their own tasks")
    raise HTTPException(404, "task not found")


# ============================================================================ endpoints
@app.get("/health")
def health():
    get_db().command("ping")
    return {"ok": True, "db": _cfg["db"]}


@app.post("/auth/login")
def login(body: Login):
    u = get_db().users.find_one({"tenant_id": body.tenant_id, "email": body.email.lower()})
    if not u or not check_pw(body.password, u["pw"]):
        raise HTTPException(401, "wrong tenant, email or password")
    return {"access_token": make_token(u), "token_type": "bearer", "tenant_id": u["tenant_id"], "role": u["role"],
            "name": u.get("name", ""), "expires_in": int(TOKEN_TTL.total_seconds())}


@app.post("/auth/signup", status_code=201)
def signup(body: Signup):
    """New tenant + its first admin in ONE multi-document transaction (Lab 10): either both exist or neither."""
    db = get_db()
    now = datetime.now(UTC)
    with db.client.start_session() as s:
        try:
            with s.start_transaction():
                db.tenants.insert_one({"_id": body.tenant_id, "name": body.tenant_name, "plan": body.plan, "created_at": now},
                                      session=s)
                r = db.users.insert_one({"tenant_id": body.tenant_id, "email": body.admin_email.lower(), "name": body.admin_name,
                                         "role": "admin", "pw": hash_pw(body.password), "created_at": now}, session=s)
        except DuplicateKeyError:
            raise HTTPException(409, f"tenant {body.tenant_id} already exists")
    return {"tenant_id": body.tenant_id, "admin_id": str(r.inserted_id), "plan": body.plan, "rate_limit_per_min": PLANS[body.plan]}


@app.get("/me")
def me(user: dict = Depends(rate_limit)):
    t = get_db().tenants.find_one({"_id": user["tenant_id"]}) or {}
    return {**user, "tenant_name": t.get("name"), "plan": t.get("plan"), "rate_limit_per_min": tenant_limit(user["tenant_id"])}


@app.post("/tasks/", response_model=TaskResponse, status_code=201)
def create_task(task: TaskCreate, user: dict = Depends(rate_limit)):
    now = datetime.now(UTC)
    doc = {"tenant_id": user["tenant_id"], "owner_id": user["id"], "owner": user["name"] or user["email"], **task.model_dump(),
           "status": "open", "created_at": now}
    r = get_db().tasks.insert_one(doc)
    doc["_id"] = r.inserted_id
    return out(doc, user)


@app.get("/tasks/", response_model=list[TaskResponse])
def list_tasks(status_: Optional[Literal["open", "in_progress", "done"]] = Query(None, alias="status"), mine: bool = False, limit: int = 50,
               skip: int = 0, user: dict = Depends(rate_limit)):
    extra = {}
    if status_:
        extra["status"] = status_
    if mine:
        extra["owner_id"] = user["id"]
    cur = get_db().tasks.find(scoped(user, extra)).sort("created_at", DESCENDING).skip(max(0, skip)).limit(max(1, min(limit, 200)))
    return [out(t, user) for t in cur]


@app.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, user: dict = Depends(rate_limit)):
    t = get_db().tasks.find_one(scoped(user, {"_id": _oid(task_id)}))
    if not t:
        raise HTTPException(404, "task not found")
    return out(t, user)


@app.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(task_id: str, patch: TaskUpdate, user: dict = Depends(rate_limit)):
    oid = _oid(task_id)
    changes = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(422, "nothing to change")
    t = get_db().tasks.find_one_and_update(scoped(user, {"_id": oid}, write=True),
                                           {"$set": {**changes, "updated_at": datetime.now(UTC)}},
                                           return_document=ReturnDocument.AFTER)
    if not t:
        _deny_or_404(user, oid)
    return out(t, user)


@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: str, user: dict = Depends(rate_limit)):
    oid = _oid(task_id)
    if get_db().tasks.delete_one(scoped(user, {"_id": oid}, write=True)).deleted_count == 0:
        _deny_or_404(user, oid)
    return Response(status_code=204)


@app.get("/stats")
def stats(user: dict = Depends(rate_limit)):
    rows = get_db().tasks.aggregate([{"$match": scoped(user)},
                                     {"$group": {"_id": {"status": "$status", "priority": "$priority"}, "n": {"$sum": 1}}}])
    out_ = {"by_status": {}, "by_priority": {}, "total": 0}
    for r in rows:
        out_["by_status"][r["_id"]["status"]] = out_["by_status"].get(r["_id"]["status"], 0) + r["n"]
        out_["by_priority"][r["_id"]["priority"]] = out_["by_priority"].get(r["_id"]["priority"], 0) + r["n"]
        out_["total"] += r["n"]
    return out_


@app.get("/users/")
def list_users(user: dict = Depends(require_admin), _: dict = Depends(rate_limit)):
    return [{"id": str(u["_id"]), "email": u["email"], "name": u.get("name", ""), "role": u["role"]}
            for u in get_db().users.find(scoped(user), {"pw": 0}).sort("email", 1)]


@app.post("/users/", status_code=201)
def add_user(body: UserCreate, user: dict = Depends(require_admin), _: dict = Depends(rate_limit)):
    try:
        r = get_db().users.insert_one({"tenant_id": user["tenant_id"], "email": body.email.lower(), "name": body.name,
                                       "role": body.role, "pw": hash_pw(body.password), "created_at": datetime.now(UTC)})
    except DuplicateKeyError:
        raise HTTPException(409, "email already used in this tenant")
    return {"id": str(r.inserted_id), "email": body.email.lower(), "role": body.role}


@app.exception_handler(PyMongoError)
def mongo_error(request: Request, exc: PyMongoError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": f"database error: {type(exc).__name__}"}, status_code=503)


@app.exception_handler(RuntimeError)
def not_ready(request: Request, exc: RuntimeError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(exc)}, status_code=503)

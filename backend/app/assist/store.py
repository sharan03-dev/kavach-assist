"""MongoDB handles of Kavach Assist.

    operational database + conversation memory   replica set rs0, database kavach_assist  (w:majority - Labs 8 and 9)
    vector store                                 MongoDB Atlas Local (VECTOR_URI), collection knowledge + vectorSearch index
                                                 (falls back to rs0 + exact NumPy search when Atlas Local is not reachable)
    threat intelligence                          the Kavach platform database cti_platform (read only)
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.write_concern import WriteConcern

from .. import db as core
from . import settings as S

UTC = timezone.utc
_lock = threading.Lock()
_state: dict = {"vec": None, "vec_checked": 0.0, "vec_ok": False, "indexes": False}


def now() -> datetime:
    return datetime.now(UTC)


def adb():
    """Operational database: users, conversations, messages, metrics, scans."""
    return core.client().get_database(S.ASSIST_DB, write_concern=WriteConcern(w="majority", wtimeout=8000))


def cti():
    return core.client()[S.CTI_DB]


def _vec_client():
    if _state["vec"] is None and S.VECTOR_URI:
        _state["vec"] = MongoClient(S.VECTOR_URI, serverSelectionTimeoutMS=3000, tz_aware=True, appname="kavach-assist-vector")
    return _state["vec"]


def vector_ok(force: bool = False) -> bool:
    """Is the Atlas Local vector server reachable? (cached for 30 s)"""
    with _lock:
        if not S.VECTOR_URI:
            return False
        if not force and time.time() - _state["vec_checked"] < 30:
            return _state["vec_ok"]
        try:
            _vec_client().admin.command("ping")
            _state["vec_ok"] = True
        except Exception:                              # noqa: BLE001
            _state["vec_ok"] = False
        _state["vec_checked"] = time.time()
        return _state["vec_ok"]


def kdb():
    """Database that holds the knowledge chunks + embeddings (the vector store)."""
    return _vec_client()[S.ASSIST_DB] if vector_ok() else core.client()[S.ASSIST_DB]


def knowledge():
    return kdb()["knowledge"]


def vector_where() -> str:
    return "atlas-local" if vector_ok() else "rs0"


def _ix(coll, keys, **kw):
    try:
        coll.create_index(keys, **kw)
    except OperationFailure as exc:
        if exc.code not in (85, 86):
            raise


def ensure_indexes() -> None:
    """Indexes behind every query of the chat application (Exercises 5 and 6). Safe to call many times."""
    d = adb()
    _ix(d.users, [("email", ASCENDING)], unique=True)
    _ix(d.users, [("role", ASCENDING), ("created_at", DESCENDING)])
    _ix(d.conversations, [("user_id", ASCENDING), ("updated_at", DESCENDING)])
    _ix(d.messages, [("conversation_id", ASCENDING), ("ts", ASCENDING)])
    _ix(d.messages, [("session_id", ASCENDING), ("ts", DESCENDING)])
    _ix(d.threats, [("platforms", ASCENDING), ("status", ASCENDING), ("priority", DESCENDING)])
    _ix(d.threats, [("status", ASCENDING), ("priority", DESCENDING)])
    _ix(d.threats, [("kind", ASCENDING), ("status", ASCENDING)])
    _ix(d.threats, [("title", "text"), ("description", "text"), ("vendor", "text"), ("product", "text")],
        weights={"title": 8, "product": 6, "vendor": 4, "description": 1}, name="threat_text", default_language="english")
    _ix(d.rate_limits, [("expires_at", ASCENDING)], expireAfterSeconds=0)                  # TTL
    _ix(d.metrics, [("ts", ASCENDING)], expireAfterSeconds=14 * 86400)                     # TTL: 14 days of request metrics
    _ix(d.metrics, [("user_id", ASCENDING), ("ts", DESCENDING)])
    _ix(d.audit, [("ts", DESCENDING)])
    _ix(d.audit, [("ts", ASCENDING)], expireAfterSeconds=90 * 86400, name="audit_ttl")
    _ix(d.scan_findings, [("status", ASCENDING), ("severity_rank", DESCENDING)])
    _ix(d.scan_jobs, [("status", ASCENDING), ("created_at", ASCENDING)])
    _ix(d.notifications, [("ts", DESCENDING)])
    _ix(d.notifications, [("ts", ASCENDING)], expireAfterSeconds=7 * 86400, name="notif_ttl")
    try:
        k = knowledge()
        _ix(k, [("kind", ASCENDING)])
        _ix(k, [("platforms", ASCENDING), ("status", ASCENDING)])
        _ix(k, [("lab", ASCENDING)], sparse=True)
        _ix(k, [("title", "text"), ("text", "text")], weights={"title": 6, "text": 1}, name="knowledge_text", default_language="english")
    except PyMongoError:
        pass
    _state["indexes"] = True


def audit(action: str, actor: str | None = None, **detail) -> None:
    try:
        adb().audit.insert_one({"ts": now(), "action": action, "actor": actor, **detail})
    except PyMongoError:
        pass


def setting(key: str, default=None):
    doc = adb().settings.find_one({"_id": key})
    return doc["value"] if doc else default


def set_setting(key: str, value) -> None:
    adb().settings.update_one({"_id": key}, {"$set": {"value": value, "updated_at": now()}}, upsert=True)


def close() -> None:
    """Graceful shutdown: return every pooled connection (A.7 lifecycle)."""
    with _lock:
        if _state["vec"] is not None:
            try:
                _state["vec"].close()
            finally:
                _state["vec"] = None
    try:
        core.client().close()
    except Exception:                                  # noqa: BLE001
        pass

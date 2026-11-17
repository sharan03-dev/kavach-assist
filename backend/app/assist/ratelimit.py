"""Rate limiting with one atomic MongoDB counter per (key, time window) - the Lab 15 technique.

find_one_and_update + $inc is atomic, so the limit also holds with several web processes; a TTL index removes old windows.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import store


def hit(key: str, limit: int, window_s: int = 60) -> dict:
    now = time.time()
    window = int(now // window_s)
    _id = f"{key}:{window_s}:{window}"
    coll = store.adb().rate_limits
    upd = {"$inc": {"n": 1}, "$setOnInsert": {"expires_at": datetime.fromtimestamp((window + 2) * window_s, timezone.utc)}}
    try:
        doc = coll.find_one_and_update({"_id": _id}, upd, upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:                           # two first requests of a window raced on the upsert
        doc = coll.find_one_and_update({"_id": _id}, {"$inc": {"n": 1}}, return_document=ReturnDocument.AFTER)
    n = doc["n"]
    return {"allowed": n <= limit, "count": n, "limit": limit, "remaining": max(0, limit - n),
            "retry_after": max(1, int((window + 1) * window_s - now))}

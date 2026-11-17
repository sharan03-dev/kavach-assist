"""A.7 monitoring - request latency, error rate and LLM token usage of the chat endpoint.

Every chat request is one document in kavach_assist.metrics (TTL 14 days) -> the admin dashboard aggregates them.
The same numbers are kept as in-process counters and exposed in Prometheus text format at /metrics.
"""
from __future__ import annotations

import threading
from datetime import timedelta

from pymongo.errors import PyMongoError

from . import store

_lock = threading.Lock()
BUCKETS = [0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120]
_c: dict = {"requests": {}, "tokens_prompt": 0, "tokens_completion": 0, "lat_sum": 0.0, "lat_count": 0, "lat_buckets": [0] * (len(BUCKETS) + 1),
            "rate_limited": 0, "logins": {}}


def record(user: dict | None, status: int, ms: float, meta: dict | None = None, error: str | None = None) -> None:
    meta = meta or {}
    key = (str(status), meta.get("provider") or "none")
    with _lock:
        _c["requests"][key] = _c["requests"].get(key, 0) + 1
        _c["tokens_prompt"] += meta.get("prompt_tokens") or 0
        _c["tokens_completion"] += meta.get("completion_tokens") or 0
        _c["lat_sum"] += ms / 1000
        _c["lat_count"] += 1
        for i, b in enumerate(BUCKETS):
            if ms / 1000 <= b:
                _c["lat_buckets"][i] += 1
                break
        else:
            _c["lat_buckets"][-1] += 1
        if status == 429:
            _c["rate_limited"] += 1
    try:
        store.adb().metrics.insert_one({"ts": store.now(), "user_id": (user or {}).get("_id"), "email": (user or {}).get("email"), "status": status,
                                        "ms": round(ms), "intent": meta.get("intent"), "provider": meta.get("provider"), "model": meta.get("model"),
                                        "prompt_tokens": meta.get("prompt_tokens") or 0, "completion_tokens": meta.get("completion_tokens") or 0,
                                        "error": error or meta.get("llm_error")})
    except PyMongoError:
        pass


def login_event(result: str) -> None:
    with _lock:
        _c["logins"][result] = _c["logins"].get(result, 0) + 1


def prometheus() -> str:
    with _lock:
        lines = ["# HELP kavach_chat_requests_total Chat requests by HTTP status and LLM provider", "# TYPE kavach_chat_requests_total counter"]
        for (status, provider), n in sorted(_c["requests"].items()):
            lines.append(f'kavach_chat_requests_total{{status="{status}",provider="{provider}"}} {n}')
        lines += ["# HELP kavach_chat_request_seconds Chat request latency", "# TYPE kavach_chat_request_seconds histogram"]
        cum = 0
        for b, n in zip(BUCKETS, _c["lat_buckets"]):
            cum += n
            lines.append(f'kavach_chat_request_seconds_bucket{{le="{b}"}} {cum}')
        lines += [f'kavach_chat_request_seconds_bucket{{le="+Inf"}} {_c["lat_count"]}', f'kavach_chat_request_seconds_sum {_c["lat_sum"]:.3f}',
                  f'kavach_chat_request_seconds_count {_c["lat_count"]}',
                  "# HELP kavach_llm_tokens_total LLM tokens used", "# TYPE kavach_llm_tokens_total counter",
                  f'kavach_llm_tokens_total{{kind="prompt"}} {_c["tokens_prompt"]}', f'kavach_llm_tokens_total{{kind="completion"}} {_c["tokens_completion"]}',
                  "# HELP kavach_chat_rate_limited_total Requests refused by the rate limiter", "# TYPE kavach_chat_rate_limited_total counter",
                  f'kavach_chat_rate_limited_total {_c["rate_limited"]}',
                  "# HELP kavach_logins_total Sign-in attempts by result", "# TYPE kavach_logins_total counter"]
        for result, n in sorted(_c["logins"].items()):
            lines.append(f'kavach_logins_total{{result="{result}"}} {n}')
    try:
        d = store.adb()
        lines += ["# TYPE kavach_users gauge", f"kavach_users {d.users.estimated_document_count()}",
                  "# TYPE kavach_conversations gauge", f"kavach_conversations {d.conversations.estimated_document_count()}",
                  "# TYPE kavach_open_findings gauge", f'kavach_open_findings {d.scan_findings.count_documents({"status": "open"})}']
    except PyMongoError:
        pass
    return "\n".join(lines) + "\n"


def summary(hours: int = 24) -> dict:
    """Latency percentiles, error rate, tokens and usage per provider / intent / user - one $facet aggregation."""
    since = store.now() - timedelta(hours=hours)
    d = store.adb()
    f = list(d.metrics.aggregate([{"$match": {"ts": {"$gte": since}}}, {"$facet": {
        "total": [{"$group": {"_id": None, "n": {"$sum": 1}, "errors": {"$sum": {"$cond": [{"$gte": ["$status", 500]}, 1, 0]}},
                              "limited": {"$sum": {"$cond": [{"$eq": ["$status", 429]}, 1, 0]}}, "avg_ms": {"$avg": "$ms"}, "max_ms": {"$max": "$ms"},
                              "prompt": {"$sum": "$prompt_tokens"}, "completion": {"$sum": "$completion_tokens"},
                              "fallbacks": {"$sum": {"$cond": [{"$ifNull": ["$error", False]}, 1, 0]}}}}],
        "lat": [{"$match": {"status": 200}}, {"$sort": {"ms": 1}}, {"$group": {"_id": None, "all": {"$push": "$ms"}}}],
        "by_provider": [{"$group": {"_id": "$provider", "n": {"$sum": 1}, "avg_ms": {"$avg": "$ms"}, "tokens": {"$sum": {"$add": ["$prompt_tokens", "$completion_tokens"]}}}}],
        "by_intent": [{"$group": {"_id": "$intent", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}],
        "by_user": [{"$group": {"_id": "$email", "n": {"$sum": 1}, "tokens": {"$sum": {"$add": ["$prompt_tokens", "$completion_tokens"]}}}}, {"$sort": {"n": -1}}, {"$limit": 10}],
        "per_hour": [{"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%dT%H:00", "date": "$ts"}}, "n": {"$sum": 1}, "avg_ms": {"$avg": "$ms"}}}, {"$sort": {"_id": 1}}],
    }}]))[0]
    tot = (f["total"] or [{}])[0]
    lat = (f["lat"] or [{"all": []}])[0]["all"]

    def pct(p):
        return lat[min(len(lat) - 1, int(p * len(lat)))] if lat else None
    n = tot.get("n", 0)
    return {"hours": hours, "requests": n, "errors": tot.get("errors", 0), "error_rate": round(tot.get("errors", 0) / n, 4) if n else 0,
            "rate_limited": tot.get("limited", 0), "llm_fallbacks": tot.get("fallbacks", 0),
            "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "avg": round(tot["avg_ms"]) if tot.get("avg_ms") is not None else None, "max": tot.get("max_ms")},
            "tokens": {"prompt": tot.get("prompt", 0), "completion": tot.get("completion", 0), "total": tot.get("prompt", 0) + tot.get("completion", 0)},
            "by_provider": [{"provider": x["_id"] or "none", "requests": x["n"], "avg_ms": round(x["avg_ms"] or 0), "tokens": x["tokens"]} for x in f["by_provider"]],
            "by_intent": [{"intent": x["_id"] or "-", "requests": x["n"]} for x in f["by_intent"]],
            "by_user": [{"email": x["_id"] or "-", "requests": x["n"], "tokens": x["tokens"]} for x in f["by_user"]],
            "per_hour": [{"hour": x["_id"], "requests": x["n"], "avg_ms": round(x["avg_ms"] or 0)} for x in f["per_hour"]]}

#!/usr/bin/env python3
"""Lab 14.1 - Build a monitoring dashboard: a Python script that reports the key MongoDB metrics.

    python monitor_dashboard.py --uri "mongodb://monitor_app:<pw>@prod-mongo-1:27017,.../?replicaSet=rs0&authSource=admin"
    python monitor_dashboard.py --uri ... --interval 30 --log results/metrics.log         # every 30 s (the lab)
    python monitor_dashboard.py --uri ... --once                                          # one table, then exit
    python monitor_dashboard.py --uri ... --webhook https://hooks.slack.com/services/...  # activity: alert a channel

Instructions of the lab and where they are in this file:
  1. connect + retrieve connection count, opcounters, WiredTiger cache statistics and replica set status
        -> collect()          serverStatus of EVERY member (direct connections) + replSetGetStatus
  2. format the output as a table and write it to a log file every 30 seconds
        -> format_table()     tabulate;  run() appends the table to --log every --interval seconds
  3. threshold alerting: WARNING if the cache hit ratio drops below 80 % or the replication lag exceeds 10 s
        -> check_thresholds() prints "WARNING ..." lines
  Activity (try it yourself): send the alerts to a webhook (Slack or Discord) when a threshold is breached
        -> send_webhook()     requests.post(webhook_url, json=alert_payload) inside try/except

Two corrections to the solution text (measured on MongoDB 7.0.34, see README):
  * serverStatus has no "bytes read from cache" counter. The hit ratio is computed from the counters that exist:
        hit ratio = 1 - (pages read into cache / pages requested from the cache)
    over the last interval (deltas). The ratio since start-up is shown as well, but it hides a cold cache.
  * A SECONDARY counts the writes it replicates in opcountersRepl, not in opcounters: both are reported.

Least privilege: the script needs only the built-in role clusterMonitor (+ read on local), i.e. user monitor_app.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from pymongo.uri_parser import parse_uri
from tabulate import tabulate

UTC = timezone.utc
CACHE_MIN = 0.80          # WARNING when the cache hit ratio drops below 80 %
LAG_MAX = 10.0            # WARNING when the replication lag exceeds 10 seconds
OPS = ("insert", "query", "update", "delete", "getmore", "command")


# ============================================================================ 1. connect + retrieve
def connect(uri: str, **kw) -> MongoClient:
    return MongoClient(uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=3000, socketTimeoutMS=10000,
                       appname="lab14-monitor", tz_aware=True, **kw)


class Members:
    """One direct connection per replica-set member, with the credentials of the main URI.

    serverStatus on the replica-set connection answers for the PRIMARY only; a dashboard must see every member
    (a SECONDARY can have a cold cache or lag while the PRIMARY looks perfect)."""

    def __init__(self, uri: str):
        self.uri, self.p, self.clients = uri, parse_uri(uri), {}

    def get(self, host: str) -> MongoClient:
        c = self.clients.get(host)
        if c is None:
            o = self.p.get("options", {})
            kw = {}
            if self.p.get("username"):
                kw = dict(username=self.p["username"], password=self.p["password"],
                          authSource=o.get("authsource") or o.get("authSource") or "admin")
            c = self.clients[host] = MongoClient(host, directConnection=True, serverSelectionTimeoutMS=2500,
                                                 connectTimeoutMS=2000, socketTimeoutMS=6000, appname="lab14-monitor",
                                                 tz_aware=True, **kw)
        return c

    def close(self):
        for c in self.clients.values():
            c.close()
        self.clients.clear()


def _member_status(c: MongoClient) -> dict:
    ss = c.admin.command("serverStatus", repl=1, metrics=0, locks=0)
    wt = (ss.get("wiredTiger") or {}).get("cache") or {}
    return {
        "host": ss.get("host"), "version": ss.get("version"), "uptime_s": ss.get("uptime"),
        "connections": {k: ss["connections"].get(k) for k in ("current", "available", "totalCreated", "active")},
        "opcounters": {k: ss["opcounters"].get(k, 0) for k in OPS},
        "opcountersRepl": {k: ss.get("opcountersRepl", {}).get(k, 0) for k in ("insert", "update", "delete", "command")},
        "cache": {"bytes_in_cache": wt.get("bytes currently in the cache"),
                  "max_bytes": wt.get("maximum bytes configured"),
                  "dirty_bytes": wt.get("tracked dirty bytes in the cache"),
                  "pages_requested": wt.get("pages requested from the cache"),
                  "pages_read_into": wt.get("pages read into cache"),
                  "bytes_read_into": wt.get("bytes read into cache")},
        "repl": {"set": (ss.get("repl") or {}).get("setName"), "primary": (ss.get("repl") or {}).get("isWritablePrimary"),
                 "secondary": (ss.get("repl") or {}).get("secondary")},
    }


def replica_status(c: MongoClient) -> dict | None:
    """replSetGetStatus -> state, health and replication lag (optimeDiff between the PRIMARY and each member)."""
    try:
        st = c.admin.command("replSetGetStatus")
    except PyMongoError as exc:                  # standalone server: "not running with --replSet"
        if "replSet" in str(exc) or getattr(exc, "code", None) == 76:
            return None
        raise
    prim = next((m for m in st["members"] if m.get("stateStr") == "PRIMARY"), None)
    out = []
    for m in st["members"]:
        lag = None
        if prim and m.get("optimeDate") and prim.get("optimeDate") and m.get("stateStr") in ("PRIMARY", "SECONDARY"):
            lag = max(0.0, (prim["optimeDate"] - m["optimeDate"]).total_seconds())
        out.append({"name": m["name"], "state": m.get("stateStr"), "health": m.get("health"), "lag_s": lag,
                    "sync_source": m.get("syncSourceHost") or "", "ping_ms": m.get("pingMs"),
                    "optime": m.get("optimeDate").isoformat() if m.get("optimeDate") else None})
    return {"set": st.get("set"), "term": st.get("term"), "primary": prim["name"] if prim else None, "members": out}


def collect(client: MongoClient, members: Members | None = None) -> dict:
    """One sample: replica set status + serverStatus of every member (or of the one server if standalone)."""
    t = time.time()
    rs = replica_status(client)
    hosts = [m["name"] for m in rs["members"]] if rs else [None]
    per = {}
    for h in hosts:
        try:
            c = members.get(h) if (members and h) else client
            per[h or "standalone"] = {"ok": True, **_member_status(c)}
        except PyMongoError as exc:
            per[h or "standalone"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    return {"t": t, "at": datetime.fromtimestamp(t, UTC).isoformat(), "rs": rs, "members": per}


# ============================================================================ derived numbers
def _d(cur, prev, *keys):
    a, b = cur, prev
    for k in keys:
        a, b = (a or {}).get(k), (b or {}).get(k)
    if a is None or b is None:
        return None
    return a - b


def compute(cur: dict, prev: dict | None) -> dict:
    """Rates per second and the cache hit ratio over the interval between two samples (needs counters twice)."""
    dt = (cur["t"] - prev["t"]) if prev else None
    rows = []
    lag = {m["name"]: m for m in (cur["rs"] or {}).get("members", [])}
    for host, m in cur["members"].items():
        r = {"member": host, "state": (lag.get(host) or {}).get("state") or ("STANDALONE" if host == "standalone" else "?"),
             "ok": m.get("ok", False), "lag_s": (lag.get(host) or {}).get("lag_s"),
             "sync_source": (lag.get(host) or {}).get("sync_source", "")}
        if not m.get("ok"):
            r["error"] = m.get("error")
            rows.append(r)
            continue
        c = m["cache"]
        r["connections"] = m["connections"]["current"]
        r["connections_available"] = m["connections"]["available"]
        r["cache_used_pct"] = round(100 * c["bytes_in_cache"] / c["max_bytes"], 1) if c.get("max_bytes") else None
        r["cache_mb"] = round((c["bytes_in_cache"] or 0) / 1048576, 1)
        r["hit_ratio_total"] = (1 - c["pages_read_into"] / c["pages_requested"]) if c.get("pages_requested") else None
        r["opcounters"] = m["opcounters"]
        p = (prev or {}).get("members", {}).get(host)
        restarted = p and p.get("ok") and (m.get("uptime_s") or 0) < (p.get("uptime_s") or 0)
        if p and p.get("ok") and dt and dt > 0 and not restarted:
            r["ops_s"] = {k: round(max(0, _d(m, p, "opcounters", k)) / dt, 1) for k in OPS}
            r["repl_ops_s"] = round(sum(max(0, _d(m, p, "opcountersRepl", k)) for k in ("insert", "update", "delete")) / dt, 1)
            req, miss = _d(m, p, "cache", "pages_requested"), _d(m, p, "cache", "pages_read_into")
            r["pages_requested"], r["pages_read_into"] = req, miss
            r["hit_ratio"] = (1 - miss / req) if req and req > 0 else None     # None = no cache traffic in the interval
        else:
            r["ops_s"], r["repl_ops_s"], r["hit_ratio"] = None, None, None
            if restarted:
                r["note"] = "restarted since the last sample - counters reset"
        rows.append(r)
    tot = [r for r in rows if r.get("ok")]
    return {"at": cur["at"], "t": cur["t"], "interval_s": round(dt, 1) if dt else None,
            "set": (cur["rs"] or {}).get("set"), "primary": (cur["rs"] or {}).get("primary"), "rows": rows,
            "connections_total": sum(r.get("connections") or 0 for r in tot),
            "max_lag_s": max([r["lag_s"] for r in rows if r.get("lag_s") is not None] or [0.0]),
            "min_hit_ratio": min([r["hit_ratio"] for r in tot if r.get("hit_ratio") is not None] or [None], key=lambda x: (x is None, x))}


# ============================================================================ 2. table + log file
def _pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def format_table(snap: dict, alerts: list | None = None) -> str:
    head = ["member", "state", "conns", "ins/s", "qry/s", "upd/s", "del/s", "getmore/s", "cmd/s", "repl/s",
            "cache used", "hit ratio (interval)", "hit ratio (since start)", "repl lag"]
    body = []
    for r in snap["rows"]:
        if not r.get("ok"):
            body.append([r["member"], r["state"], "-", *["-"] * 7, "-", "-", "-", r.get("error", "unreachable")[:40]])
            continue
        o = r.get("ops_s") or {}
        body.append([r["member"], r["state"], r.get("connections"),
                     *[o.get(k, "-") for k in OPS], "-" if r.get("repl_ops_s") is None else r["repl_ops_s"],
                     f"{r['cache_mb']} MB ({r['cache_used_pct']}%)", _pct(r.get("hit_ratio")), _pct(r.get("hit_ratio_total")),
                     "-" if r.get("lag_s") is None else f"{r['lag_s']:.1f} s"])
    title = (f"{snap['at'][:19].replace('T', ' ')} UTC   replica set {snap.get('set') or '-'}   primary {snap.get('primary') or '-'}"
             f"   interval {snap.get('interval_s') or '-'} s   connections {snap['connections_total']}")
    out = title + "\n" + tabulate(body, headers=head, tablefmt="github", disable_numparse=True)
    for a in alerts or []:
        out += "\n" + a["line"]
    return out


def write_log(path: str, text: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:            # open('metrics.log', 'a') - the solution text
        f.write(text.rstrip() + "\n\n")


# ============================================================================ 3. threshold alerting
def check_thresholds(snap: dict, cache_min: float = CACHE_MIN, lag_max: float = LAG_MAX) -> list:
    alerts = []
    for r in snap["rows"]:
        if r.get("hit_ratio") is not None and r["hit_ratio"] < cache_min:
            alerts.append({"key": f"cache:{r['member']}", "metric": "cache_hit_ratio", "member": r["member"],
                           "value": round(r["hit_ratio"], 4), "threshold": cache_min,
                           "line": f"WARNING {r['member']}: cache hit ratio {100 * r['hit_ratio']:.1f}% is below "
                                   f"{100 * cache_min:.0f}% ({r.get('pages_read_into')} of {r.get('pages_requested')} pages "
                                   f"requested had to be read into the cache)"})
        if r.get("lag_s") is not None and r["lag_s"] > lag_max:
            alerts.append({"key": f"lag:{r['member']}", "metric": "replication_lag", "member": r["member"],
                           "value": round(r["lag_s"], 1), "threshold": lag_max,
                           "line": f"WARNING {r['member']}: replication lag {r['lag_s']:.1f} s exceeds {lag_max:g} s "
                                   f"(syncing from {r.get('sync_source') or '-'})"})
        if not r.get("ok") or r.get("state") not in ("PRIMARY", "SECONDARY", "STANDALONE", "ARBITER"):
            alerts.append({"key": f"down:{r['member']}", "metric": "member_state", "member": r["member"],
                           "value": r.get("state"), "threshold": "PRIMARY/SECONDARY",
                           "line": f"WARNING {r['member']}: member is {r.get('state') or 'unreachable'}"})
    return alerts


class AlertState:
    """Only CHANGES are sent to the webhook: FIRING once, a reminder every `remind_s` while it lasts, RESOLVED once.
    (Printing a WARNING every 30 s is fine for a log; posting it to a chat channel every 30 s is alert fatigue.)"""

    def __init__(self, remind_s: float = 600):
        self.active: dict = {}
        self.remind_s = remind_s

    def update(self, alerts: list, now: float | None = None) -> list:
        now = now or time.time()
        events, seen = [], set()
        for a in alerts:
            seen.add(a["key"])
            st = self.active.get(a["key"])
            if st is None:
                self.active[a["key"]] = {"since": now, "sent": now, "alert": a}
                events.append({**a, "status": "FIRING", "since": now})
            else:
                st["alert"] = a
                if now - st["sent"] >= self.remind_s:
                    st["sent"] = now
                    events.append({**a, "status": "STILL FIRING", "since": st["since"]})
        for k in list(self.active):
            if k not in seen:
                st = self.active.pop(k)
                events.append({**st["alert"], "status": "RESOLVED", "since": st["since"], "duration_s": round(now - st["since"], 1),
                               "line": f"RESOLVED {st['alert']['member']}: {st['alert']['metric']} back within the threshold "
                                       f"after {now - st['since']:.0f} s"})
        return events


# ============================================================================ activity: webhook (Slack / Discord)
def _human(e: dict) -> str:
    if e["metric"] == "cache_hit_ratio":
        return f"cache hit ratio {100 * e['value']:.1f}% (threshold {100 * e['threshold']:.0f}%)"
    if e["metric"] == "replication_lag":
        return f"replication lag {e['value']:.1f} s (threshold {e['threshold']:g} s)"
    return f"state {e['value']}"


def build_payload(url: str, e: dict, source: str = "Kavach MongoDB monitor") -> dict:
    """Slack incoming webhooks take {text, blocks}; Discord webhooks take {content, embeds}. Same alert, two shapes."""
    icon = {"FIRING": ":rotating_light:", "STILL FIRING": ":warning:", "RESOLVED": ":white_check_mark:"}.get(e["status"], "")
    text = f"[{e['status']}] {e['member']}: {_human(e)}"
    at = datetime.now(UTC).isoformat(timespec="seconds")
    if "discord" in url:
        return {"username": source, "content": f"**{e['status']}** - MongoDB alert",
                "embeds": [{"title": text, "color": 0x37C98B if e["status"] == "RESOLVED" else 0xFF4D5E, "timestamp": at,
                            "fields": [{"name": "Member", "value": e["member"], "inline": True},
                                       {"name": "Metric", "value": e["metric"], "inline": True},
                                       {"name": "Value", "value": str(e["value"]), "inline": True},
                                       {"name": "Threshold", "value": str(e["threshold"]), "inline": True}],
                            "footer": {"text": source}}]}
    return {"text": f"{icon} {text}",
            "blocks": [{"type": "header", "text": {"type": "plain_text", "text": f"{e['status']}: MongoDB {e['metric'].replace('_', ' ')}"}},
                       {"type": "section", "fields": [{"type": "mrkdwn", "text": f"*Member*\n{e['member']}"},
                                                      {"type": "mrkdwn", "text": f"*Value*\n{e['value']}"},
                                                      {"type": "mrkdwn", "text": f"*Threshold*\n{e['threshold']}"},
                                                      {"type": "mrkdwn", "text": f"*Since*\n{datetime.fromtimestamp(e.get('since', time.time()), UTC):%H:%M:%S} UTC"}]},
                       {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{source} - {at}"}]}]}


def send_webhook(webhook_url: str, alert_payload: dict, timeout: float = 5) -> dict:
    """requests.post(webhook_url, json=alert_payload) with a try/except: a dead chat service must never stop the monitor."""
    t0 = time.perf_counter()
    try:
        r = requests.post(webhook_url, json=alert_payload, timeout=timeout)
        r.raise_for_status()
        return {"ok": True, "http": r.status_code, "ms": round(1000 * (time.perf_counter() - t0))}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}", "ms": round(1000 * (time.perf_counter() - t0))}


def notify(urls: list, events: list, log=print) -> list:
    sent = []
    for e in events:
        for u in urls:
            res = send_webhook(u, build_payload(u, e))
            sent.append({"url": _redact(u), "status": e["status"], "key": e["key"], **res})
            log(f"   webhook {e['status']:<12} {e['key']:<32} -> {_redact(u)}: "
                f"{'HTTP ' + str(res.get('http')) if res['ok'] else res['error']} ({res['ms']} ms)")
    return sent


def _redact(u: str) -> str:
    """Slack/Discord webhook URLs ARE the secret: never write the token part into a log."""
    for k in ("hooks.slack.com/services/", "/api/webhooks/"):
        if k in u:
            head, tail = u.split(k, 1)
            return head + k + tail[:6] + "..."
    return u


# ============================================================================ the loop
def run(uri: str, interval: float = 30, log_path: str = "metrics.log", once: bool = False, webhooks: list | None = None,
        cache_min: float = CACHE_MIN, lag_max: float = LAG_MAX, on_sample=None, max_samples: int | None = None):
    client = connect(uri)
    members = Members(uri)
    state = AlertState()
    prev, n = None, 0
    if once:                                   # rates need two samples: take the first one a few seconds earlier
        prev = collect(client, members)
        time.sleep(min(interval, 5))
    try:
        while True:
            t0 = time.time()
            try:
                cur = collect(client, members)
                snap = compute(cur, prev)
                alerts = check_thresholds(snap, cache_min, lag_max) if prev else []
                table = format_table(snap, alerts)
                print(table, flush=True)
                write_log(log_path, table)
                events = state.update(alerts)
                sent = notify(webhooks or [], events) if events else []
                if on_sample:
                    on_sample(snap, alerts, events, sent)
                prev = cur
            except PyMongoError as exc:
                line = f"{datetime.now(UTC):%Y-%m-%d %H:%M:%S} UTC   WARNING cannot reach MongoDB: {type(exc).__name__}: {str(exc)[:160]}"
                print(line, flush=True)
                write_log(log_path, line)
            n += 1
            if once or (max_samples and n >= max_samples):
                return
            time.sleep(max(0.0, interval - (time.time() - t0)))
    finally:
        members.close()
        client.close()


def build_uri(user: str, password: str, hosts: str, rs: str | None) -> str:
    q = f"replicaSet={rs}&authSource=admin" if rs else "authSource=admin&directConnection=true"
    return f"mongodb://{quote_plus(user)}:{quote_plus(password)}@{hosts}/?{q}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uri", default=os.getenv("MONITOR_URI") or os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    ap.add_argument("--interval", type=float, default=30, help="seconds between samples (default 30)")
    ap.add_argument("--log", default=os.path.join(os.getenv("RESULTS_DIR", "."), "metrics.log"))
    ap.add_argument("--once", action="store_true", help="one table (two samples 5 s apart), then exit")
    ap.add_argument("--samples", type=int, help="stop after N tables")
    ap.add_argument("--webhook", action="append", default=[w for w in os.getenv("LAB14_WEBHOOK_URL", "").split(",") if w.strip()],
                    help="Slack or Discord webhook URL (repeatable; also LAB14_WEBHOOK_URL)")
    ap.add_argument("--cache-min", type=float, default=CACHE_MIN)
    ap.add_argument("--lag-max", type=float, default=LAG_MAX)
    a = ap.parse_args()
    try:
        run(a.uri, a.interval, a.log, a.once, a.webhook, a.cache_min, a.lag_max, max_samples=a.samples)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

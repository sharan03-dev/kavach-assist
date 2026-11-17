"""Lab 14 - Managing database services in production - applied to Lab 13 (and so to Lab 10), following the mentor's rule.

    Lab 14.1   monitoring dashboard: labs/lab14/monitor_dashboard.py watches the PRODUCTION replica set of Lab 13
               (prod-mongo-1/2/3) as the least-privilege user monitor_app (clusterMonitor): connections, opcounters,
               WiredTiger cache, replica set status; table to results/metrics.log every 30 s; WARNING below 80 % cache hit
               ratio or above 10 s replication lag.
    Activity   alerts to a webhook (Slack + Discord formats). The website has a built-in receiver (#mongodb-alerts) so the
               demo works offline; a real Slack/Discord URL can be added with LAB14_WEBHOOK_URL.
               Two DRILLS make the thresholds fire for real: replication lag (fsyncLock on a secondary that nobody syncs
               from, while writes continue) and a cold cache (restart a secondary, then read 60,000 documents from it).
    Lab 14.2   backup_verify.py: mongodump to timestamped folders, hourly with the schedule library (ops_daemon),
               7-day retention, restore verification of 1,000 documents, point-in-time restore from the oplog,
               1,000 / 10,000 / 100,000-document benchmark, RPO and RTO.
    14 on 13   disaster recovery of the Lab 10 classifier billing on the Lab 13 production set: base backup, more
               customers pay, an attacker zeroes every wallet and deletes the payments, point-in-time recovery to the
               second before the attack, every rupee and both invariants checked.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from bson import json_util
from pymongo import MongoClient
from pymongo.errors import CollectionInvalid, OperationFailure, PyMongoError

from . import config
from . import lab13 as L13

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "labs" / "lab14"
sys.path.insert(0, str(LAB))
import backup_verify as BV          # noqa: E402  (the Lab 14.2 script, used as a module)
import monitor_dashboard as MD      # noqa: E402  (the Lab 14.1 script, used as a module)

RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
BACKUP_DIR = Path(os.getenv("LAB14_BACKUP_DIR", "/backups"))
INBOX = os.getenv("LAB14_INBOX_URL", "http://lab7:8000").rstrip("/")
EXTERNAL = [u.strip() for u in os.getenv("LAB14_WEBHOOK_URL", "").split(",") if u.strip()]
CACHE_MIN = float(os.getenv("LAB14_CACHE_MIN", "0.80"))
LAG_MAX = float(os.getenv("LAB14_LAG_MAX", "10"))
INTERVAL = int(os.getenv("LAB14_INTERVAL", "30"))
BACKUP_EVERY_MIN = int(os.getenv("LAB14_BACKUP_EVERY_MIN", "60"))
TARGETS = [t.strip() for t in os.getenv("LAB14_BACKUP_TARGETS", "billing,kavach,tasktracker").split(",") if t.strip()]
HEAVY = threading.Lock()
USERS = {
    "monitor_app": ([{"role": "clusterMonitor", "db": "admin"}, {"role": "read", "db": "local"}],
                    "Lab 14.1 monitoring dashboard + daemon: reads metrics, never data"),
    "backup_app": ([{"role": "backup", "db": "admin"}, {"role": "restore", "db": "admin"}],
                   "Lab 14.2 mongodump / mongorestore"),
}


def _j(x):
    return json.loads(json_util.dumps(x)) if not isinstance(x, (str, int, float, type(None))) else x


def _jd(x):
    return json.loads(json.dumps(x, default=str))


def now_iso():
    return datetime.now(UTC).isoformat()


# ============================================================================ users + URIs
def password(user: str) -> str:
    sec = L13.secrets()
    if user not in sec:
        raise RuntimeError(f"no password for {user} in {L13.PROD_DIR}/secrets.json - restart the stack once "
                           "(docker compose up -d lab14): the lab13-keyfile container adds the Lab 14 users' passwords")
    return sec[user]


def uri(user: str = "admin") -> str:
    return (f"mongodb://{quote_plus(user)}:{quote_plus(password(user))}@{','.join(L13.PROD_HOSTS)}/"
            f"?replicaSet={L13.PROD_RS}&authSource=admin")


def kavach_uri() -> str:
    return config.MONGO_URI


def kdb():
    from .db import get_db
    return get_db()


def ensure_users(log=print) -> dict:
    """Create monitor_app + backup_app on the production set and MEASURE what each one may do."""
    adm = L13.client("admin")
    have = {u["user"] for u in adm.admin.command("usersInfo")["users"]}
    for u, (roles, _) in USERS.items():
        if u in have:
            adm.admin.command("updateUser", u, pwd=password(u), roles=roles)
        else:
            adm.admin.command("createUser", u, pwd=password(u), roles=roles, mechanisms=["SCRAM-SHA-256"])
    log("   users: " + ", ".join(f"{u} ({', '.join(r['role'] for r in rl)})" for u, (rl, _) in USERS.items()))
    tests = [("serverStatus", lambda c: c.admin.command("serverStatus")),
             ("replSetGetStatus", lambda c: c.admin.command("replSetGetStatus")),
             ("read billing (find cti_billing.accounts)", lambda c: c.cti_billing.accounts.find_one()),
             ("insert (lab14.priv_test)", lambda c: c.lab14.priv_test.insert_one({"t": 1})),
             ("update (lab14.priv_test)", lambda c: c.lab14.priv_test.update_one({}, {"$set": {"t": 2}})),
             ("delete (lab14.priv_test)", lambda c: c.lab14.priv_test.delete_one({})),
             ("createUser", lambda c: c.admin.command("createUser", "lab14_tmp_user", pwd="x" * 16, roles=[]))]
    matrix = []
    for u in USERS:
        c = MongoClient(uri(u), serverSelectionTimeoutMS=4000)
        row = {"user": u, "roles": [r["role"] for r in USERS[u][0]], "purpose": USERS[u][1], "ops": {}}
        for name, fn in tests:
            try:
                fn(c)
                row["ops"][name] = "allowed"
            except OperationFailure as exc:
                row["ops"][name] = "refused" if exc.code == 13 else f"error {exc.code}"
        c.close()
        matrix.append(row)
    try:
        adm.admin.command("dropUser", "lab14_tmp_user")
    except OperationFailure:
        pass
    adm.lab14.priv_test.drop()
    for r in matrix:
        log(f"   {r['user']:<12} " + "  ".join(f"{k.split(' (')[0]}={v}" for k, v in r["ops"].items()))
    return {"users": [{"user": u, "roles": [x["role"] + "@" + x["db"] for x in rl], "purpose": p} for u, (rl, p) in USERS.items()],
            "matrix": matrix}


# ============================================================================ storage on the Kavach database
def _capped(name: str, size_mb: int = 8):
    db = kdb()
    try:
        db.create_collection(name, capped=True, size=size_mb * 1024 * 1024)
    except (CollectionInvalid, OperationFailure):
        pass
    return db[name]


def compact(snap: dict, alerts: list | None = None, source: str = "daemon") -> dict:
    rows = []
    for r in snap["rows"]:
        o = r.get("ops_s") or {}
        rows.append({"member": r["member"], "state": r["state"], "ok": r.get("ok"), "connections": r.get("connections"),
                     "ops_s": round(sum(o.values()), 1) if o else None, "ops": o or None, "repl_ops_s": r.get("repl_ops_s"),
                     "hit_ratio": r.get("hit_ratio"), "hit_ratio_total": r.get("hit_ratio_total"),
                     "cache_used_pct": r.get("cache_used_pct"), "cache_mb": r.get("cache_mb"), "lag_s": r.get("lag_s"),
                     "sync_source": r.get("sync_source"), "pages_requested": r.get("pages_requested"),
                     "pages_read_into": r.get("pages_read_into"), "error": r.get("error")})
    return {"at": datetime.fromtimestamp(snap["t"], UTC), "interval_s": snap.get("interval_s"), "set": snap.get("set"),
            "primary": snap.get("primary"), "connections_total": snap.get("connections_total"), "max_lag_s": snap.get("max_lag_s"),
            "rows": rows, "alerts": [a["line"] for a in alerts or []], "source": source}


def record_events(events: list, sent: list, source: str):
    if not events:
        return
    col = kdb()["lab14_alerts"]
    for e in events:
        col.insert_one({"at": datetime.now(UTC), "status": e["status"], "key": e["key"], "metric": e["metric"],
                        "member": e["member"], "value": e["value"], "threshold": e["threshold"], "line": e["line"],
                        "source": source, "webhooks": [s for s in sent if s["key"] == e["key"] and s["status"] == e["status"]]})


def webhook_urls() -> list:
    return [f"{INBOX}/api/lab14/webhook/slack", f"{INBOX}/api/lab14/webhook/discord"] + EXTERNAL


# ============================================================================ Lab 14.1 - live dashboard (website)
class Live:
    """The website's own sampler: one sample per page refresh; rates over the time since the previous refresh."""

    def __init__(self):
        self.lock, self.prev, self.client, self.members, self.state = threading.Lock(), None, None, None, MD.AlertState(1e9)

    def sample(self) -> dict:
        with self.lock:
            if self.client is None:
                self.client = MD.connect(uri("monitor_app"))
                self.members = MD.Members(uri("monitor_app"))
            try:
                cur = MD.collect(self.client, self.members)
            except PyMongoError:
                self.client.close(); self.members.close(); self.client = None
                raise
            if self.prev and cur["t"] - self.prev["t"] < 1.5:
                cur = self.prev                                 # two browsers polling: keep the last interval
            snap = MD.compute(cur, self.prev if self.prev is not cur else None)
            if self.prev is not cur:
                self.prev = cur
            alerts = MD.check_thresholds(snap, CACHE_MIN, LAG_MAX) if snap.get("interval_s") else []
            return _jd({**compact(snap, alerts, "live"), "table": MD.format_table(snap, alerts),
                        "thresholds": {"cache_min": CACHE_MIN, "lag_max": LAG_MAX}})


LIVE = Live()


def history(minutes: int = 60) -> dict:
    since = datetime.fromtimestamp(time.time() - minutes * 60, UTC)
    rows = list(kdb()["lab14_metrics"].find({"at": {"$gte": since}}, {"_id": 0}).sort("$natural", 1))
    return _jd({"samples": rows[-400:], "minutes": minutes})


def metrics_log_tail(lines: int = 60) -> str:
    p = RESULTS / "metrics.log"
    if not p.exists():
        return ""
    with open(p, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 60000))
        return "\n".join(f.read().decode("utf-8", "replace").splitlines()[-lines:])


# ============================================================================ drills (make the thresholds fire for real)
def _drill_monitor(tag: str):
    client, members, state = MD.connect(uri("monitor_app")), MD.Members(uri("monitor_app")), MD.AlertState(1e9)
    store = _capped("lab14_metrics")
    sent_all, events_all = [], []

    def tick(prev, log, quiet=False):
        cur = MD.collect(client, members)
        snap = MD.compute(cur, prev)
        alerts = MD.check_thresholds(snap, CACHE_MIN, LAG_MAX) if prev else []
        table = MD.format_table(snap, alerts)
        MD.write_log(str(RESULTS / "metrics.log"), f"[{tag}]\n" + table)
        if not quiet:
            for a in alerts:
                log("   " + a["line"])
        ev = state.update(alerts)
        sent = MD.notify(webhook_urls(), ev, log=lambda m: log(m)) if ev else []
        record_events(ev, sent, tag)
        events_all.extend({k: e[k] for k in ("status", "key", "line", "value")} for e in ev)
        sent_all.extend(sent)
        try:
            store.insert_one(compact(snap, alerts, tag))
        except PyMongoError:
            pass
        return cur, snap
    return client, members, tick, events_all, sent_all


def lag_drill(duration_s: int = 24, log=print) -> dict:
    """fsyncLock() a SECONDARY that no other member syncs from, keep writing to the PRIMARY, watch the lag pass 10 s."""
    adm = L13.client("admin")
    st = adm.admin.command("replSetGetStatus")
    ms = st["members"]
    sources = {m.get("syncSourceHost") for m in ms if m.get("syncSourceHost")}
    secs = [m["name"] for m in ms if m["stateStr"] == "SECONDARY"]
    leaf = [h for h in secs if h not in sources]
    target = (leaf or secs or [None])[-1]
    if not target:
        raise RuntimeError("no SECONDARY to lock")
    if not leaf:                                        # every secondary is a sync source: re-point the others first
        prim = next(m["name"] for m in ms if m["stateStr"] == "PRIMARY")
        for h in secs:
            if h != target:
                L13.new_client("admin", direct=h).admin.command("replSetSyncFrom", prim)
    sync = {m["name"]: m.get("syncSourceHost") or "-" for m in ms}
    log(f"   sync sources: {sync}  ->  locking {target} (no member replicates from it, so the majority keeps acknowledging)")
    client, members, tick, events, sent = _drill_monitor("lag-drill")
    d = L13.new_client("admin", direct=target)
    stop, wr = threading.Event(), {"ok": 0, "err": 0}
    hb = adm.lab14.get_collection("heartbeat")

    def writer():
        while not stop.is_set():
            try:
                hb.insert_one({"at": datetime.now(UTC), "drill": "lag"}); wr["ok"] += 1
            except PyMongoError:
                wr["err"] += 1
            time.sleep(0.2)
    series, t0 = [], time.time()
    prev, _ = tick(None, log, quiet=True)
    th = threading.Thread(target=writer, daemon=True)
    th.start()
    unlocked_at = None
    try:
        d.admin.command("fsync", lock=True)
        log(f"   db.fsyncLock() on {target}: it stops applying the oplog; writes go on at ~5 per second (w: majority)")
        while time.time() - t0 < duration_s:
            time.sleep(2)
            prev, snap = tick(prev, log)
            series.append({"t": round(time.time() - t0, 1), "writes": wr["ok"],
                           "lag": {r["member"]: r.get("lag_s") for r in snap["rows"]}})
    finally:
        for _ in range(5):
            try:
                if d.admin.command("fsyncUnlock").get("lockCount", 0) == 0:
                    break
            except OperationFailure:
                break
        unlocked_at = round(time.time() - t0, 1)
        log(f"   db.fsyncUnlock() at {unlocked_at} s")
    for _ in range(15):
        time.sleep(2)
        prev, snap = tick(prev, log)
        lag = {r["member"]: r.get("lag_s") for r in snap["rows"]}
        series.append({"t": round(time.time() - t0, 1), "writes": wr["ok"], "lag": lag})
        if (lag.get(target) or 0) < 2 and any(e["status"] == "RESOLVED" for e in events):
            break
    stop.set(); th.join()
    d.close(); client.close(); members.close()
    mx = max([(s["lag"].get(target) or 0) for s in series] or [0])
    out = {"target": target, "sync_sources": sync, "series": series, "max_lag_s": mx, "unlocked_at_s": unlocked_at,
           "writes_ok": wr["ok"], "writes_err": wr["err"], "events": events, "webhooks": sent,
           "fired": any(e["status"] == "FIRING" and e["key"] == f"lag:{target}" for e in events),
           "resolved": any(e["status"] == "RESOLVED" and e["key"] == f"lag:{target}" for e in events),
           "threshold_s": LAG_MAX}
    out["pass"] = out["fired"] and out["resolved"] and wr["err"] == 0 and wr["ok"] > 0
    log(f"   lag drill: max lag {mx:.1f} s on {target}, writes acknowledged {wr['ok']} (errors {wr['err']}), "
        f"WARNING fired {out['fired']}, resolved {out['resolved']}")
    return out


def restart_member(host: str) -> float:
    t0 = time.time()
    cmd = os.getenv("LAB14_RESTART_CMD")
    if cmd:                                             # for a deployment without Docker (e.g. a local test bed)
        import subprocess
        subprocess.run([cmd, host], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    else:
        r = L13.docker("restart", L13.container_of(host), timeout=120)
        if r.rc:
            raise RuntimeError(f"docker restart failed: {(r.err or r.out)[-200:]}")
    return round(time.time() - t0, 1)


def ensure_probe(log=print, n: int = 60000):
    adm = L13.client("admin")
    c = adm.lab14.cache_probe
    if c.estimated_document_count() >= n:
        return
    log(f"   writing {n:,} documents of ~1 KB to lab14.cache_probe (more than a cold cache holds in a few pages)")
    c.drop()
    pad = "x" * 900
    for i in range(0, n, 5000):
        c.insert_many([{"_id": j, "n": j, "pad": pad, "r": os.urandom(24).hex()} for j in range(i, i + 5000)])


def cache_drill(log=print) -> dict:
    """Restart a SECONDARY (its WiredTiger cache is empty), read 60,000 documents from it: hit ratio << 80 %."""
    ensure_probe(log)
    adm = L13.client("admin")
    ms = adm.admin.command("replSetGetStatus")["members"]
    secs = [m["name"] for m in ms if m["stateStr"] == "SECONDARY"]
    target = next((h for h in reversed(secs) if h != L13.PROD_HOSTS[0]), secs[-1] if secs else None)
    if not target:
        raise RuntimeError("no SECONDARY available")
    for _ in range(60):                                 # the target must have the whole collection
        m = next(x for x in adm.admin.command("replSetGetStatus")["members"] if x["name"] == target)
        p = next(x for x in adm.admin.command("replSetGetStatus")["members"] if x["stateStr"] == "PRIMARY")
        if (p["optimeDate"] - m["optimeDate"]).total_seconds() < 2:
            break
        time.sleep(1)
    log(f"   restarting {target} (not {L13.PROD_HOSTS[0]}: its exporter sidecar shares that container's network)")
    rs = restart_member(target)
    for _ in range(90):
        if L13.hello(target).get("secondary"):
            break
        time.sleep(1)
    log(f"   {target} is SECONDARY again after {rs} s + election of its state; its cache is cold")
    client, members, tick, events, sent = _drill_monitor("cache-drill")
    reader = L13.new_client("admin", direct=target, readPreference="secondaryPreferred", sst=8000)
    prev, _ = tick(None, log, quiet=True)
    out = {"target": target, "restart_s": rs, "scans": []}
    for label in ("cold scan (just after the restart)", "warm scan (same query again)"):
        t0 = time.perf_counter()
        n = sum(1 for _ in reader.lab14.cache_probe.find({}, {"n": 1}))
        secs_ = round(time.perf_counter() - t0, 2)
        time.sleep(1)
        prev, snap = tick(prev, log)
        row = next(r for r in snap["rows"] if r["member"] == target)
        out["scans"].append({"label": label, "docs": n, "seconds": secs_, "hit_ratio": row.get("hit_ratio"),
                             "pages_requested": row.get("pages_requested"), "pages_read_into": row.get("pages_read_into"),
                             "cache_mb": row.get("cache_mb")})
        hr = row.get("hit_ratio")
        log(f"   {label}: {n:,} documents in {secs_} s - pages requested {row.get('pages_requested')}, read into cache "
            f"{row.get('pages_read_into')} -> hit ratio {'-' if hr is None else f'{100 * hr:.1f}%'}")
    reader.close(); client.close(); members.close()
    out.update(events=events, webhooks=sent, threshold=CACHE_MIN,
               fired=any(e["status"] == "FIRING" and e["key"] == f"cache:{target}" for e in events),
               resolved=any(e["status"] == "RESOLVED" and e["key"] == f"cache:{target}" for e in events))
    out["pass"] = out["fired"] and out["resolved"]
    return out


def test_alert(fmt: str = "both", log=print) -> dict:
    e = {"status": "FIRING", "key": "test:website", "metric": "test_alert", "member": "website", "value": "test",
         "threshold": "-", "since": time.time(), "line": "TEST alert from the Lab 14 page"}
    urls = [u for u in webhook_urls() if fmt == "both" or fmt in u or u in EXTERNAL]
    sent = MD.notify(urls, [e], log=log)
    sent.append({"url": "http://127.0.0.1:9/unreachable", "status": "FIRING", "key": e["key"],
                 **MD.send_webhook("http://127.0.0.1:9/unreachable", MD.build_payload("x", e), timeout=2)})
    return {"sent": sent, "note": "the last URL is deliberately unreachable: try/except keeps the monitor alive"}


# ============================================================================ Lab 14.2 - backups
def target_spec(name: str) -> dict:
    if name == "billing":
        return {"name": "billing", "uri": uri("backup_app"), "db": "cti_billing",
                "label": "Lab 10 billing on the Lab 13 production set (backup_app)"}
    if name == "kavach":
        return {"name": "kavach", "uri": kavach_uri(), "db": config.DB_NAME, "label": "Kavach CTI platform (Lab 7, rs0)"}
    if name == "tasktracker":
        return {"name": "tasktracker", "uri": uri("backup_app"), "db": "tasktracker",
                "label": "Lab 15 TaskTracker SaaS on the production set (backup_app)"}
    if name == "prod-full":
        return {"name": "prod-full", "uri": uri("backup_app"), "db": None, "oplog": True,
                "label": "whole production set, --oplog (consistent snapshot)"}
    raise ValueError(f"unknown backup target {name}")


def backup_now(target: str = "billing", log=print) -> dict:
    t = target_spec(target)
    out = BACKUP_DIR / t["name"]
    out.mkdir(parents=True, exist_ok=True)
    BV.say = lambda m="": log(m)
    man = BV.run_backup(t["uri"], str(out), db=t["db"], oplog=t.get("oplog", False), gzip=True)
    ret = BV.apply_retention(str(out), log=log)
    doc = {"at": datetime.now(UTC), "target": t["name"], "label": t["label"], **{k: man.get(k) for k in
           ("name", "ok", "rc", "seconds", "bytes", "files", "output")}, "retention_deleted": len(ret["deleted"])}
    try:
        kdb()["lab14_backups"].insert_one(dict(doc))
    except PyMongoError:
        pass
    return _jd(doc)


def list_all_backups() -> dict:
    out = {}
    for t in ("billing", "kavach", "tasktracker", "prod-full", "verify"):
        out[t] = BV.list_backups(str(BACKUP_DIR / t))
    return out


def retention_demo(log=print) -> dict:
    d = BACKUP_DIR / "retention-demo"
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    BV.say = lambda m="": log(m)
    made = BV.simulate_history(str(d), log=log)
    real = BV.run_backup(uri("backup_app"), str(d), db="cti_billing", gzip=True, label="real")
    before = BV.list_backups(str(d))
    ret = BV.apply_retention(str(d), log=log)
    return _jd({"created": made, "real_backup": real.get("name"), "before": before, **ret})


def verify(log=print) -> dict:
    BV.say = lambda m="": log(m)
    (BACKUP_DIR / "verify").mkdir(parents=True, exist_ok=True)
    return _jd(BV.verify_restore(uri("admin"), str(BACKUP_DIR / "verify"), uri("backup_app"),
                                 alert=lambda m: _alert_text(m, "restore-verify")))


def _alert_text(msg: str, source: str):
    e = {"status": "FIRING", "key": f"{source}", "metric": "restore_verification", "member": "backup", "value": "mismatch",
         "threshold": "0 mismatches", "since": time.time(), "line": msg}
    sent = MD.notify(webhook_urls(), [e], log=lambda m: None)
    record_events([e], sent, source)


def pitr(log=print) -> dict:
    BV.say = lambda m="": log(m)
    (BACKUP_DIR / "pitr").mkdir(parents=True, exist_ok=True)
    return _jd(BV.pitr(uri("admin"), str(BACKUP_DIR / "pitr"), uri("backup_app"), uri("admin")))


def benchmark(sizes=(1000, 10000, 100000), log=print) -> dict:
    BV.say = lambda m="": log(m)
    (BACKUP_DIR / "bench").mkdir(parents=True, exist_ok=True)
    rows = BV.benchmark(uri("admin"), str(BACKUP_DIR / "bench"), uri("backup_app"), sizes=sizes)
    app = BV.app_restart_seconds(uri("admin"))
    rr = BV.rpo_rto(rows, interval_s=BACKUP_EVERY_MIN * 60, app_restart_s=app)
    log("   " + rr["explain"])
    return _jd({"rows": rows, "rpo_rto": rr, "pass": all(r["verified"] for r in rows)})


# ============================================================================ Lab 14 applied to Lab 13 / Lab 10
def _billing_digest(c) -> str:
    d = c["cti_billing"]
    return hashlib.sha256(json_util.dumps([sorted(d[x].find(), key=lambda y: str(y["_id"]))
                                           for x in ("accounts", "usage", "payments", "revenue")]).encode()).hexdigest()


def billing_dr(customers: int = 6, log=print) -> dict:
    from . import billing as B
    adm = L13.client("admin")
    if adm.cti_billing.accounts.estimated_document_count() == 0:
        log("   cti_billing is empty on the production set - copying it from the platform first (Lab 13 on 10)")
        try:
            L13.billing_copy(log=log)
        except Exception as exc:                        # noqa: BLE001
            log(f"   (copy not possible: {exc}) - starting from an empty ledger")
    B.ensure_schema(adm, log=lambda *a: None)
    bc = L13.new_client("billing_app", sst=5000, w="majority")
    work = BACKUP_DIR / "billing-dr" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    work.mkdir(parents=True, exist_ok=True)
    tl, BV.say = [], (lambda m="": log(m))

    def snap(what, **kw):
        inv = B.invariants(adm)
        s = {"what": what, "at": now_iso(), "accounts": adm.cti_billing.accounts.count_documents({}),
             "payments": adm.cti_billing.payments.count_documents({}), "invariants": inv, **kw}
        tl.append(s)
        log(f"   [{s['at'][11:19]}] {what}: {s['accounts']} accounts, {s['payments']} payments, wallets Rs {inv['wallets']:,}, "
            f"revenue Rs {inv['revenue']:,}, money conserved {inv['money_conserved']}, revenue = subscriptions {inv['revenue_matches']}")
        return s
    snap("before")
    t0 = BV._last_ts(adm)
    b = BV.tool("mongodump", uri("backup_app"), ["--db", "cti_billing", "--out", str(work / "base")])
    snap("T0 base backup of cti_billing", rc=b["rc"], seconds=b["seconds"])
    tag = f"{int(time.time()) % 1000000:06d}"
    for i in range(customers):
        u = B.sign_in(f"dr-{tag}-{i}@lab14.test", f"DR customer {i}", client=bc, synthetic=True)["user_id"]
        for k in range(3):
            B.consume(u, lambda: B._fake_work(1), client=bc, note=f"dr-{tag}-{i}-{k}")
        B.topup(u, 200, f"dr-t-{tag}-{i}", client=bc)
        B.subscribe(u, f"dr-s-{tag}-{i}", client=bc)
    good_digest = _billing_digest(adm)
    good = snap(f"{customers} new customers: 3 free uses, top-up Rs 200, plan Rs 100 (after the backup!)", sha256=good_digest[:16])
    last_good = BV._last_ts(adm)
    time.sleep(1.1)
    w = adm.cti_billing.accounts.update_many({}, {"$set": {"wallet": 0}}).modified_count
    p = adm.cti_billing.payments.delete_many({}).deleted_count
    bad = snap(f"ATTACK: {w} wallets set to Rs 0, {p} payments deleted")
    t_rec = time.perf_counter()
    rep = BV.oplog_slice(uri("backup_app"), work, t0, r"^cti_billing\.")
    r1 = BV.restore(uri("backup_app"), work / "base", "cti_billing.*")
    r2 = BV.tool("mongorestore", uri("admin"), ["--oplogReplay", f"--oplogLimit={last_good.time}:{last_good.inc + 1}", str(rep)])
    rto = round(time.perf_counter() - t_rec, 2)
    after_digest = _billing_digest(adm)
    after = snap("after point-in-time recovery to the second before the attack", sha256=after_digest[:16],
                 replay=r2["tail"][-2:] if r2["tail"] else [])
    bc.close()
    same = after_digest == good_digest and after["invariants"] == good["invariants"]
    out = {"timeline": tl, "customers_after_backup": customers, "attack": {"wallets_zeroed": w, "payments_deleted": p},
           "attack_detected_by": [k for k in ("money_conserved", "revenue_matches") if not bad["invariants"][k]],
           "base_restore_rc": r1["rc"], "replay_rc": r2["rc"], "recovery_s": rto, "digest_match": after_digest == good_digest,
           "pass": same and r1["rc"] == 0 and r2["rc"] == 0}
    log(f"   recovery took {rto} s; ledger identical to the moment before the attack: {out['digest_match']} "
        f"(the attack was visible to: {', '.join(out['attack_detected_by']) or 'NO invariant'})")
    return _jd(out)


# ============================================================================ the daemon (lab14 container)
def daemon(log=print):
    """Every 30 s: a monitoring sample (metrics.log + MongoDB + webhook on changes). Every hour: backups + retention.
    Both jobs are scheduled with the schedule library."""
    import schedule
    while True:                                         # the daemon may start before MongoDB: wait, never crash
        try:
            kdb().command("ping"); L13.client("admin").admin.command("ping")
            break
        except Exception as exc:                        # noqa: BLE001
            log(f"waiting for MongoDB ({type(exc).__name__})"); L13.reset_clients(); time.sleep(5)
    st = {"prev": None, "client": None, "members": None, "state": MD.AlertState(), "samples": 0, "last_backup": None}
    store, hb = _capped("lab14_metrics"), kdb()["lab14_daemon"]

    def monitor_job():
        try:
            if st["client"] is None:
                st["client"], st["members"] = MD.connect(uri("monitor_app")), MD.Members(uri("monitor_app"))
            cur = MD.collect(st["client"], st["members"])
            snap = MD.compute(cur, st["prev"])
            alerts = MD.check_thresholds(snap, CACHE_MIN, LAG_MAX) if st["prev"] else []
            table = MD.format_table(snap, alerts)
            MD.write_log(str(RESULTS / "metrics.log"), table)
            for a in alerts:
                log(a["line"])
            ev = st["state"].update(alerts)
            sent = MD.notify(webhook_urls(), ev, log=log) if ev else []
            record_events(ev, sent, "daemon")
            store.insert_one(compact(snap, alerts, "daemon"))
            st["prev"] = cur
            st["samples"] += 1
        except Exception as exc:                        # noqa: BLE001
            log(f"monitor: {type(exc).__name__}: {str(exc)[:160]}")
            st["client"] = None
            MD.write_log(str(RESULTS / "metrics.log"), f"{now_iso()[:19]} WARNING cannot sample: {exc}")
        beat()

    def backup_job():
        for t in TARGETS:
            try:
                r = backup_now(t, log=log)
                st["last_backup"] = {"at": now_iso(), "target": t, "ok": r.get("ok"), "name": r.get("name")}
            except Exception as exc:                    # noqa: BLE001
                log(f"backup {t}: {type(exc).__name__}: {str(exc)[:160]}")
        beat()

    def beat():
        jobs = {j.job_func.__name__: j.next_run for j in schedule.get_jobs()}
        hb.replace_one({"_id": "daemon"}, {"_id": "daemon", "at": datetime.now(UTC), "pid": os.getpid(),
                                           "interval_s": INTERVAL, "backup_every_min": BACKUP_EVERY_MIN, "targets": TARGETS,
                                           "samples": st["samples"], "last_backup": st["last_backup"],
                                           "next_backup": str(jobs.get("backup_job")), "retention_days": BV.RETENTION_DAYS},
                       upsert=True)
    schedule.every(INTERVAL).seconds.do(monitor_job)
    (schedule.every().hour if BACKUP_EVERY_MIN == 60 else schedule.every(BACKUP_EVERY_MIN).minutes).do(backup_job)
    log(f"lab14 daemon: monitor every {INTERVAL} s -> {RESULTS / 'metrics.log'}; backups of {', '.join(TARGETS)} every "
        f"{BACKUP_EVERY_MIN} min to {BACKUP_DIR} with {BV.RETENTION_DAYS}-day retention")
    monitor_job()
    backup_job()
    while True:
        schedule.run_pending()
        time.sleep(1)


def daemon_status() -> dict:
    try:
        d = kdb()["lab14_daemon"].find_one({"_id": "daemon"}) or {}
    except PyMongoError:
        d = {}
    if d.get("at"):
        age = (datetime.now(UTC) - d["at"].replace(tzinfo=UTC if d["at"].tzinfo is None else d["at"].tzinfo)).total_seconds()
        d["alive"] = age < INTERVAL * 3 + 15
        d["age_s"] = round(age)
    return _jd(d)


# ============================================================================ website status + results
def status() -> dict:
    out = {"available": False, "thresholds": {"cache_min": CACHE_MIN, "lag_max": LAG_MAX}, "interval_s": INTERVAL,
           "backup_dir": str(BACKUP_DIR), "external_webhooks": [MD._redact(u) for u in EXTERNAL]}
    av = L13.available()
    out.update(av)
    if av["available"]:
        try:
            out["rs"] = L13.rs_summary()
        except PyMongoError as exc:
            out["rs_error"] = str(exc)[:160]
        sec = L13.secrets()
        out["users_ready"] = all(u in sec for u in USERS)
    out["daemon"] = daemon_status()
    try:
        out["alerts_firing"] = firing()
    except PyMongoError:
        out["alerts_firing"] = []
    return _jd(out)


def firing() -> list:
    last = {}
    for a in kdb()["lab14_alerts"].find({"source": "daemon"}).sort("at", -1).limit(200):
        last.setdefault(a["key"], a)
    return _jd([a for a in last.values() if a["status"] != "RESOLVED"])


def save_part(name: str, data):
    p = RESULTS / "lab14_results.json"
    try:
        doc = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        doc = {}
    doc[name] = _jd(data)
    doc["updated_at"] = now_iso()
    p.write_text(json.dumps(doc, indent=2))
    try:
        kdb().lab_results.update_one({"_id": "lab_14"}, {"$set": {name: _jd(data), "updated_at": datetime.now(UTC)}}, upsert=True)
    except PyMongoError:
        pass


def load_results() -> dict:
    try:
        doc = kdb().lab_results.find_one({"_id": "lab_14"}, {"_id": 0})
        if doc:
            return _jd(doc)
    except PyMongoError:
        pass
    p = RESULTS / "lab14_results.json"
    return json.loads(p.read_text()) if p.exists() else {}

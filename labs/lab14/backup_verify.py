#!/usr/bin/env python3
"""Lab 14.2 - Automated backup and restore verification (RPO / RTO).

    python backup_verify.py --uri URI --out /backups backup          # mongodump -> /backups/backup-20261112T101500Z
    python backup_verify.py --uri URI --out /backups schedule        # every hour + 7-day retention (schedule library)
    python backup_verify.py --uri URI --out /backups retention       # delete backups older than 7 days
    python backup_verify.py --uri URI --out /backups verify          # 1,000 docs: backup, modify, mongorestore, assert
    python backup_verify.py --uri URI --out /backups pitr            # point-in-time restore to just before an accident
    python backup_verify.py --uri URI --out /backups benchmark       # 1,000 / 10,000 / 100,000 docs: dump + restore times
    python backup_verify.py --uri URI --out /backups all             # everything above except the endless scheduler

  --uri         the database (test data, verification). Accepts a MongoDB connection URI, as the lab asks.
  --backup-uri  optional least-privilege user for mongodump/mongorestore (roles backup + restore); default: --uri
  --replay-uri  user for `mongorestore --oplogReplay` (needs more than the restore role, see below); default: --uri

Solution text -> this file:
  subprocess.run(['mongodump', '--uri', uri, '--out', output_dir]) + return code   -> run_backup()
  schedule.every().hour.do(run_backup)                                            -> schedule_hourly()
  sort backup folders by creation time, shutil.rmtree() older than 7 days         -> apply_retention()
  1,000 docs in backup_test, backup, set verified:false, mongorestore --drop
  --nsInclude, compare count + field values in an assertion loop, alert          -> verify_restore()
  1,000 / 10,000 / 100,000 documents timed with the time module, RPO and RTO      -> benchmark(), rpo_rto()

The password is passed to mongodump/mongorestore in a private --config file (mode 600) instead of --uri on the command
line, where every user of the machine could read it with `ps` - same effect as the solution's --uri.

Measured on MongoDB 7.0.34 / database tools 100.13 (see README):
  * --oplogReplay as a user with only backup + restore applies inserts but is refused at the first update
    ("applyOps ... Unauthorized") - the collection is left HALF restored. Verification is what catches it.
  * deleteMany() is written to the oplog as batched applyOps entries in admin.$cmd, not as entries with the
    collection's namespace: the incident must be searched in o.applyOps.ns as well.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bson import json_util
from bson.timestamp import Timestamp
from pymongo import MongoClient
from pymongo.uri_parser import parse_uri

UTC = timezone.utc
PREFIX = "backup-"
RETENTION_DAYS = 7


def say(msg=""):
    print(msg, flush=True)


def redact(uri: str) -> str:
    try:
        p = parse_uri(uri)
        if p.get("password"):
            return uri.replace(p["password"], "***", 1)
    except Exception:                                   # noqa: BLE001
        pass
    return uri


def tool(name: str, uri: str, args: list, timeout: int = 1800) -> dict:
    """Run mongodump / mongorestore with the URI in a mode-600 config file. Returns rc, seconds and the output."""
    fd, cfg = tempfile.mkstemp(prefix="lab14-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(f"uri: {json.dumps(uri)}\n")
        os.chmod(cfg, 0o600)
        cmd = [name, f"--config={cfg}", *args]
        t0 = time.perf_counter()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        secs = time.perf_counter() - t0
    finally:
        os.unlink(cfg)
    out = (p.stdout + p.stderr).strip().splitlines()
    return {"rc": p.returncode, "seconds": round(secs, 3), "tail": [ln.split("\t")[-1] for ln in out[-4:]],
            "command": " ".join([name, "--uri", "'" + redact(uri) + "'", *args])}


def dir_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


# ============================================================================ backup
def run_backup(uri: str, out_dir: str, *, db: str | None = None, collection: str | None = None, oplog: bool = False,
               gzip: bool = False, label: str = "", now: datetime | None = None) -> dict:
    """mongodump to <out>/backup-YYYYmmddTHHMMSSZ; the return code is captured; a manifest.json describes the backup."""
    now = now or datetime.now(UTC)
    target = Path(out_dir) / f"{PREFIX}{now:%Y%m%dT%H%M%S}Z{('-' + label) if label else ''}"
    n = 1
    while target.exists():
        target = target.with_name(target.name + f"-{n}"); n += 1
    args = ["--out", str(target)]
    if db:
        args += ["--db", db]
    if collection:
        args += ["--collection", collection]
    if oplog:
        args += ["--oplog"]           # consistent snapshot of a replica set: also captures writes made during the dump
    if gzip:
        args += ["--gzip"]
    r = tool("mongodump", uri, args)
    man = {"name": target.name, "created_at": now.isoformat(), "rc": r["rc"], "ok": r["rc"] == 0, "seconds": r["seconds"],
           "db": db or "(all)", "collection": collection, "oplog": oplog, "gzip": gzip, "label": label,
           "command": r["command"], "output": r["tail"]}
    if target.exists():
        man["bytes"] = dir_bytes(target)
        man["files"] = sum(1 for f in target.rglob("*.bson*") if f.is_file())
        (target / "manifest.json").write_text(json.dumps(man, indent=2))
    say(f"   mongodump -> {target.name}: rc {r['rc']}, {r['seconds']:.2f} s, {man.get('bytes', 0) / 1048576:.2f} MB"
        + ("" if r["rc"] == 0 else f"  !! {' | '.join(r['tail'])}"))
    man["path"] = str(target)
    return man


def restore(uri: str, path: str, ns_include: str | None = None, drop: bool = True, gzip: bool = False, extra=None) -> dict:
    args = (["--drop"] if drop else []) + ([f"--nsInclude={ns_include}"] if ns_include else []) + \
        (["--gzip"] if gzip else []) + list(extra or []) + [str(path)]
    return tool("mongorestore", uri, args)


# ============================================================================ schedule + retention
def created_at(p: Path) -> datetime:
    """Creation time of a backup folder: the manifest (written by run_backup) or, failing that, the folder mtime."""
    try:
        return datetime.fromisoformat(json.loads((p / "manifest.json").read_text())["created_at"])
    except Exception:                                   # noqa: BLE001
        return datetime.fromtimestamp(p.stat().st_mtime, UTC)


def list_backups(out_dir: str) -> list:
    d = Path(out_dir)
    if not d.exists():
        return []
    rows = []
    for p in d.iterdir():
        if p.is_dir() and p.name.startswith(PREFIX):
            try:
                man = json.loads((p / "manifest.json").read_text())
            except Exception:                           # noqa: BLE001
                man = {}
            rows.append({"name": p.name, "created_at": created_at(p).isoformat(), "bytes": man.get("bytes", dir_bytes(p)),
                         "ok": man.get("ok"), "seconds": man.get("seconds"), "db": man.get("db"),
                         "simulated": man.get("simulated", False), "label": man.get("label", "")})
    return sorted(rows, key=lambda r: r["created_at"])                 # sorted by creation time, oldest first


def apply_retention(out_dir: str, days: int = RETENTION_DAYS, now: datetime | None = None, log=say) -> dict:
    now = now or datetime.now(UTC)
    limit = now - timedelta(days=days)
    kept, deleted = [], []
    for b in list_backups(out_dir):                    # oldest first
        age_h = (now - datetime.fromisoformat(b["created_at"])).total_seconds() / 3600
        row = {"name": b["name"], "age_h": round(age_h, 1), "simulated": b["simulated"]}
        if datetime.fromisoformat(b["created_at"]) < limit:
            shutil.rmtree(Path(out_dir) / b["name"])
            deleted.append(row)
        else:
            kept.append(row)
    log(f"   retention {days} days: deleted {len(deleted)} backup(s) older than {limit:%Y-%m-%d %H:%M} UTC, kept {len(kept)}")
    return {"days": days, "limit": limit.isoformat(), "kept": kept, "deleted": deleted}


def simulate_history(out_dir: str, ages_h=(240, 200, 169, 167, 120, 48, 24, 1), log=say) -> list:
    """Retention can't be shown by waiting 8 days: create placeholder backups dated in the past (flagged simulated)."""
    made = []
    for h in ages_h:
        ts = datetime.now(UTC) - timedelta(hours=h)
        p = Path(out_dir) / f"{PREFIX}{ts:%Y%m%dT%H%M%S}Z-simulated"
        p.mkdir(parents=True, exist_ok=True)
        (p / "manifest.json").write_text(json.dumps({"name": p.name, "created_at": ts.isoformat(), "simulated": True,
                                                     "ok": True, "bytes": 0}))
        os.utime(p, (ts.timestamp(), ts.timestamp()))
        made.append({"name": p.name, "age_h": h})
    log(f"   created {len(made)} simulated backups aged {', '.join(str(h) + ' h' for h in ages_h)}")
    return made


def schedule_hourly(uri: str, out_dir: str, every_minutes: int | None = None, **kw):
    """schedule.every().hour.do(run_backup) - each run is followed by the 7-day retention policy."""
    import schedule

    def job():
        run_backup(uri, out_dir, **kw)
        apply_retention(out_dir)
    (schedule.every(every_minutes).minutes if every_minutes else schedule.every().hour).do(job)
    say(f"scheduler: backup every {f'{every_minutes} min' if every_minutes else 'hour'} to {out_dir}, retention "
        f"{RETENTION_DAYS} days (Ctrl+C to stop). First run now.")
    job()
    while True:
        schedule.run_pending()
        time.sleep(1)


# ============================================================================ restore verification
def _client(uri):
    return MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True, w="majority")


def fingerprint(coll) -> str:
    return hashlib.sha256(json_util.dumps(list(coll.find().sort("_id", 1))).encode()).hexdigest()


def compare(coll, before: dict) -> list:
    """The assertion loop: every original document must exist with every field value unchanged."""
    bad = []
    now = {d["_id"]: d for d in coll.find()}
    if len(now) != len(before):
        bad.append({"_id": None, "problem": f"count {len(now)} != {len(before)}"})
    for _id, doc in before.items():
        try:
            assert _id in now, "missing"
            for k, v in doc.items():
                assert now[_id].get(k) == v, f"{k}: {now[_id].get(k)!r} != {v!r}"
        except AssertionError as exc:
            bad.append({"_id": _id, "problem": str(exc)})
    return bad


def verify_restore(uri: str, out_dir: str, backup_uri: str | None = None, n: int = 1000, db: str = "lab14",
                   name: str = "backup_test", alert=None) -> dict:
    backup_uri = backup_uri or uri
    c = _client(uri)
    coll = c[db][name]
    ns = f"{db}.{name}"
    say(f"   1. insert {n:,} test documents with timestamps into {ns}")
    coll.drop()
    base = datetime.now(UTC).replace(microsecond=0)
    coll.insert_many([{"_id": i, "seq": i, "created_at": base + timedelta(milliseconds=i), "customer": f"cust-{i % 97:02d}",
                       "amount": round(random.uniform(10, 5000), 2), "verified": True} for i in range(n)])
    before = {d["_id"]: d for d in coll.find()}
    fp0 = fingerprint(coll)
    say(f"   2. take a backup (count {len(before):,}, sha256 {fp0[:16]})")
    b = run_backup(backup_uri, out_dir, db=db, collection=name, label="verify")
    say("   3. modify the data: update_many({}, {$set: {verified: false}}) + delete 10 documents")
    m = coll.update_many({}, {"$set": {"verified": False}}).modified_count
    coll.delete_many({"_id": {"$lt": 10}})
    neg = compare(coll, before)
    say(f"   negative control: the checker finds {len(neg):,} mismatches in the modified data (it CAN fail)")
    say(f"   4. mongorestore --drop --nsInclude={ns} {Path(b['path']).name}")
    t0 = time.perf_counter()
    r = restore(backup_uri, b["path"], ns)
    rsecs = round(time.perf_counter() - t0, 3)
    bad = compare(coll, before)
    fp1 = fingerprint(coll)
    ok = r["rc"] == 0 and not bad and fp1 == fp0
    say(f"   5. verify: count {coll.count_documents({}):,} of {n:,}, field mismatches {len(bad)}, sha256 {fp1[:16]} "
        f"-> {'ORIGINAL DATA RECOVERED' if ok else 'MISMATCH'}")
    if not ok:
        msg = f"ALERT: restore verification of {ns} failed - {len(bad)} mismatches, e.g. {bad[:3]}"
        say(msg)                                        # any mismatch triggers an alert printed to stdout
        if alert:
            alert(msg)
    c.close()
    return {"ns": ns, "docs": n, "backup": {k: b.get(k) for k in ("name", "rc", "seconds", "bytes")},
            "modified": m, "deleted_after_backup": 10, "mismatches_before_restore": len(neg),
            "restore_rc": r["rc"], "restore_seconds": rsecs, "restore_output": r["tail"], "mismatches_after_restore": len(bad),
            "examples": bad[:3], "sha256_before": fp0, "sha256_after": fp1, "pass": ok}


# ============================================================================ point-in-time restore
def _last_ts(c) -> Timestamp:
    return c.local["oplog.rs"].find({}, {"ts": 1}).sort("$natural", -1).limit(1).next()["ts"]


def oplog_slice(backup_uri: str, out_dir: Path, since: Timestamp, ns_regex: str) -> Path:
    """Dump the oplog entries of the namespaces after `since` - including those wrapped in applyOps (batched deletes,
    transactions) - into a folder that mongorestore --oplogReplay can read (oplog.bson at its root)."""
    q = {"ts": {"$gt": {"$timestamp": {"t": since.time, "i": since.inc}}},
         "$or": [{"ns": {"$regex": ns_regex}}, {"o.applyOps.ns": {"$regex": ns_regex}}]}
    r = tool("mongodump", backup_uri, ["--db", "local", "--collection", "oplog.rs", "--query", json.dumps(q),
                                       "--out", str(out_dir / "oplog-dump")])
    if r["rc"]:
        raise RuntimeError("oplog dump failed: " + " | ".join(r["tail"]))
    rep = out_dir / "replay"
    rep.mkdir(parents=True, exist_ok=True)
    shutil.copy(out_dir / "oplog-dump" / "local" / "oplog.rs.bson", rep / "oplog.bson")
    return rep


def find_incident(c, since: Timestamp, ns: str):
    """First delete on the namespace after `since`: a plain 'd' entry OR one inside a batched applyOps (7.0)."""
    return c.local["oplog.rs"].find_one({"ts": {"$gt": since}, "$or": [{"ns": ns, "op": "d"},
                                                                      {"o.applyOps": {"$elemMatch": {"ns": ns, "op": "d"}}}]},
                                        sort=[("$natural", 1)])


def pitr(uri: str, out_dir: str, backup_uri: str | None = None, replay_uri: str | None = None, db: str = "lab14",
         name: str = "pitr_orders") -> dict:
    backup_uri, replay_uri = backup_uri or uri, replay_uri or uri
    c = _client(uri)
    coll, ns = c[db][name], f"{db}.{name}"
    work = Path(out_dir) / f"pitr-{datetime.now(UTC):%Y%m%dT%H%M%S}Z"
    work.mkdir(parents=True, exist_ok=True)
    tl = []

    def ev(what, **kw):
        tl.append({"at": datetime.now(UTC).isoformat(), "what": what, "count": coll.count_documents({}), **kw})
        say(f"   [{tl[-1]['at'][11:19]}] {what} (documents: {tl[-1]['count']:,})")
    coll.drop()
    coll.insert_many([{"_id": i, "amount": 100 + i, "status": "new"} for i in range(500)])
    ev("500 orders exist")
    t0 = _last_ts(c)
    b = tool("mongodump", backup_uri, ["--db", db, "--collection", name, "--out", str(work / "base")])
    ev("T0 base backup (mongodump)", ts=f"{t0.time}:{t0.inc}", rc=b["rc"], seconds=b["seconds"])
    coll.insert_many([{"_id": 500 + i, "amount": 7, "status": "new"} for i in range(200)])
    coll.update_many({"_id": {"$lt": 50}}, {"$set": {"status": "paid"}})
    good_count, good_fp = coll.count_documents({}), fingerprint(coll)
    ev("business continues: +200 orders, 50 marked paid", sha256=good_fp[:16])
    time.sleep(1.2)
    coll.delete_many({})
    inc = find_incident(c, t0, ns)
    if not inc:
        raise RuntimeError("incident not found in the oplog")
    ev("INCIDENT: deleteMany({}) by mistake", ts=f"{inc['ts'].time}:{inc['ts'].inc}", oplog_ns=inc["ns"],
       batched=inc["ns"] == "admin.$cmd")
    rep = oplog_slice(backup_uri, work, t0, "^" + ns.replace(".", "\\.") + "$")
    limit = f"--oplogLimit={inc['ts'].time}:{inc['ts'].inc}"            # replay everything BEFORE the incident

    attempts = []
    for who, ruri in (("backup_app (backup + restore roles)", backup_uri), ("admin", replay_uri)):
        if attempts and attempts[-1]["pass"]:
            break
        if attempts and ruri == attempts[-1]["uri"]:
            break
        r1 = restore(backup_uri, work / "base", ns)
        r2 = tool("mongorestore", ruri, ["--oplogReplay", limit, str(rep)])
        cnt, fp = coll.count_documents({}), fingerprint(coll)
        ok = r1["rc"] == 0 and r2["rc"] == 0 and fp == good_fp
        attempts.append({"user": who, "uri": ruri, "base_rc": r1["rc"], "replay_rc": r2["rc"], "replay_output": r2["tail"],
                         "count": cnt, "sha256": fp[:16], "pass": ok, "seconds": round(r1["seconds"] + r2["seconds"], 2)})
        ev(f"restore base + oplog replay to just before the incident, as {who}", replay_rc=r2["rc"], match=ok,
           replay=(r2["tail"][-2] if len(r2["tail"]) > 1 else r2["tail"][-1:]))
        if not ok:
            say(f"   ALERT: restored data does NOT match the pre-incident state ({cnt} docs, sha256 {fp[:16]} != "
                f"{good_fp[:16]}): {' | '.join(r2['tail'][-2:])[:220]}")
    for a in attempts:
        a.pop("uri", None)
    c.close()
    return {"ns": ns, "timeline": tl, "expected_count": good_count, "expected_sha256": good_fp[:16],
            "incident_in_admin_cmd": inc["ns"] == "admin.$cmd", "attempts": attempts,
            "pass": bool(attempts and attempts[-1]["pass"])}


# ============================================================================ benchmark + RPO/RTO
def benchmark(uri: str, out_dir: str, backup_uri: str | None = None, sizes=(1000, 10000, 100000), db: str = "lab14",
              gzip: bool = True) -> list:
    backup_uri = backup_uri or uri
    c = _client(uri)
    rows = []
    for n in sizes:
        name = f"bench_{n}"
        coll = c[db][name]
        coll.drop()
        say(f"   {n:>7,} documents: generating ~1 KB each ...")
        for i in range(0, n, 5000):
            coll.insert_many([{"_id": j, "created_at": datetime.now(UTC), "customer": f"cust-{j % 997}", "amount": j % 5000,
                               "tags": ["ioc", "report", "kavach"][: j % 3 + 1], "payload": os.urandom(440).hex()}
                              for j in range(i, min(i + 5000, n))], ordered=False)
        st = next(coll.aggregate([{"$collStats": {"storageStats": {}}}]))["storageStats"]
        d = run_backup(backup_uri, out_dir, db=db, collection=name, label=f"bench{n}")
        g = run_backup(backup_uri, out_dir, db=db, collection=name, label=f"bench{n}-gz", gzip=True) if gzip else {}
        t0 = time.perf_counter()
        r = restore(backup_uri, d["path"], f"{db}.{name}")
        rs = time.perf_counter() - t0
        cnt = coll.count_documents({})
        row = {"docs": n, "data_mb": round(st["size"] / 1048576, 2), "storage_mb": round(st["storageSize"] / 1048576, 2),
               "dump_s": d["seconds"], "dump_mb": round(d.get("bytes", 0) / 1048576, 2),
               "gzip_s": g.get("seconds"), "gzip_mb": round(g.get("bytes", 0) / 1048576, 2) if g else None,
               "restore_s": round(rs, 3), "restore_rc": r["rc"], "restored": cnt, "verified": cnt == n and r["rc"] == 0,
               "dump_mb_s": round(d.get("bytes", 0) / 1048576 / max(d["seconds"], 1e-3), 1),
               "restore_docs_s": round(n / max(rs, 1e-3))}
        rows.append(row)
        say(f"           data {row['data_mb']} MB | mongodump {row['dump_s']:.2f} s ({row['dump_mb']} MB) | gzip "
            f"{row['gzip_s'] or 0:.2f} s ({row['gzip_mb']} MB) | mongorestore {row['restore_s']:.2f} s | restored {cnt:,} "
            f"-> {'OK' if row['verified'] else 'FAIL'}")
        for p in (d.get("path"), g.get("path")):
            if p:
                shutil.rmtree(p, ignore_errors=True)
        coll.drop()
    c.close()
    return rows


def app_restart_seconds(uri: str) -> float:
    """Application restart: a new Python process connects and answers its first query (measured, not assumed)."""
    code = ("import sys,time;t=time.perf_counter();from pymongo import MongoClient;"
            "c=MongoClient(sys.argv[1],serverSelectionTimeoutMS=8000);c.admin.command('ping');"
            "c['lab14'].list_collection_names();print(round(time.perf_counter()-t,3))")
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, "-c", code, uri], capture_output=True, text=True, timeout=60)
    return round(time.perf_counter() - t0, 3) if p.returncode == 0 else float("nan")


def rpo_rto(rows: list, interval_s: int = 3600, app_restart_s: float = 0.0) -> dict:
    big = max(rows, key=lambda r: r["docs"])
    return {"backup_interval_s": interval_s, "rpo_s": interval_s,
            "rpo_worst_s": round(interval_s + big["dump_s"], 1),
            "restore_s": big["restore_s"], "app_restart_s": app_restart_s,
            "rto_s": round(big["restore_s"] + app_restart_s, 2), "for_docs": big["docs"],
            "explain": (f"RPO = backup interval = {interval_s // 60} min: a failure just before the next hourly backup loses up to "
                        f"{interval_s // 60} min of writes (+ {big['dump_s']:.1f} s while the dump runs). RTO = restore time "
                        f"{big['restore_s']:.1f} s + application restart {app_restart_s:.1f} s = "
                        f"{big['restore_s'] + app_restart_s:.1f} s for {big['docs']:,} documents. With the oplog dump of the "
                        "point-in-time restore the RPO shrinks to the moment of the incident.")}


# ============================================================================ CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uri", required=True, help="MongoDB connection URI")
    ap.add_argument("--out", required=True, help="output directory for the backups")
    ap.add_argument("--backup-uri")
    ap.add_argument("--replay-uri")
    ap.add_argument("--every-minutes", type=int, help="(schedule) instead of every hour - for a demonstration")
    ap.add_argument("action", nargs="?", default="backup",
                    choices=["backup", "schedule", "retention", "verify", "pitr", "benchmark", "all"])
    a = ap.parse_args()
    Path(a.out).mkdir(parents=True, exist_ok=True)
    res = {}
    if a.action in ("backup", "all"):
        say("== backup"); res["backup"] = run_backup(a.backup_uri or a.uri, a.out)
    if a.action == "schedule":
        schedule_hourly(a.backup_uri or a.uri, a.out, a.every_minutes)
    if a.action in ("retention", "all"):
        say("== retention"); res["retention"] = apply_retention(a.out)
    if a.action in ("verify", "all"):
        say("== restore verification"); res["verify"] = verify_restore(a.uri, a.out, a.backup_uri)
    if a.action in ("pitr", "all"):
        say("== point-in-time restore"); res["pitr"] = pitr(a.uri, a.out, a.backup_uri, a.replay_uri)
    if a.action in ("benchmark", "all"):
        say("== benchmark"); rows = benchmark(a.uri, a.out, a.backup_uri)
        res["benchmark"] = {"rows": rows, "rpo_rto": rpo_rto(rows, app_restart_s=app_restart_seconds(a.uri))}
        say("   " + res["benchmark"]["rpo_rto"]["explain"])
    ok = all(v.get("pass", v.get("ok", True)) for v in res.values() if isinstance(v, dict))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""Exercise 14 - Managing database services in production: run every Lab 14 task and save the evidence.

    python labs/lab14/run_lab14.py                 # everything (about 3-5 minutes)
    python labs/lab14/run_lab14.py --quick         # shorter: no cold-cache drill, benchmark 1,000 + 10,000 only
    python labs/lab14/run_lab14.py --only 14.1     # one part:  users | 14.1 | activity | 14.2 | dr
    python labs/lab14/run_lab14.py --report        # also build results/Exercise-14_Report.pdf

Console -> results/console_14.txt, numbers -> results/lab14_results.json and MongoDB cti_platform.lab_results (_id lab_14),
monitoring tables -> results/metrics.log.
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from backend.app import lab14 as L  # noqa: E402

RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
RESULTS.mkdir(parents=True, exist_ok=True)


class Tee:
    def __init__(self, path):
        self.f, self.o = open(path, "w", encoding="utf-8"), sys.stdout

    def write(self, s):
        self.o.write(s); self.f.write(s)

    def flush(self):
        self.o.flush(); self.f.flush()


def banner(t, ch="="):
    print(f"\n{ch * 76}\n{t}\n{ch * 76}")


def guarded(name, fn, *a, **kw):
    t0 = time.time()
    try:
        out = fn(*a, **kw)
    except Exception as exc:                           # noqa: BLE001 - one failing part must not hide the others
        print(f"!! {name} failed: {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=3, file=sys.stdout)
        out = {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "pass": False}
    if isinstance(out, dict):
        out.setdefault("seconds", round(time.time() - t0, 1))
    return out


def environment():
    import pymongo
    import subprocess
    v = subprocess.run(["mongodump", "--version"], capture_output=True, text=True).stdout.splitlines()
    rs = L.L13.rs_summary()
    env = {"pymongo": pymongo.version, "at": datetime.now(timezone.utc).isoformat(), "prod_hosts": L.L13.PROD_HOSTS,
           "replica_set": rs["set"], "primary": rs["primary"], "tools": v[0] if v else "?", "backup_dir": str(L.BACKUP_DIR),
           "thresholds": {"cache_min": L.CACHE_MIN, "lag_max": L.LAG_MAX}, "interval_s": L.INTERVAL,
           "members": [f"{m['name']} {m['state']}" for m in rs["members"]]}
    print(f"production set {env['replica_set']}: {', '.join(env['members'])} | {env['tools']} | PyMongo {env['pymongo']}")
    return env


def part_users():
    banner("Setup - least-privilege users for monitoring and backups (on the Lab 13 production set)")
    return guarded("users", L.ensure_users)


def part_141():
    banner("Lab 14.1 - Build a monitoring dashboard")
    print("Step 1: connect as monitor_app (clusterMonitor) -> serverStatus of every member + replSetGetStatus")
    print("Step 2: table -> results/metrics.log (the daemon appends one every 30 s; here two samples 10 s apart)")
    out = {}

    def two():
        m = L.MD
        c, ms = m.connect(L.uri("monitor_app")), m.Members(L.uri("monitor_app"))
        p = m.collect(c, ms)
        time.sleep(10)
        cur = m.collect(c, ms)
        snap = m.compute(cur, p)
        al = m.check_thresholds(snap, L.CACHE_MIN, L.LAG_MAX)
        t = m.format_table(snap, al)
        print(t)
        m.write_log(str(RESULTS / "metrics.log"), "[lab14 14.1]\n" + t)
        c.close(); ms.close()
        return {"table": t, "snapshot": L.compact(snap, al, "14.1"), "warnings": [a["line"] for a in al], "pass": True}
    out["sample"] = guarded("sample", two)
    print("\nStep 3: thresholds - WARNING below 80 % cache hit ratio or above 10 s replication lag")
    print(f"        (in a healthy set nothing fires: warnings now = {len((out['sample'] or {}).get('warnings', []))}); "
          "the drills below make both fire for real")
    return out


def part_activity(quick):
    banner("Activity - alerts to a webhook (Slack / Discord) + drills that breach the thresholds")
    out = {"webhook_test": guarded("webhook test", L.test_alert)}
    print("\nDrill 1 - replication lag: fsyncLock() a secondary while the application keeps writing")
    out["lag_drill"] = guarded("lag drill", L.lag_drill)
    if not quick:
        print("\nDrill 2 - cold cache: restart a secondary, read 60,000 documents from it, then read them again")
        out["cache_drill"] = guarded("cache drill", L.cache_drill)
    return out


def part_142(quick):
    banner("Lab 14.2 - Automated backup and restore verification")
    out = {}
    print("Backups (what the daemon runs every hour): mongodump -> timestamped folder, gzip, then 7-day retention")
    out["backups"] = [guarded(f"backup {t}", L.backup_now, t) for t in L.TARGETS]
    print("\nRetention policy (7 days) on simulated history")
    out["retention"] = guarded("retention", L.retention_demo)
    print("\nRestore verification - 1,000 documents")
    out["verify"] = guarded("verify", L.verify)
    print("\nPoint-in-time restore - base backup + oplog replay to the second before an accidental deleteMany")
    out["pitr"] = guarded("pitr", L.pitr)
    print("\nBenchmark - backup and restore times, RPO and RTO")
    out["benchmark"] = guarded("benchmark", L.benchmark, (1000, 10000) if quick else (1000, 10000, 100000))
    return out


def part_dr():
    banner("Lab 14 applied to Lab 13 + Lab 10 - disaster recovery of the classifier billing on the production set")
    return guarded("billing DR", L.billing_dr)


def checks(o):
    a, b, act = o.get("lab_14_2") or {}, o.get("lab_14_1") or {}, o.get("activity") or {}
    lg, cd = act.get("lag_drill") or {}, act.get("cache_drill")
    w = act.get("webhook_test") or {}
    c = []
    if o.get("users"):
        m = {r["user"]: r["ops"] for r in o["users"].get("matrix", [])}
        c.append(("monitor_app reads metrics but no data (find on cti_billing refused)",
                  m.get("monitor_app", {}).get("serverStatus") == "allowed" and
                  m.get("monitor_app", {}).get("read billing (find cti_billing.accounts)") == "refused"))
        c.append(("backup_app cannot update or delete application data",
                  m.get("backup_app", {}).get("update (lab14.priv_test)") == "refused" and
                  m.get("backup_app", {}).get("delete (lab14.priv_test)") == "refused"))
    if b:
        c.append(("14.1 table of every member written to metrics.log", (b.get("sample") or {}).get("pass", False)))
    if act:
        c.append(("Webhook delivered (Slack + Discord format) and a dead URL handled by try/except",
                  any(s.get("ok") for s in w.get("sent", [])) and any(not s.get("ok") for s in w.get("sent", []))))
        c.append((f"Replication lag drill: WARNING > 10 s fired and resolved, writes continued (max {lg.get('max_lag_s', 0):.0f} s)",
                  lg.get("pass", False)))
        if cd is not None:
            c.append(("Cold cache drill: hit ratio < 80 % fired, warm read resolved it", cd.get("pass", False)))
    if a:
        c.append(("Backups ran with return code 0", all(x.get("ok") for x in a.get("backups", []))))
        r = a.get("retention") or {}
        c.append(("Retention deleted exactly the backups older than 7 days",
                  bool(r.get("deleted")) and all(x["age_h"] > 168 for x in r.get("deleted", [])) and
                  all(x["age_h"] <= 168 for x in r.get("kept", []))))
        v = a.get("verify") or {}
        c.append(("Restore verification: 1,000 documents identical after mongorestore (checker proved it can fail)",
                  v.get("pass", False) and v.get("mismatches_before_restore", 0) > 0))
        c.append(("Point-in-time restore matches the state just before the incident", (a.get("pitr") or {}).get("pass", False)))
        c.append(("Benchmark: every size restored completely", (a.get("benchmark") or {}).get("pass", False)))
    if o.get("billing_dr"):
        c.append(("Billing ledger recovered to the second before the attack (digest + invariants)", o["billing_dr"].get("pass", False)))
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", choices=["users", "14.1", "activity", "14.2", "dr"])
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    sys.stdout = Tee(RESULTS / ("console_14.txt" if not a.only else f"console_14_{a.only}.txt"))
    print(f"root@lab14-runner:/app# lab14 {'--only ' + a.only if a.only else 'all'}{' --quick' if a.quick else ''}")
    path = RESULTS / "lab14_results.json"
    out = {}
    if path.exists():
        try:
            out = json.loads(path.read_text())
        except ValueError:
            out = {}
    out.update({"environment": guarded("environment", environment), "quick": a.quick})
    if a.only in (None, "users"):
        out["users"] = part_users()
    if a.only in (None, "14.1"):
        out["lab_14_1"] = part_141()
    if a.only in (None, "activity"):
        out["activity"] = part_activity(a.quick)
    if a.only in (None, "14.2"):
        out["lab_14_2"] = part_142(a.quick)
    if a.only in (None, "dr"):
        out["billing_dr"] = part_dr()
    for k in ("lag_drill", "cache_drill"):                 # the website reads the latest drills here too
        if (out.get("activity") or {}).get(k):
            out[k] = out["activity"][k]
    for k in ("verify", "pitr", "benchmark", "retention"):
        if (out.get("lab_14_2") or {}).get(k):
            out[k] = out["lab_14_2"][k]

    banner("SUMMARY")
    ok = True
    for name, v in checks(out):
        print(f"  [{'PASS' if v else 'FAIL'}] {name}")
        ok &= v
    out["checks"] = [{"name": n, "ok": v} for n, v in checks(out)]
    out = json.loads(json.dumps(out, default=str))
    path.write_text(json.dumps(out, indent=2))
    try:
        L.kdb().lab_results.replace_one({"_id": "lab_14"}, {"_id": "lab_14", "ran_at": datetime.now(timezone.utc), **out},
                                        upsert=True)
    except Exception as exc:                           # noqa: BLE001
        print(f"(could not store results in MongoDB rs0: {str(exc)[:120]})")
    print("\nSaved results/lab14_results.json, results/console_14.txt, results/metrics.log and MongoDB cti_platform.lab_results (_id lab_14)")
    if a.report and not a.only:
        sys.stdout.flush()
        import importlib.util
        spec = importlib.util.spec_from_file_location("lab14_report", Path(__file__).resolve().parent / "make_report.py")
        mr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)
        pdf = mr.build(out, RESULTS, os.getenv("DOCKER_URL", ""))
        print(f"Report written: {pdf}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()

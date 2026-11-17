"""Exercise 15 - Designing and architecting a MongoDB-backed product as a service: run every task, save the evidence.

    python labs/lab15/run_lab15.py              # everything (about 2-3 minutes)
    python labs/lab15/run_lab15.py --quick      # Lab 15.2 only at 5 x 100 (no 20,000 / 100-tenant scales)
    python labs/lab15/run_lab15.py --only crud  # one part: applied | crud | rate | rbac | migration
    python labs/lab15/run_lab15.py --report     # also results/Exercise-15_Report.pdf
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
from backend.app import lab15 as L  # noqa: E402

RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
RESULTS.mkdir(parents=True, exist_ok=True)


class Tee:
    def __init__(self, path):
        self.f, self.o = open(path, "w", encoding="utf-8"), sys.stdout

    def write(self, s):
        self.o.write(s); self.f.write(s)

    def flush(self):
        self.o.flush(); self.f.flush()


def banner(t):
    print(f"\n{'=' * 76}\n{t}\n{'=' * 76}")


def guarded(name, fn, *a, **kw):
    t0 = time.time()
    try:
        out = fn(*a, **kw)
    except Exception as exc:                           # noqa: BLE001
        print(f"!! {name} failed: {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=3, file=sys.stdout)
        out = {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "pass": False}
    if isinstance(out, dict):
        out.setdefault("seconds", round(time.time() - t0, 1))
    return out


PARTS = [("applied", "Applied to Labs 14 -> 7: least privilege, transaction, backup, deployment", L.run_applied),
         ("crud", "Lab 15.1 - multi-tenant API: CRUD, tenant isolation, validation, the classroom sample", L.run_151),
         ("rate", "Lab 15.1 - rate limiting with MongoDB as the counter store", L.run_rate),
         ("rbac", "Activity 15.1 - role-based access control (admin / member)", L.run_rbac)]


def checks(o):
    c = []
    if o.get("applied"):
        a = o["applied"]
        c.append(("tasktracker_app may use only its database (billing, platform, collMod, createUser refused)",
                  a["least_privilege"]["matrix"].get("read cti_billing (Lab 10)") == "refused" and
                  a["least_privilege"]["matrix"].get("collMod (change / switch off the validator)") == "refused"))
        c.append(("Signup = tenant + admin in one transaction (failed signup rolled back)", a.get("transaction", {}).get("tenant_rolled_back", False)))
        c.append(("TaskTracker backed up by the Lab 14 tools", bool(a.get("backup", {}).get("ok"))))
    if o.get("lab_15_1"):
        t = o["lab_15_1"].get("tests", [])
        c.append((f"CRUD, isolation, tokens, validation: {sum(r['pass'] for r in t)}/{len(t)} HTTP tests", o["lab_15_1"].get("pass", False)))
        e = o["lab_15_1"].get("explain", {})
        c.append(("List query uses the (tenant_id, created_at) index, docs examined = returned",
                  e.get("stage") == "IXSCAN" and e.get("docs_examined") == e.get("returned")))
    if o.get("rate"):
        r = o["rate"]
        c.append((f"Rate limit: {r['burst']['ok']} allowed / {r['burst']['limited']} x 429 of 70, other tenant unaffected", r.get("pass", False)))
        c.append((f"Atomic counter: {r['concurrency']['allowed']} of 200 concurrent requests allowed (limit 50)", r["concurrency"]["allowed"] == 50))
    if o.get("rbac"):
        t = o["rbac"].get("tests", [])
        c.append((f"RBAC: {sum(r['pass'] for r in t)}/{len(t)} cases (members own tasks, admins all, other tenants 404)", o["rbac"].get("pass", False)))
    if o.get("lab_15_2"):
        m = o["lab_15_2"]
        c.append(("15.2 migration: 500/500 documents, no cross-tenant leakage (negative control detected)", m.get("pass", False)))
        b = m.get("backup", {})
        if b.get("pitfall_drop_on_shared"):
            c.append(("One-tenant restore works in both layouts (pitfall measured and repaired)", b.get("pass", False) and b["pitfall_drop_on_shared"]["repaired"]))
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", choices=[p[0] for p in PARTS] + ["migration"])
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    sys.stdout = Tee(RESULTS / ("console_15.txt" if not a.only else f"console_15_{a.only}.txt"))
    print(f"root@lab15-runner:/app# lab15 {'--only ' + a.only if a.only else 'all'}{' --quick' if a.quick else ''}")
    p = RESULTS / "lab15_results.json"
    try:
        out = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        out = {}
    rs = L.L13.rs_summary()
    out["environment"] = {"at": datetime.now(timezone.utc).isoformat(), "replica_set": rs["set"],
                          "members": [f"{m['name']} {m['state']}" for m in rs["members"]], "db": L.DB, "quick": a.quick}
    print(f"TaskTracker on production set {rs['set']}: {', '.join(out['environment']['members'])}")
    L.ensure_user(lambda *x: None)
    names = {"applied": "applied", "crud": "lab_15_1", "rate": "rate", "rbac": "rbac"}
    for key, title, fn in PARTS:
        if a.only in (None, key):
            banner(title)
            out[names[key]] = guarded(key, fn)
    if a.only in (None, "migration"):
        banner("Lab 15.2 - data migration between tenant isolation strategies")
        out["lab_15_2"] = guarded("migration", L.run_migration, a.quick)
    banner("SUMMARY")
    ok = True
    for n, v in checks(out):
        print(f"  [{'PASS' if v else 'FAIL'}] {n}")
        ok &= bool(v)
    out["checks"] = [{"name": n, "ok": bool(v)} for n, v in checks(out)]
    out = json.loads(json.dumps(out, default=str))
    p.write_text(json.dumps(out, indent=2))
    try:
        L.L14.kdb().lab_results.replace_one({"_id": "lab_15"}, {"_id": "lab_15", **out}, upsert=True)
    except Exception as exc:                           # noqa: BLE001
        print(f"(could not store results in MongoDB: {str(exc)[:120]})")
    print("\nSaved results/lab15_results.json, results/console_15.txt and MongoDB cti_platform.lab_results (_id lab_15)")
    if a.report and not a.only:
        sys.stdout.flush()
        import importlib.util                          # explicit path: labs/lab14 has its own make_report.py on sys.path
        spec = importlib.util.spec_from_file_location("lab15_report", Path(__file__).resolve().parent / "make_report.py")
        mr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)
        print(f"Report written: {mr.build(out, RESULTS, os.getenv('DOCKER_URL', ''))}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()

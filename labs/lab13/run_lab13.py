"""Exercise 13 - Deploying MongoDB with containers and the cloud: run every Lab 13 task and save the evidence.

    python labs/lab13/run_lab13.py                  # everything (about 10-15 minutes, most of it Kubernetes)
    python labs/lab13/run_lab13.py --quick          # shorter: no crash tests, no classroom-variant experiment
    python labs/lab13/run_lab13.py --only 13.1      # one part:  13.1 | activity | billing | 13.2 | cloud
    python labs/lab13/run_lab13.py --report         # also build results/Exercise-13_Report.pdf

Console -> results/console_13.txt, numbers -> results/lab13_results.json and MongoDB cti_platform.lab_results
(_id "lab_13"), where the website's Lab 13 page reads them.
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

from backend.app import lab13 as L  # noqa: E402

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
        traceback.print_exc(limit=2, file=sys.stdout)
        out = {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "pass": False}
    if isinstance(out, dict):
        out.setdefault("seconds", round(time.time() - t0, 1))
    return out


def environment():
    st = {"pymongo": __import__("pymongo").version, "at": datetime.now(timezone.utc).isoformat(),
          "prod_hosts": L.PROD_HOSTS, "replica_set": L.PROD_RS, "k8s_mode": L.K8S_MODE}
    for c in L.PROD_CONTAINERS:
        i = L.inspect(c) or {}
        st.setdefault("containers", {})[c] = (i.get("State") or {}).get("Status", "missing")
    print(f"production members: {', '.join(f'{k} {v}' for k, v in st['containers'].items())} | Kubernetes: {L.K8S_MODE} | "
          f"PyMongo {st['pymongo']}")
    return st


def lab_13_1(quick):
    banner("Lab 13.1 - Deploy a replica set with Docker Compose")
    print("Step 1: docker-compose file - 3 x mongod --replSet rs0 --keyFile /etc/mongodb/keyfile, health checks,")
    print("        restart: unless-stopped, init, resource limits, log rotation (labs/lab13/compose/docker-compose.production.yml)")
    print("Step 2: keyfile - openssl rand -base64 756 > keyfile; chown 999:999 keyfile; chmod 400 keyfile (lab13-keyfile)")
    try:
        print("        keyfile-info.json: " + (L.PROD_DIR / "keyfile-info.json").read_text().replace("\n", " ")[:200])
    except OSError:
        pass
    out = {}
    print("\nStep 3: initialise the replica set and verify all three members are healthy")
    out["init"] = guarded("initiate", L.initiate)
    out["verify"] = guarded("verify", L.verify_deployment)
    if not quick:
        print("\n  A member with a DIFFERENT keyfile tries to join (internal authentication):")
        out["rogue"] = guarded("rogue member", L.rogue_member)
    print("\nStep 4: connect PyMongo to the replica set and verify failover works")
    print("  (a) planned failover - rs.stepDown()")
    out["failover_stepdown"] = guarded("stepdown", L.failover, "stepdown")
    if not quick:
        print("\n  (b) unplanned failover - the PRIMARY's mongod is killed (kill -9); Docker's restart policy brings it back")
        out["failover_crash"] = guarded("crash", L.failover, "crash")
    return out


def activity(quick):
    banner("Activity 13.1 - Prometheus exporter sidecar for prod-mongo-1")
    print("prod-mongo-1-exporter: percona mongodb_exporter, network_mode service:prod-mongo-1 (same network namespace),")
    print("user 'exporter' (clusterMonitor + read local); Prometheus scrapes prod-mongo-1:9216 every 5 s; 4 alert rules\n")
    return guarded("monitoring", L.monitoring, restart_test=not quick)


def billing(quick):
    banner("Lab 13 applied to Lab 10 - the classifier billing on the production replica set")
    print("Rs 100 per month after 3 free classifications (Lab 10 transactions) - copied to prod rs0, used as billing_app\n")
    out = {"copy": guarded("copy", L.billing_copy)}
    print("\nThe Lab 10 ACID checks, now on the production replica set:")
    out["acid"] = guarded("acid", L.billing_acid)
    print("\nCustomer journeys THROUGH a planned failover:")
    out["stepdown"] = guarded("billing stepdown", L.billing_failover, "stepdown")
    if not quick:
        print("\nCustomer journeys THROUGH a crash of the primary:")
        out["crash"] = guarded("billing crash", L.billing_failover, "crash")
    return out


def lab_13_2(quick):
    banner("Lab 13.2 - Kubernetes StatefulSet for MongoDB")
    if L.K8S_MODE == "off":
        print("LAB13_K8S=off - skipped (set LAB13_K8S=k3s to run the Kubernetes lab)")
        return {"skipped": True}
    print("headless Service mongo-headless-svc + StatefulSet mongo (3 replicas, volumeClaimTemplates, InitContainer keyfile-init,")
    print("readiness probe tcp 27017) on k3s running in the k3s container (labs/lab13/k8s/*.yaml)\n")
    out = {"deploy": guarded("k8s deploy", L.k8s_deploy)}
    if out["deploy"].get("error"):
        return out
    print("\nTest resiliency: kubectl delete pod mongo-2")
    out["resiliency"] = guarded("resiliency", L.k8s_resiliency, "mongo-2")
    if not quick:
        print("\nLab 13.2 applied to Lab 10: delete the PRIMARY pod while a client pod runs transactions")
        out["failover"] = guarded("k8s failover", L.k8s_failover_txn)
        print("\nThe solution text taken literally: the InitContainer GENERATES the keyfile in every pod")
        out["classroom_variant"] = guarded("classroom variant", L.k8s_classroom_variant)
    out["pods"] = L.pods_summary()
    return out


def cloud():
    banner("Cloud - MongoDB Atlas free tier with Terraform (reference main.tf reviewed and corrected)")
    return guarded("cloud", L.cloud)


def checks(out):
    res = []
    a = out.get("lab_13_1") or {}
    if a:
        v = a.get("verify") or {}
        for k, val in (v.get("checks") or {}).items():
            res.append((f"13.1: {k}", val))
        if "rogue" in a:
            res.append(("13.1: a member with a different keyfile cannot join (health 0)", a["rogue"].get("pass")))
        fs = a.get("failover_stepdown") or {}
        res.append((f"13.1: rs.stepDown() -> new primary, 0 acknowledged writes lost "
                    f"({fs.get('acknowledged')} written)", fs.get("pass")))
        if "failover_crash" in a:
            fc = a["failover_crash"]
            res.append((f"13.1: kill -9 of the primary -> new primary, Docker restarted it, it rejoined as SECONDARY, "
                        f"0 acknowledged writes lost", fc.get("pass")))
    m = out.get("activity")
    if m:
        res.append(("Activity: exporter sidecar shares prod-mongo-1's network namespace, Prometheus scrapes it, "
                    "the insert counter rises under load", m.get("pass")))
        if m.get("restart_test"):
            res.append(("Activity: sidecar measured after its main container restarted (and recovered)",
                        m["restart_test"].get("pass")))
    b = out.get("billing")
    if b:
        res.append(("13 on 10: billing copied to the production set and verified", (b.get("copy") or {}).get("verified")))
        res.append(("13 on 10: Lab 10 ACID checks pass on the production set", (b.get("acid") or {}).get("pass")))
        res.append(("13 on 10: every customer journey exactly right through rs.stepDown()", (b.get("stepdown") or {}).get("pass")))
        if "crash" in b:
            res.append(("13 on 10: every customer journey exactly right through a crash of the primary", b["crash"].get("pass")))
    k = out.get("lab_13_2") or {}
    if k and not k.get("skipped"):
        res.append(("13.2: StatefulSet ready, rs0 = 1 PRIMARY + 2 SECONDARY, same keyfile (400) in every pod, 3 PVCs, "
                    "headless service", (k.get("deploy") or {}).get("pass")))
        if "resiliency" in k:
            res.append(("13.2: deleted mongo-2 recreated, same PVC, data intact, SECONDARY again", k["resiliency"].get("pass")))
        if "failover" in k:
            res.append(("13.2 on 10: PRIMARY pod deleted during transactions - A + B unchanged, ledger = commits",
                        k["failover"].get("pass")))
        if "classroom_variant" in k:
            res.append(("13.2: per-pod keyfile generation (solution text) fails as predicted: different keys, "
                        "rs.initiate refused", k["classroom_variant"].get("pass")))
    c = out.get("cloud")
    if c:
        v = c.get("validate_corrected") or {}
        if v.get("ran"):
            res.append(("Cloud: corrected main.tf passes terraform validate", v.get("valid")))
    return [(n, bool(v)) for n, v in res]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", choices=["13.1", "activity", "billing", "13.2", "cloud"])
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    sys.stdout = Tee(RESULTS / ("console_13.txt" if not a.only else f"console_13_{a.only}.txt"))
    print(f"root@lab13-runner:/app# lab13 {'--only ' + a.only if a.only else 'all'}{' --quick' if a.quick else ''}")
    path = RESULTS / "lab13_results.json"
    out = {}
    if a.only and path.exists():
        try:
            out = json.loads(path.read_text())
        except Exception:                              # noqa: BLE001
            out = {}
    out.update({"environment": environment(), "quick": a.quick})
    if a.only in (None, "13.1"):
        out["lab_13_1"] = lab_13_1(a.quick)
    if a.only in (None, "activity"):
        out["activity"] = activity(a.quick)
    if a.only in (None, "billing"):
        out["billing"] = billing(a.quick)
    if a.only in (None, "13.2"):
        out["lab_13_2"] = lab_13_2(a.quick)
    if a.only in (None, "cloud"):
        out["cloud"] = cloud()

    banner("DOCKER COMPOSE vs KUBERNETES (measured where a number is shown)")
    out["compare"] = L.compare(out)
    for r in out["compare"]:
        print(f"  {r['aspect']:<36} compose: {r['compose']}\n  {'':<36} k8s    : {r['k8s']}")

    banner("SUMMARY")
    ok = True
    for name, v in checks(out):
        print(f"  [{'PASS' if v else 'FAIL'}] {name}")
        ok &= v
    out["checks"] = [{"name": n, "ok": v} for n, v in checks(out)]
    out = json.loads(json.dumps(out, default=str))
    path.write_text(json.dumps(out, indent=2))
    try:
        from backend.app.db import get_db
        get_db().lab_results.replace_one({"_id": "lab_13"}, {"_id": "lab_13", "ran_at": datetime.now(timezone.utc), **out},
                                         upsert=True)
    except Exception as exc:                           # noqa: BLE001
        print(f"(could not store results in MongoDB rs0: {str(exc)[:120]})")
    print("\nSaved results/lab13_results.json, results/console_13.txt and MongoDB cti_platform.lab_results (_id lab_13)")
    if a.report and not a.only:
        sys.stdout.flush()
        from make_report import build
        pdf = build(out, RESULTS, os.getenv("DOCKER_URL", ""))
        print(f"Report written: {pdf}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()

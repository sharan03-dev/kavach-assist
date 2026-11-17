"""Exercise 11 - run every Lab 11 task on the sharded cluster and save the evidence.

    python labs/lab11/run_lab11.py                 # 11.1 (24,000 orders) + 11.2 (10,000 docs) + resharding (~2 min)
    python labs/lab11/run_lab11.py --quick         # 11.1 with 6,000 orders, smaller resharding demo
    python labs/lab11/run_lab11.py --only 11.1     # one part:  11.1 | 11.2 | reshard
    python labs/lab11/run_lab11.py --report        # also build results/Exercise-11_Report.pdf

Needs the sharded cluster of docker-compose.yml (mongos + config server replica set + shardA + shardB).
Console output -> results/console_11.txt, numbers -> results/lab11_results.json and MongoDB
cti_platform.lab_results (_id "lab_11"), where the website's Lab 11 page reads them.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import lab11 as L  # noqa: E402

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


def table(headers, rows):
    w = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    print("  ".join(str(h).ljust(w[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * x for x in w))
    for r in rows:
        print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))


def environment():
    st = L.cluster_status()
    if not st.get("available"):
        print("!! The sharded cluster is not available: " + st.get("reason", "") +
              "\n   Start it with:  docker compose up -d lab11   (see README)")
        sys.exit(2)
    print(f"mongos {st['mongos_version']} at {st['router']} | config servers {st['config']['name']} | "
          f"shards {', '.join(s['name'] for s in st['shards'])} | balancer {st['balancer']['mode']}")
    for s in st["shards"]:
        print(f"   {s['name']}: " + ", ".join(f"{m['name']} {m['state']}" for m in s["members"]))
    return {"mongodb": st["mongos_version"], "router": st["router"], "config": st["config"], "shards": st["shards"],
            "pymongo": __import__("pymongo").version, "at": datetime.now(timezone.utc).isoformat()}


def lab_11_1(quick):
    banner("Lab 11.1 - Evaluate shard key choices (measured on the sharded cluster)")
    print("E-commerce order management system. Candidates: order_id, customer_id, order_date, hashed customer_id,")
    print("plus two extra rows from the course material: category (low cardinality) and a compound hashed key.")
    print(f"Workload: " + "; ".join(f"{q['label']} {q['weight']:.0%}" for q in L.WORKLOAD))
    r = L.evaluate_shard_keys(6_000 if quick else 24_000)
    print("\nQueries used for targeting (same values on every collection):")
    for k, v in r["queries"].items():
        print(f"   {k:<14} {v}")
    print("\nDECISION MATRIX  (scores 1-5, weights: " + ", ".join(f"{k} {v:.0%}" for k, v in r["criteria"].items()) + ")")
    rows = []
    for c in r["candidates"]:
        sc = c["scores"]
        rows.append([c["label"] + ("" if c["required"] else " *"), f"{sc['cardinality']} ({c['cardinality']['distinct']:,})",
                     f"{sc['distribution']} ({c['distribution']['busiest_share']:.0%} max)",
                     f"{sc['hotspot']} {c['hotspot']['level']}", f"{sc['targeting']} ({c['targeting']['targeted_weight']:.0%})",
                     c["total"], c["verdict"]])
    table(["shard key", "cardinality", "distribution", "hot-spot", "targeting", "score", "verdict"], rows)
    print("   * extra row from the course material (not one of the four required candidates)")
    print("\nMeasured facts behind each score:")
    for c in r["candidates"]:
        m = L.matrix_cells(c)
        print(f"   {c['label']}")
        for k in ("cardinality", "distribution", "hotspot", "targeting"):
            print(f"      {k:<13} {m[k]}")
    print("\nQuery targeting in detail (shards hit / 2):")
    table(["shard key"] + [q["id"] for q in L.WORKLOAD],
          [[c["label"]] + [f"{c['targeting']['queries'][q['id']]['n_shards']} ({'targeted' if c['targeting']['queries'][q['id']]['targeted'] else 'broadcast'})"
                           for q in L.WORKLOAD] for c in r["candidates"]])
    print("\nVerdicts:")
    for c in r["candidates"]:
        print(f"   {c['label']:<40} {c['verdict']}: {c['reason']}")
    best = next(c for c in r["candidates"] if c["id"] == r["recommended"])
    print(f"\nRECOMMENDATION: {best['label']}  ({best['key']})  score {best['total']}")
    print(f"   {best['reason']}.")
    if r["best_overall"] != r["recommended"]:
        x = next(c for c in r["candidates"] if c["id"] == r["best_overall"])
        print(f"   Refinement: {x['label']} scores {x['total']} - {x['reason']}.")
    return r


def lab_11_2():
    banner("Lab 11.2 - Shard key hotspot detection")
    print("db.hotspot_data shard key {ts: 1} (monotonically increasing ISODate) vs db.even_data shard key")
    print("{user_id: 'hashed'} (a new ObjectId per document). 10,000 documents each.")
    r = L.hotspot_experiment(log=print)
    s = r["summary"]
    a, b = r["shards"]
    print(f"\nResult: hotspot_data {s['hotspot_by_shard'][a]:,} on {a} / {s['hotspot_by_shard'][b]:,} on {b};  "
          f"even_data {s['even_by_shard'][a]:,} / {s['even_by_shard'][b]:,}")
    print(f"Inserts executed by each shard primary in phase 2 (serverStatus opcounters.insert): "
          f"hotspot_data {s['hotspot_phase2_inserts']}, even_data {s['even_phase2_inserts']}")
    return r


def lab_reshard(quick):
    banner("Activity 11.1 - Resharding (MongoDB 5.0+): live demonstration")
    print("orders sharded on the bad key {order_date: 1} -> reshardCollection to {customer_id: 'hashed'} while a")
    print("writer keeps inserting one order every 20 ms. Phases are read from $currentOp and config.reshardingOperations.")
    r = L.reshard_demo(6_000 if quick else 12_000, log=print)
    print("\nSteps of a resharding operation:")
    for i, (t, d) in enumerate(L.RESHARD_STEPS, 1):
        print(f"   {i}. {t}: {d}")
    print("\nLimitations:")
    for x in L.RESHARD_LIMITS:
        print(f"   - {x}")
    return r


def lab_billing(quick):
    """Mentor's update: Lab 11 applied to Lab 10 - the classifier billing (Lab 10 on Lab 7) moves to the shards."""
    from backend.app import billing
    banner("Lab 11 applied to Lab 10 - the classifier billing on the sharded cluster")
    print("Lab 10 put the classifier behind a paywall (3 free uses, then Rs 100/month) using transactions on rs0.")
    print("Lab 11 scales it out: cti_billing moves to the shards and keeps running as distributed transactions.")
    print("Step 1  move the data once (verified copy, then the live marker switches to 'sharded'):")
    mig = billing.scale_out(log=print)
    n = 120 if quick else 300
    print(f"Step 2  load test: {n} users at once (sign in, free trials, top-up, subscribe, keep classifying):")
    lt = billing.load_test(n, 8, log=print)
    print("Step 3  the Lab 10 ACID experiments again, now across shards:")
    acid = billing.acid_checks(L.client(), log=print)
    return {"migration": mig, "load": lt, "acid": acid,
            "keys": {"accounts": "{_id: 'hashed'}", "usage": "{user_id: 'hashed', at: 1}", "payments": "{user_id: 'hashed'}",
                     "revenue": "{_id: 'hashed'} (8 bucket documents)"}}


def checks(out):
    res = []
    if "lab_11_1" in out:
        r = out["lab_11_1"]
        by = {c["id"]: c for c in r["candidates"]}
        res += [("11.1 order_date flagged as a write hot-spot (AVOID)", by["order_date"]["hotspot"]["level"] == "High"),
                ("11.1 hashed customer_id recommended among the four candidates", r["recommended"] == "hashed_customer_id"),
                ("11.1 hashed customer_id spreads new orders evenly (<= 56 % on one shard)",
                 by["hashed_customer_id"]["distribution"]["busiest_share"] <= 0.56),
                ("11.1 customer queries go to ONE shard with hashed customer_id",
                 by["hashed_customer_id"]["targeting"]["queries"]["q_customer"]["targeted"]),
                ("11.1 order_id makes customer queries scatter-gather",
                 not by["order_id"]["targeting"]["queries"]["q_customer"]["targeted"])]
    if "lab_11_2" in out:
        r = out["lab_11_2"]
        s = r["summary"]
        tot_h = sum(s["hotspot_by_shard"].values()) or 1
        tot_e = sum(s["even_by_shard"].values()) or 1
        e = r["explain"]
        res += [("11.2 hotspot_data: >= 90 % of documents on one shard", max(s["hotspot_by_shard"].values()) / tot_h >= 0.9),
                ("11.2 even_data: every shard holds 40-60 %", all(0.4 <= v / tot_e <= 0.6 for v in s["even_by_shard"].values())),
                ("11.2 range on ts (hotspot_data) targets a single shard", e["H1"]["n_shards"] == 1),
                ("11.2 range on hashed user_id is broadcast to all shards", e["E2"]["n_shards"] == e["E2"]["total_shards"])]
    if "resharding" in out:
        r = out["resharding"]
        b, a = r["before"]["by_shard"], r["after"]["by_shard"]
        res += [("Activity: reshardCollection committed, key is now {customer_id: 'hashed'}", r["ok"] and "hashed" in r["after"]["key"]),
                ("Activity: data more even after resharding", max(a.values()) / sum(a.values()) < max(b.values()) / sum(b.values())),
                ("Activity: customer query targeted after resharding", r["after"]["explain_customer"]["targeted"]),
                ("Activity: application writes continued, 0 errors", r["writes"]["errors"] == 0 and r["writes"]["count"] > 0)]
    if "billing" in out:
        b = out["billing"]
        lt = b["load"]
        res += [("Lab 11 on 10: billing data moved to the shards (verified copy)", b["migration"].get("verified") or b["migration"].get("already")),
                (f"Lab 11 on 10: {lt['users']} concurrent users, 0 errors, money conserved across shards",
                 lt["errors"] == 0 and lt["invariants"]["money_conserved"] and lt["invariants"]["revenue_matches"]),
                ("Lab 11 on 10: a user's account, usage and payments are each read from ONE shard",
                 all(lt["targeting"][k]["n_shards"] == 1 for k in ("account by _id", "usage of one user", "payments of one user"))),
                ("Lab 11 on 10: the Lab 10 ACID checks pass across shards", b["acid"]["pass"])]
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", choices=["11.1", "11.2", "reshard", "billing"])
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    sys.stdout = Tee(RESULTS / ("console_11.txt" if not a.only else f"console_11_{a.only}.txt"))
    print(f"root@lab11-runner:/app# lab11 {'--only ' + a.only if a.only else 'all'}{' --quick' if a.quick else ''}")
    env = environment()

    path = RESULTS / "lab11_results.json"
    out = {}
    if a.only and path.exists():
        try:
            out = json.loads(path.read_text())
        except Exception:                                 # noqa: BLE001
            out = {}
    out.update({"environment": env, "quick": a.quick})
    if a.only in (None, "11.1"):
        out["lab_11_1"] = lab_11_1(a.quick)
    if a.only in (None, "11.2"):
        out["lab_11_2"] = lab_11_2()
    if a.only in (None, "reshard"):
        out["resharding"] = lab_reshard(a.quick)
    if a.only in (None, "billing"):
        out["billing"] = lab_billing(a.quick)

    banner("SUMMARY")
    ok = True
    for name, v in checks(out):
        print(f"  [{'PASS' if v else 'FAIL'}] {name}")
        ok &= bool(v)
    out["checks"] = [{"name": n, "ok": bool(v)} for n, v in checks(out)]
    out = json.loads(json.dumps(out, default=str))
    path.write_text(json.dumps(out, indent=2))
    try:
        from backend.app.db import get_db
        get_db().lab_results.replace_one({"_id": "lab_11"}, {"_id": "lab_11", "ran_at": datetime.now(timezone.utc), **out},
                                         upsert=True)
    except Exception as exc:                              # noqa: BLE001
        print(f"(could not store results in MongoDB rs0: {str(exc)[:120]})")
    print("\nSaved results/lab11_results.json, results/console_11.txt and MongoDB cti_platform.lab_results (_id lab_11)")

    if a.report and not a.only:
        sys.stdout.flush()
        from make_report import build
        pdf = build(out, RESULTS, os.getenv("DOCKER_URL", ""))
        print(f"Report written: {pdf}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()

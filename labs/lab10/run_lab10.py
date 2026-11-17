"""Exercise 10 - run every Lab 10 task and save the evidence.

    python labs/lab10/run_lab10.py              # Lab 10.1 + 10.2 + throughput (about 1-2 minutes)
    python labs/lab10/run_lab10.py --quick      # smaller throughput run (used when Lab 10 is a dependency)
    python labs/lab10/run_lab10.py --only 10.1  # one part:  10.1 | 10.2 | bench
    python labs/lab10/run_lab10.py --report     # also build results/Exercise-10_Report.pdf

Needs a replica set (multi-document transactions are not available on a standalone server).
Console output is saved to results/console_10.txt, numbers to results/lab10_results.json and to
MongoDB (cti_platform.lab_results, _id "lab_10") where the website's Lab 10 page reads them.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from pymongo import MongoClient  # noqa: E402

from backend.app import config, lab10 as L  # noqa: E402

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
    line = "  ".join(str(h).ljust(w[i]) for i, h in enumerate(headers))
    print(line + "\n" + "  ".join("-" * x for x in w))
    for r in rows:
        print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))


def balances(client):
    return {a["account_id"]: a["balance"] for a in L.bank_state(client)["accounts"]}


def lab_10_1(client, out):
    banner("Lab 10.1 - Bank transfer as ONE multi-document transaction")
    print("Transaction = debit ACC-001 + credit ACC-002 + history record. All three happen, or none.")
    st = L.seed_bank(client)
    print(f"Start: {balances(client)}   total money = {st['total']}")
    res = {}

    print("\n[1] Transfer 100 from ACC-001 to ACC-002")
    r = L.transfer(client, "ACC-001", "ACC-002", 100)
    print(f"    -> {r['outcome']}  (attempts={r['attempts']}, {r['ms']} ms)   balances: {balances(client)}")
    res["success"] = {**r, "balances": balances(client)}

    print("\n[2] Insufficient funds: transfer 5000 (ACC-001 only has 900)  -> must ROLL BACK")
    before = balances(client)
    r = L.transfer(client, "ACC-001", "ACC-002", 5000)
    after = balances(client)
    hist = len(L.bank_state(client)["history"])
    print(f"    -> {r['outcome']}: {r.get('reason')}")
    print(f"    balances before {before}  after {after}  -> unchanged: {before == after}; history rows: {hist} (still 1)")
    res["insufficient"] = {**r, "before": before, "after": after, "unchanged": before == after}

    print("\n[3] Crash after the debit but before the credit  -> must ROLL BACK the debit")
    before = balances(client)
    r = L.transfer(client, "ACC-001", "ACC-002", 100, crash_after_debit=True)
    after = balances(client)
    print(f"    -> {r['outcome']}: {r.get('reason')}")
    print(f"    balances before {before}  after {after}  -> unchanged: {before == after}")
    res["crash"] = {**r, "before": before, "after": after, "unchanged": before == after}

    print("\n[4] The classroom sample WITHOUT a matched_count check (why we check it)")
    L.seed_bank(client)
    start = L.bank_state(client)["total"]
    info = L.unsafe_transfer_like_sample(client, "ACC-001", "ACC-002", 5000)
    end = L.bank_state(client)["total"]
    print(f"    debit matched {info['debit_matched']} document(s) but the credit still ran: "
          f"total money {start} -> {end}  ({end - start:+d} created from nothing)")
    print("    update_one with a non-matching filter does NOT raise, so nothing triggers the rollback.")
    res["sample_bug"] = {"total_before": start, "total_after": end, "created": end - start,
                         "debit_matched": info["debit_matched"]}

    print("\n[5] Activity 10.1 - retry with exponential back-off on write conflicts")
    L.seed_bank(client)
    d = L.conflict_demo(client, hold=0.6)
    for k, label in (("A", "A (holds its transaction open 0.6 s)"), ("B", "B (starts 0.15 s later)")):
        r = d[k]
        print(f"    transfer {label}: {r['outcome']}  attempts={r['attempts']}  conflicts={r['conflicts']}  "
              f"back-off delays={r['delays']}  {r['ms']} ms")
    print(f"    ACC-001/ACC-002 after both: { {a['account_id']: a['balance'] for a in d['after']} }   "
          f"money conserved: {d['money_conserved']}")
    print("    B was aborted by the server with a write conflict (TransientTransactionError), waited, retried, won.")
    res["conflict"] = d
    L.seed_bank(client)
    return res


def lab_10_2(client, out):
    banner("Lab 10.2 - Inventory reservation under concurrency")
    print("Product P-100 has stock 5. Eight threads each try to reserve 1 unit at the same instant.")
    res = {}
    for retry in (False, True):
        print(f"\n[{'2' if retry else '1'}] 8 concurrent reservations, retry logic {'ON (max 5 retries, 50 ms back-off)' if retry else 'OFF'}")
        r = L.concurrency_test(client, threads=8, stock=5, retry=retry)
        s = r["summary"]
        rows = [[x["customer"], x["status"], x.get("attempts", 1), x.get("conflicts", 0), x["ms"]] for x in r["results"]]
        table(["customer", "result", "attempts", "conflicts", "ms"], rows)
        print(f"    reserved={s['reserved']}  out_of_stock={s['out_of_stock']}  write_conflict(failed)={s['write_conflict']}  "
              f"retries used={s['retries']}  in {r['seconds']} s")
        print(f"    final stock={r['final_stock']}  orders={r['orders']}  oversold={r['oversold']}  consistent={r['consistent']}")
        res["with_retry" if retry else "no_retry"] = {k: v for k, v in r.items() if k != "results"} | {"results": r["results"]}
    nr, wr = res["no_retry"]["summary"], res["with_retry"]["summary"]
    print(f"\n    Without retry {nr['write_conflict']} request(s) died with a write conflict although stock was left; "
          f"with retry the first {wr['reserved']} succeed and the other {wr['out_of_stock']} correctly fail: stock reached zero.")

    print("\n[3] Atomicity: crash AFTER the stock decrement, BEFORE the order insert")
    a = L.atomicity_demo(client)
    for k, label in (("transactional", "transaction       "), ("non_transactional", "findAndModify     ")):
        print(f"    {label}: stock={a[k]['stock']}  orders={a[k]['orders']}  -> "
              f"{'consistent (rolled back)' if a[k]['consistent'] else 'INCONSISTENT: stock lost, no order'}")
    res["atomicity"] = a
    return res


def lab_bench(client, quick):
    banner("Lab 10.2 - Throughput vs. concurrency, transaction vs. findAndModify")
    ops = 60 if quick else 120
    print(f"{ops} reservations of 1 unit on ONE product per run (plenty of stock). Threads: 1, 4, 8, 16.")
    b = L.throughput(client, ops=ops)
    print()
    table(["threads", "txn ok/s", "txn retries", "txn failed", "findAndModify ok/s", "txn / fAM"],
          [[r["threads"], r["transactional"]["per_second"], r["transactional"]["retries"], r["transactional"]["failed"],
            r["non_transactional"]["per_second"],
            f"{r['transactional']['per_second'] / max(r['non_transactional']['per_second'], 0.1):.2f}"] for r in b["rows"]])
    print()
    for line in b["explanation"]:
        print("* " + line)
    return b


def lab_billing(client):
    """Mentor's update: Lab 10 applied to Lab 7 - the classifier costs Rs 100/month after 3 free uses."""
    from backend.app import billing
    banner("Lab 10 applied to Lab 7 - classifier paywall: 3 free uses, then Rs 100 per month")
    print("Every use, top-up and subscription is ONE multi-document transaction (accounts, usage, payments, revenue)")
    print("in database cti_billing on this replica set. Each ACID property is shown with a real experiment:")
    billing.ensure_schema(client, log=print)
    r = billing.acid_checks(client, log=print)
    print(f"Website: Classifier page (sign in, 3 free classifications, then the paywall) and the Lab 10 page.")
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", choices=["10.1", "10.2", "bench", "billing"])
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    sys.stdout = Tee(RESULTS / "console_10.txt" if not a.only else RESULTS / f"console_10_{a.only}.txt")

    print(f"root@lab10-runner:/app# lab10 {'--only ' + a.only if a.only else 'all'}{' --quick' if a.quick else ''}")
    client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=8000, appname="lab10")
    hello = client.admin.command("hello")
    if not hello.get("setName"):
        print("!! This MongoDB is a standalone server. Multi-document transactions need a replica set.\n"
              "   Start the Docker stack (docker compose up -d lab10) - see README.")
        sys.exit(2)
    ver = client.server_info()["version"]
    env = {"mongodb": ver, "replica_set": hello["setName"], "primary": hello.get("primary"),
           "hosts": hello.get("hosts"), "pymongo": __import__("pymongo").version, "at": datetime.now(timezone.utc).isoformat()}
    print(f"MongoDB {ver} | replica set {env['replica_set']} | primary {env['primary']} | PyMongo {env['pymongo']}")

    out = {"environment": env, "quick": a.quick}
    if a.only in (None, "10.1"):
        out["lab_10_1"] = lab_10_1(client, out)
    if a.only in (None, "10.2"):
        out["lab_10_2"] = lab_10_2(client, out)
    if a.only in (None, "bench"):
        out["throughput"] = lab_bench(client, a.quick)
    if a.only in (None, "billing"):
        out["billing"] = lab_billing(client)

    banner("SUMMARY")
    ok = True
    if "lab_10_1" in out:
        r = out["lab_10_1"]
        checks = [("transfer committed", r["success"]["ok"]), ("insufficient funds rolled back", r["insufficient"]["unchanged"]),
                  ("crash after debit rolled back", r["crash"]["unchanged"]),
                  ("retry demo: both transfers committed, money conserved",
                   r["conflict"]["A"]["ok"] and r["conflict"]["B"]["ok"] and r["conflict"]["money_conserved"])]
        for name, v in checks:
            print(f"  [{'PASS' if v else 'FAIL'}] Lab 10.1 {name}"); ok &= bool(v)
    if "lab_10_2" in out:
        w = out["lab_10_2"]["with_retry"]
        for name, v in [("5 reserved / 3 out of stock with retry", w["summary"]["reserved"] == 5 and w["summary"]["out_of_stock"] == 3),
                        ("never oversold, stock == 0, 5 orders", not w["oversold"] and w["final_stock"] == 0 and w["orders"] == 5),
                        ("transaction rolled back on crash", out["lab_10_2"]["atomicity"]["transactional"]["consistent"])]:
            print(f"  [{'PASS' if v else 'FAIL'}] Lab 10.2 {name}"); ok &= bool(v)
    if "throughput" in out:
        print("  [DONE] throughput table recorded")
    if "billing" in out:
        b = out["billing"]
        for name, v in [("classifier: 10 simultaneous uses -> exactly 3 free trials", b["free_trial_race"]["pass"]),
                        ("classifier: crash after wallet debit rolled back", b["crash_after_debit"]["pass"]),
                        ("classifier: double click on Pay charged once", b["double_click"]["pass"]),
                        ("classifier: money conserved, revenue matches", b["invariants"]["money_conserved"] and b["invariants"]["revenue_matches"]),
                        ("classifier: commits are majority + journaled", b["durability"]["pass"])]:
            print(f"  [{'PASS' if v else 'FAIL'}] Lab 10 on Lab 7 {name}"); ok &= bool(v)

    (RESULTS / "lab10_results.json").write_text(json.dumps(out, indent=2, default=str))
    try:
        from backend.app.db import get_db
        get_db().lab_results.replace_one({"_id": "lab_10"}, {"_id": "lab_10", "ran_at": datetime.now(timezone.utc),
                                                              **json.loads(json.dumps(out, default=str))}, upsert=True)
    except Exception as exc:                              # noqa: BLE001
        print(f"(could not store results in MongoDB: {exc})")
    print("\nSaved results/lab10_results.json, results/console_10.txt and MongoDB cti_platform.lab_results (_id lab_10)")

    if a.report and not a.only:
        sys.stdout.flush()
        from make_report import build
        pdf = build(out, RESULTS, os.getenv("DOCKER_URL", ""))
        print(f"Report written: {pdf}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()

"""
Lab 9.1 - Measure Write Concern Latency
Objective: measure the impact of different write concerns on write latency.

  1. Insert 1,000 documents with each write concern - w:1, w:majority, w:all -
     and measure the total time for each.
  2. Calculate the average latency per write for each concern level.
  3. Print a comparison table and an explanation of the differences.

About "w:all"
  MongoDB has no built-in write concern called "all". The valid values are a
  number, "majority" or the name of a custom mode defined in
  settings.getLastErrorModes. The script first sends w:"all" once to show the
  server's error, then uses w:<number of data-bearing members> (w:3 here),
  which is exactly "acknowledged by every member".

Run (inside the lab9 container):  lab9 9.1
Run (on the host):                python Activity_9.1_Write_Concern_Latency/write_concern_latency.py
Options: --docs 1000 --rounds 5 --warmup 50
  --rounds N repeats the whole comparison N times in rotated order
  (w:1 first, then w:majority first, ...) so that background noise or a
  warming cache cannot favour one concern. Times are averaged per 1,000 docs.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.bench import (explain_91, probe_custom_mode, run_write_benchmark,  # noqa: E402
                          summarize, wc_text)
from common.rs import (banner, data_bearing_members, environment_snapshot,  # noqa: E402
                       format_table, results_file, rs_client, rs_config)

PREDICTION = ("Prediction (from theory, before running): w:1 < w:majority < w:all, "
              "with w:majority about 2-5x slower than w:1 (lab handout).")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--docs", type=int, default=1000)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=50)
    ap.add_argument("--wtimeout", type=int, default=10000, help="ms, for w>1")
    args = ap.parse_args()

    banner("Lab 9.1 - Write concern latency (w:1 vs w:majority vs w:all)")
    client = rs_client()
    client.admin.command("ping")
    env = environment_snapshot(client)
    hosts = data_bearing_members(rs_config(client))
    client.close()

    n = env["data_bearing_members"]
    print(f"MongoDB {env['mongodb_version']}  |  PyMongo {env['pymongo_version']}  |  "
          f"replica set {env['replica_set']}")
    print(f"Primary: {env['primary']}   data-bearing members: {n}   "
          f"writeMajorityCount: {env['write_majority_count'] or n // 2 + 1}")
    print(f"writeConcernMajorityJournalDefault: "
          f"{env['write_concern_majority_journal_default']}")
    print(PREDICTION)

    print('\nChecking w:"all" ...')
    err = probe_custom_mode("all")
    if err is None:
        w_all, note = "all", 'custom write-concern mode "all" exists - using w:"all"'
    else:
        w_all = n
        note = (f'server rejected w:"all" -> {err}\n'
                f"Using w:{n} instead (= acknowledgment from every data-bearing member).")
    print(note)

    concerns = [("w:1", 1), ("w:majority", "majority"), ("w:all", w_all)]
    runs = {label: [] for label, _ in concerns}
    print(f"\nInserting {args.docs:,} documents per concern, {args.rounds} round(s), "
          f"{args.warmup} untimed warm-up inserts each ...")
    for r in range(args.rounds):
        order = concerns[r % 3:] + concerns[:r % 3]
        for label, w in order:
            res = run_write_benchmark(label, w, None, args.docs, args.warmup,
                                      args.wtimeout, hosts)
            runs[label].append(res)
            print(f"  round {r + 1}: {label:<11} sent as {wc_text(res['sent_as']):<28} "
                  f"total {res['total_s']:7.3f} s   avg {res['total_s'] * 1000 / args.docs:6.3f} ms")

    rows = {label: summarize(label, runs[label]) for label, _ in concerns}
    base = rows["w:1"]["avg_ms"]

    banner(f"RESULTS - {args.docs:,} inserts per concern (mean of {args.rounds} round(s))")
    headers = ["Write concern", "Sent to server", "Total (s)", "Avg (ms)", "P50", "P95",
               "P99", "Writes/s", "vs w:1"]
    table = [[label, wc_text(r["sent_as"]), f"{r['total_s']:.3f}", f"{r['avg_ms']:.3f}",
              f"{r['p50_ms']:.3f}", f"{r['p95_ms']:.3f}", f"{r['p99_ms']:.3f}",
              f"{r['throughput_ops']:.0f}", f"{r['avg_ms'] / base:.2f}x"]
             for label, r in rows.items()]
    print(format_table(headers, table, right=range(2, 9)))
    print(f"\nTotal time for {args.docs:,} documents in each round (s):")
    for label, r in rows.items():
        print(f"  {label:<11} " + "  ".join(f"{t:.3f}" for t in r["total_s_each"])
              + f"   (per-round avg {min(r['round_avg_ms']):.3f}-{max(r['round_avg_ms']):.3f} ms)")

    banner("WHY THE NUMBERS DIFFER", "-")
    for line in explain_91(rows, env):
        print(f"* {line}\n")

    order = sorted(rows, key=lambda k: rows[k]["avg_ms"])
    ratio = rows["w:majority"]["avg_ms"] / base
    print(f"Prediction check: order {' < '.join(order)} "
          f"({'as predicted' if order == ['w:1', 'w:majority', 'w:all'] else 'differs from the prediction'}); "
          f"w:majority/w:1 = {ratio:.2f}x "
          f"({'inside' if 2 <= ratio <= 5 else 'outside'} the predicted 2-5x).\n")

    out = {"environment": env, "w_all_probe": err, "w_all_sent_as": w_all,
           "docs": args.docs, "rounds": args.rounds, "warmup": args.warmup,
           "prediction": PREDICTION, "results": rows}
    results_file("lab9_1_results.json").write_text(json.dumps(out, indent=2, default=str))
    with results_file("lab9_1_results.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(headers)
        w.writerows(table)
    print("Saved results/lab9_1_results.json and results/lab9_1_results.csv")


if __name__ == "__main__":
    main()

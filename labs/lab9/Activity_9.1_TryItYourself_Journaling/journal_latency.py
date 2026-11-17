"""
Lab 9.1 - Try it yourself: repeat the experiment with j:true and j:false for
each write concern. Does journaling add significant latency?

Six configurations x 1,000 inserts:
    w:1        j:false | j:true
    w:majority j:false | j:true
    w:all(w:3) j:false | j:true

Besides latency, the script reads every member's WiredTiger
"log sync operations" counter before and after each run. That counter goes
up once per journal fsync, so it shows which configurations really waited
for the journal (j:true forces an immediate sync; j:false relies on the
background group flush).

Documented rule tested here (MongoDB manual, "Write Concern"):
  with writeConcernMajorityJournalDefault: true (the default), w:"majority"
  writes are acknowledged only after the journal is flushed EVEN IF j:false
  is requested -> expect almost no j:true/j:false difference for w:majority.

The chapter-09 README ("Check your work") expects the order
  w:1,j:false < w:1,j:true < w:majority,j:false < w:majority,j:true
and the script checks the measured order against it.

Run (inside the lab9 container):  lab9 9.1-journal
Run (on the host):                python Activity_9.1_TryItYourself_Journaling/journal_latency.py
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.bench import (explain_journal, journal_comparison, reference_order_check,  # noqa: E402
                          run_write_benchmark, summarize, syncs_per_write, wc_text)
from common.rs import (banner, data_bearing_members, environment_snapshot,  # noqa: E402
                       format_table, results_file, rs_client, rs_config, short)

PREDICTION = ("Prediction (from theory, before running): j:true clearly slows w:1 (a journal "
              "flush per write) but changes w:majority very little, because w:majority is "
              "already journaled when writeConcernMajorityJournalDefault is true.")


def main():
    ap = argparse.ArgumentParser(description="Lab 9.1 try-it-yourself: j:true vs j:false")
    ap.add_argument("--docs", type=int, default=1000)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=50)
    ap.add_argument("--wtimeout", type=int, default=10000)
    args = ap.parse_args()

    banner("Lab 9.1 (Try it yourself) - does journaling add latency?")
    client = rs_client()
    client.admin.command("ping")
    env = environment_snapshot(client)
    hosts = data_bearing_members(rs_config(client))
    client.close()
    n = env["data_bearing_members"]
    print(f"MongoDB {env['mongodb_version']}   Primary: {env['primary']}   "
          f"writeConcernMajorityJournalDefault: {env['write_concern_majority_journal_default']}")
    print(PREDICTION)

    configs = []
    for w_label, w in (("w:1", 1), ("w:majority", "majority"), ("w:all", n)):
        for j in (False, True):
            configs.append((f"{w_label}, j:{str(j).lower()}", w, j))

    runs = {label: [] for label, _, _ in configs}
    print(f"\n{len(configs)} configurations x {args.docs:,} inserts x {args.rounds} round(s)")
    for r in range(args.rounds):
        k = r % len(configs)
        for label, w, j in configs[k:] + configs[:k]:
            res = run_write_benchmark(label, w, j, args.docs, args.warmup, args.wtimeout, hosts)
            runs[label].append(res)
            print(f"  round {r + 1}: {label:<20} total {res['total_s']:7.3f} s   "
                  f"avg {res['total_s'] * 1000 / args.docs:6.3f} ms")

    rows = {label: summarize(label, runs[label]) for label, _, _ in configs}

    banner("RESULTS (mean of rounds)")
    headers = ["Configuration", "Total (s)", "Avg (ms)", "P50", "P99", "spread"] + \
              [f"syncs/wr {short(h)}" for h in hosts]
    table = [[label, f"{r['total_s']:.3f}", f"{r['avg_ms']:.3f}", f"{r['p50_ms']:.3f}",
              f"{r['p99_ms']:.3f}", f"{r['round_spread_ms']:.3f}"] +
             [("n/a" if syncs_per_write(r, h) is None else f"{syncs_per_write(r, h):.2f}")
              for h in hosts] for label, r in rows.items()]
    print(format_table(headers, table, right=range(1, len(headers))))
    print("spread = max-min of the per-round averages (ms); syncs/wr = journal fsyncs per write")

    comp = journal_comparison(rows)
    print()
    print(format_table(
        ["Write concern", "j:false (ms)", "j:true (ms)", "Difference", "Change", "Noise", "Verdict"],
        [[c["w"], f"{c['j_false_ms']:.3f}", f"{c['j_true_ms']:.3f}", f"{c['delta_ms']:+.3f} ms",
          f"{c['delta_pct']:+.1f}%", f"{c['noise_ms']:.3f}", c["verdict"]] for c in comp],
        right=range(1, 6)))

    banner("ANALYSIS", "-")
    for line in explain_journal(rows, env):
        print(f"* {line}\n")

    chk = reference_order_check(rows)
    out = {"environment": env, "hosts": hosts, "docs": args.docs, "rounds": args.rounds,
           "prediction": PREDICTION, "results": rows, "comparison": comp,
           "reference_order": chk, "sent_as": {k: wc_text(v["sent_as"]) for k, v in rows.items()}}
    results_file("lab9_1_journal_results.json").write_text(json.dumps(out, indent=2, default=str))
    with results_file("lab9_1_journal_results.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(headers)
        w.writerows(table)
    print("Saved results/lab9_1_journal_results.json and .csv")


if __name__ == "__main__":
    main()

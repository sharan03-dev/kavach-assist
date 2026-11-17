"""Exercise 18 - Comparative capstone: false positives from the threat intelligence service in MongoDB, Cassandra, Neo4j.

    python labs/lab18/run_lab18.py              # 100,000 threats, 4 benchmarks x 10 runs, FP detection, communities (5-10 min)
    python labs/lab18/run_lab18.py --quick      # 10,000 threats
    python labs/lab18/run_lab18.py --report     # also results/Exercise-18_Report.pdf (the Lab 18.2 comparative report)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import runner_kit as K  # noqa: E402
from backend.app import lab18 as L  # noqa: E402

PARTS = [("lab_18_1", "Lab 18.1 - the comparative system: load, 4 benchmarks x 10 runs, FP detection, communities, scale", lambda q: L.run_181(q))]


def checks(o):
    a = o.get("lab_18_1") or {}
    c = []
    if a.get("benchmark"):
        s = a["benchmark"]["same_answer"]
        c.append((f"{a['load']['n']:,} threats loaded into MongoDB, Cassandra and Neo4j", a["load"]["n"] >= 10000))
        c.append(("Q1-Q3 give the same answer in all three databases", bool(s.get("q1")) and bool(s.get("q2")) and bool(s.get("q3"))))
        c.append(("Q4 insert throughput measured (10 runs x 1,000 threats)", all(a["benchmark"]["results"]["q4"][k]["throughput_per_s"] > 0 for k in ("mongodb", "cassandra", "neo4j"))))
        f = a["fp"]
        c.append((f"FP detector: identical verdicts in all three, precision {f['accuracy']['precision']}, recall {f['accuracy']['recall']}", f["identical"]))
        m = a["communities"]
        c.append((f"Activity: Louvain communities recovered {m['campaigns_found']}/{m['campaigns_planted']} coordinated campaigns", m.get("pass", False)))
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", choices=[p[0] for p in PARTS])
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    from backend.app import polyglot as PG
    PG.wait_for(("mongodb", "cassandra", "neo4j"), 300)
    sys.exit(0 if K.run(18, PARTS, a, checks, "Exercise 18 - Comparative capstone (MongoDB vs Cassandra vs Neo4j)") else 1)


if __name__ == "__main__":
    main()

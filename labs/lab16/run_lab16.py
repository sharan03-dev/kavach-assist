"""Exercise 16 - Wide-column and graph databases: Cassandra and Neo4j. Runs every task and saves the evidence.

    python labs/lab16/run_lab16.py              # 16.1 + activity + 16.2 + applied (about 2-4 minutes)
    python labs/lab16/run_lab16.py --quick      # smaller datasets
    python labs/lab16/run_lab16.py --only lab_16_2
    python labs/lab16/run_lab16.py --report     # also results/Exercise-16_Report.pdf
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import runner_kit as K  # noqa: E402
from backend.app import lab16 as L  # noqa: E402

PARTS = [("lab_16_1", "Lab 16.1 - the university enrollment system in MongoDB, Cassandra and Neo4j", lambda q: L.run_161(q)),
         ("activity", "Activity 16.1 - hybrid architecture: MongoDB operational, Cassandra audit log, Neo4j prerequisites", lambda q: L.run_activity()),
         ("lab_16_2", "Lab 16.2 - FraudQueryService: pooling, circuit breaker, unit + integration tests, latency", lambda q: L.run_162(q)),
         ("applied", "Applied to Lab 10 - the Kavach billing ledger through the polyglot fraud check", lambda q: L.run_applied())]


def checks(o):
    c = []
    a = o.get("lab_16_1") or {}
    if a.get("queries"):
        c.append(("16.1: the three queries return identical answers in MongoDB, Cassandra and Neo4j", a.get("identical", False)))
    a = o.get("activity") or {}
    if a.get("steps"):
        c.append(("Activity: Neo4j refuses the enrollment until the prerequisite chain is complete; MongoDB + Cassandra store it; audit trail written", a.get("pass", False)))
    a = o.get("lab_16_2") or {}
    if a.get("unit_tests"):
        u = a["unit_tests"]
        c.append((f"16.2 unit tests with unittest.mock: {u['passed']}/{u['total']}", u.get("ok", False)))
        c.append((f"16.2 integration tests on the real databases: {sum(t['ok'] for t in a['integration'])}/{len(a['integration'])}",
                  all(t["ok"] for t in a["integration"])))
        c.append(("16.2 circuit breaker: opens after 3 failures, skips the backend, half-open after 30 s, closes", a.get("circuit", {}).get("pass", False)))
    a = o.get("applied") or {}
    if a:
        c.append((f"Applied to Lab 10: {a.get('imported', 0)} billing payments analysed by FraudQueryService", a.get("pass", False)))
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", choices=[p[0] for p in PARTS])
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    from backend.app import polyglot as PG
    PG.wait_for(("mongodb", "cassandra", "neo4j"), 300)
    sys.exit(0 if K.run(16, PARTS, a, checks, "Exercise 16 - MongoDB + Cassandra + Neo4j (polyglot persistence)") else 1)


if __name__ == "__main__":
    main()

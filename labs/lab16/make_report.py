"""Builds results/Exercise-16_Report.pdf (the Docker Hub URL is on page 1).   python labs/lab16/make_report.py"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from report_kit import (DBC, DBN, GREEN, RED, W, P, Paragraph, bars3, box, arrow, build_doc, checks, esc, header,  # noqa: E402
                        hbars, mm, styles, tbl, yn, Drawing, Rect, String, colors)
from reportlab.platypus import PageBreak  # noqa: E402

TITLE = "Wide-Column and Graph Databases: Cassandra and Neo4j"
NOTES = [
    "Cassandra has no joins: 'students who share a course' needs 1 + N queries and a union in the application, or one more table.",
    "Every enrollment is written to two Cassandra tables (student_courses, course_students): writes are cheap, consistency between the tables is the application's job.",
    "A variable-length bound in Cypher ([:TRANSFERRED_TO*1..3]) cannot be a query parameter; the service validates max_hops (1-6) before building the string - the user id is always a parameter.",
    "Every Cassandra statement is prepared once and reused (the driver sends only the values); a TRUNCATE keeps a snapshot on disk (auto_snapshot) - the lab clears it.",
    "The circuit breaker turns a dead backend from a slow failure (connection timeout on every call) into an instant, labelled partial answer.",
    "Rings are cycles of large transfers (>= Rs 10,000) inside 24 hours; a plain cycle search would also flag ordinary people who happen to pay each other back.",
]


def architecture():
    d = Drawing(500, 150)
    d.add(Rect(2, 2, 496, 146, fillColor=colors.HexColor("#eef3fb"), strokeColor=None))
    d.add(String(8, 138, "Lab 16 - one service, three databases (polyglot persistence)", fontSize=7.5, fontName="Helvetica-Bold"))
    box(d, 10, 70, 110, 50, ["FraudQueryService", "analyze_user()", "circuit breaker x 3"], fill="#ffffff", size=6.4)
    for i, (k, lines) in enumerate((("mongodb", ["MongoDB", "transactions documents", "get_transaction(txn_id)"]),
                                    ("cassandra", ["Cassandra", "user_profiles (user_id, event_time DESC)", "get_user_profile(user_id)"]),
                                    ("neo4j", ["Neo4j", "(:User)-[:TRANSFERRED_TO]->(:User)", "detect_fraud_rings(user, hops)"]))):
        box(d, 180 + i * 106, 70, 100, 50, lines, fill="#ffffff", size=5.9)
        arrow(d, 120, 95, 180 + i * 106, 95, DBC[k])
    box(d, 10, 12, 230, 44, ["Activity: enroll() - Neo4j checks the prerequisite chain,", "MongoDB stores (transaction), Cassandra audits"], fill="#fff4dc", size=6.2)
    box(d, 252, 12, 240, 44, ["Applied to Lab 10: billing payments -> transactions +", "profile events + User nodes -> same fraud check"], fill="#fff4dc", size=6.2)
    return d


def build(out: dict, results: Path, docker_url: str) -> Path:
    st = styles()
    e = header(st, 16, TITLE, docker_url,
               "docker compose up -d lab16                  # Lab 7 -> 8 -> ... -> 15 -> 16 (+ Cassandra, Neo4j)\n"
               "open http://localhost:8000/#/lab16          # every task, live\n"
               "docker compose exec lab16 lab16 all         # every Lab 16 task + this report\n"
               "docker compose exec lab16 python -m unittest -v labs/lab16/test_fraud_service.py", out.get("environment"))
    e += checks(out, st)
    e += [Paragraph("Architecture", st["h1"]), architecture()]

    a = out.get("lab_16_1") or {}
    if a.get("queries"):
        e += [PageBreak(), Paragraph("Lab 16.1 - Model data in three databases", st["h1"]),
              Paragraph(f"A university course enrollment system: {a['load']['students']} students, {a['load']['courses']} courses, {a['load']['enrollments']} enrollments. "
                        f"Load time (s): MongoDB {a['load']['load_s']['mongodb']}, Cassandra {a['load']['load_s']['cassandra']} ({a['load']['cassandra_rows']} rows), Neo4j {a['load']['load_s']['neo4j']}.", st["p"]),
              tbl([["Database", "Schema"],
                   ["MongoDB", P("students {_id, name, dept, year, <b>course_ids[]</b>} + courses {_id, title, credits, prereqs[], <b>roster[{student_id, name, year}]</b>}; multikey indexes on course_ids and roster.student_id", st)],
                   ["Cassandra", P("student_courses PRIMARY KEY ((student_id), course_id); course_students PRIMARY KEY ((course_id), student_id) - one table per query, each enrollment written twice", st)],
                   ["Neo4j", P("(:Student {id, name})-[:ENROLLED_IN {term}]->(:Course {id, title}); unique constraints on Student.id and Course.id", st)]],
                  [26 * mm, W - 26 * mm]),
              Paragraph("Queries, answers and latency (median of 5 arguments x 10 runs)", st["h2"]),
              bars3([(q["title"].replace("Find ", "")[:34], {k: q["db"][k]["median_ms"] for k in DBC}) for q in a["queries"]], "median latency (ms)", fmt="%.2f")]
        for q in a["queries"]:
            e += [Paragraph(f"{esc(q['title'])} - identical answers: <b>{yn(q['identical_answers'])}</b> (example {esc(q['example']['arg'])}: {q['example']['count']} results)", st["h2"]),
                  tbl([["Database", "Query", "Lines / round trips", "Median"]] +
                      [[DBN[k], P(esc(q["db"][k]["code"]).replace("\n", "<br/>"), st), f"{q['db'][k]['lines']} / {q['db'][k]['round_trips']}", f"{q['db'][k]['median_ms']:.2f} ms"] for k in DBC],
                      [22 * mm, W - 70 * mm, 26 * mm, 22 * mm])]
        e += [Paragraph("Comparison", st["h2"]),
              Paragraph("<b>Syntax complexity.</b> The two direct look-ups are one statement in every database. The 'shared course' question is a single Cypher pattern, a 4-stage "
                        "aggregation in MongoDB, and in Cassandra 1 + N queries plus a union in Python (no joins in CQL). <b>Expected performance.</b> MongoDB and Cassandra "
                        "answer key look-ups from one index entry / one partition; Neo4j's cost depends on the relationships it follows, not on the database size, which makes "
                        "multi-hop questions cheap and full scans expensive; Cassandra's fan-out is only as fast as its slowest partition.", st["p"])]
    act = out.get("activity") or {}
    if act.get("steps"):
        e += [Paragraph("Activity 16.1 - hybrid architecture", st["h1"]),
              Paragraph("MongoDB keeps the operational documents (an enrollment updates the student and the course roster in one transaction), Cassandra keeps the append-only "
                        "audit log (partition = (student, day), clustering = timeuuid DESC), Neo4j answers prerequisite-chain queries with "
                        "<font face='Courier'>(:Course)-[:REQUIRES*1..]-&gt;(:Course)</font> and refuses an enrollment while a prerequisite is not COMPLETED.", st["p"]),
              Paragraph(f"Chain of {esc(act['course'])}: <b>{esc(' -> '.join(act['chain']['longest_path']))}</b> ({len(act['chain']['all'])} prerequisite courses).", st["p"]),
              tbl([["Step", "Result", "Missing prerequisites"]] + [[P(esc(s["step"]), st), "ok" if s.get("ok") else "refused", P(esc(", ".join(s.get("missing") or [])), st)] for s in act["steps"]],
                  [W - 80 * mm, 20 * mm, 60 * mm]),
              Paragraph(f"Stored afterwards: MongoDB {act['stored']['mongodb']}, Cassandra {act['stored']['cassandra']}; audit rows today: {len(act['audit'])}.", st["p"])]
    f = out.get("lab_16_2") or {}
    if f.get("unit_tests"):
        u = f["unit_tests"]
        e += [PageBreak(), Paragraph("Lab 16.2 - Build a hybrid query service", st["h1"]),
              Paragraph(f"Dataset: {f['load']['users']} users, {f['load']['transactions']} transactions (MongoDB), {f['load']['profile_rows']} events (Cassandra, user_profiles "
                        f"partitioned by user_id, clustered by event_time DESC), {f['load']['transfers']} transfers (Neo4j) and {len(f['load']['rings'])} planted fraud rings.", st["p"]),
              tbl([["Method", "Database", "Query"],
                   ["get_transaction(txn_id)", "MongoDB", P("db.transactions.find_one({'txn_id': txn_id}) - the full document (amount, merchant, timestamp, risk_score)", st)],
                   ["get_user_profile(user_id)", "Cassandra", P("SELECT ... FROM user_profiles WHERE user_id = ? ORDER BY event_time DESC LIMIT ? (prepared)", st)],
                   ["detect_fraud_rings(user, 3)", "Neo4j", P("MATCH path = (u:User {id:$uid})-[:TRANSFERRED_TO*1..3]-(c:User) ... and cycles of large transfers within 24 h", st)]],
                  [40 * mm, 22 * mm, W - 62 * mm]),
              Paragraph("Connection pooling: " + esc("; ".join(f"{DBN[k]} {v}" for k, v in f["pooling"].items())) + ".", st["p"]),
              Paragraph(f"Unit tests with unittest.mock: {u['passed']}/{u['total']} passed", st["h2"]),
              tbl([["Test", "Case", ""]] + [[P(esc(t["test"]), st), esc(t["case"]), "ok" if t["ok"] else "FAIL"] for t in u["tests"]], [W - 60 * mm, 44 * mm, 16 * mm]),
              Paragraph("Integration test (the full fraud analysis workflow on the real databases)", st["h2"]),
              tbl([["Check", ""]] + [[P(esc(t["test"]), st), yn(t["ok"])] for t in f["integration"]], [W - 18 * mm, 18 * mm],
                  extra=[("TEXTCOLOR", (1, i + 1), (1, i + 1), GREEN if t["ok"] else RED) for i, t in enumerate(f["integration"])]),
              Paragraph("Latency characteristics", st["h2"]),
              hbars([(k, v["median_ms"], DBC["mongodb"] if "Mongo" in k else DBC["cassandra"] if "Cassandra" in k else DBC["neo4j"] if "Neo4j" in k else None)
                     for k, v in f["latency"].items()], "median latency per method (ms, 50 calls)", unit=" ms"),
              tbl([["Method", "median", "p95", "min", "max"]] + [[P(esc(k), st), f"{v['median_ms']} ms", f"{v['p95_ms']} ms", f"{v['min_ms']} ms", f"{v['max_ms']} ms"] for k, v in f["latency"].items()],
                  [W - 88 * mm, 22 * mm, 22 * mm, 22 * mm, 22 * mm]),
              Paragraph("Circuit breaker (real service, Cassandra outage injected; a controllable clock replaces the 30-second wait)", st["h2"]),
              tbl([["Call", "Cassandra circuit", "Cassandra", "Degraded", "ms"]] +
                  [[P(esc(x["call"]), st), x["cassandra"], "answered" if x["cassandra_result"].get("ok") else ("skipped" if x["cassandra_result"].get("skipped") else "error"),
                    ", ".join(x["degraded"]) or "-", f"{x['ms']}"] for x in f["circuit"]["events"]],
                  [W - 92 * mm, 24 * mm, 22 * mm, 26 * mm, 20 * mm])]
    ap = out.get("applied") or {}
    if ap:
        e += [Paragraph("Applied to Lab 10 (mentor's rule)", st["h1"]),
              Paragraph(("The classifier billing ledger (" + esc(ap.get("backend", "")) + f"): {ap.get('imported', 0)} payments of {ap.get('users', 0)} Kavach users became MongoDB transactions, "
                         "Cassandra profile events and Neo4j User nodes, and were analysed by the same FraudQueryService.") if ap.get("available") else
                        "Billing ledger not reachable at the time of the run: " + esc(str(ap.get("reason", ""))), st["p"])]
        if ap.get("sample"):
            e.append(tbl([["User", "Risk", "Transactions", "Events", "Rings"]] + [[esc(s["user"]), f"{s['risk']['level']} ({s['risk']['score']})", s["transactions"], s["profile_events"], s["rings"]] for s in ap["sample"]],
                         [W - 88 * mm, 24 * mm, 22 * mm, 22 * mm, 20 * mm]))
    e += [Paragraph("Notes on correctness", st["h1"])] + [Paragraph("- " + esc(n), st["p"]) for n in NOTES]
    return build_doc(results / "Exercise-16_Report.pdf", TITLE, 16, e)


if __name__ == "__main__":
    res = Path(os.getenv("RESULTS_DIR", str(Path(__file__).resolve().parents[2] / "results")))
    print(build(json.loads((res / "lab16_results.json").read_text()), res, os.getenv("DOCKER_URL", "")))

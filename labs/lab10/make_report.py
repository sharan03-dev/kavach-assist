"""Builds results/Exercise-10_Report.pdf - the file to submit (contains the Docker Hub URL on page 1)."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

ROOT = Path(__file__).resolve().parent.parent.parent
INK, ACCENT, GREY = colors.HexColor("#13212c"), colors.HexColor("#b87700"), colors.HexColor("#56687a")
STUDENT = "M NAGASAI GIRISHARAN  |  24BTRCL114  |  5 AIML-B  |  23CSE528 NoSQL Database using MongoDB"


def styles():
    s = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=s["Title"], fontSize=22, textColor=INK, alignment=0, spaceAfter=4),
        "sub": ParagraphStyle("s", parent=s["Normal"], fontSize=10.5, textColor=GREY, spaceAfter=10),
        "h1": ParagraphStyle("h1", parent=s["Heading1"], fontSize=15, textColor=INK, spaceBefore=14, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=s["Heading2"], fontSize=11.5, textColor=ACCENT, spaceBefore=10, spaceAfter=3),
        "p": ParagraphStyle("p", parent=s["BodyText"], fontSize=10, leading=14.5, spaceAfter=5),
        "code": ParagraphStyle("c", fontName="Courier", fontSize=6.9, leading=8.4, backColor=colors.HexColor("#f2f5f7"),
                               borderPadding=4, leftIndent=2),
    }


def sections(console: str) -> dict:
    chunks, parts = {}, re.split(r"\n={76}\n(.+)\n={76}\n", console)
    for i in range(1, len(parts) - 1, 2):
        chunks[parts[i].strip()] = parts[i + 1].rstrip()
    return chunks


def code_block(text: str, st, width=135):
    lines = []
    for ln in text.splitlines():
        while len(ln) > width:
            lines.append(ln[:width]); ln = "   " + ln[width:]
        lines.append(ln)
    return [Preformatted("\n".join(lines[i:i + 70]), st["code"]) for i in range(0, len(lines), 70)]


def tbl(data, widths=None, head=True):
    t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
    style = [("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c3ced7")),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if head:
        style += [("BACKGROUND", (0, 0), (-1, 0), INK), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    t.setStyle(TableStyle(style))
    return t


def chart(rows):
    d = Drawing(440, 210)
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 50, 40, 370, 140
    bc.data = [[r["transactional"]["per_second"] for r in rows], [r["non_transactional"]["per_second"] for r in rows]]
    bc.categoryAxis.categoryNames = [f"{r['threads']} thread{'s' if r['threads'] > 1 else ''}" for r in rows]
    bc.bars[0].fillColor, bc.bars[1].fillColor = ACCENT, colors.HexColor("#2f6fd0")
    bc.valueAxis.valueMin = 0
    bc.valueAxis.labels.fontSize = bc.categoryAxis.labels.fontSize = 8
    d.add(String(50, 192, "Successful reservations per second (higher is better)", fontSize=9, fillColor=INK))
    lg = Legend(); lg.x, lg.y, lg.fontSize, lg.alignment = 170, 24, 8, "right"
    lg.colorNamePairs = [(ACCENT, "multi-document transaction"), (colors.HexColor("#2f6fd0"), "findAndModify (no transaction)")]
    lg.columnMaximum = 1
    d.add(bc); d.add(lg)
    return d


def build(out: dict, results: Path, docker_url: str) -> Path:
    st = styles()
    console = (results / "console_10.txt").read_text(encoding="utf-8") if (results / "console_10.txt").exists() else ""
    sec = sections(console)
    pdf = results / "Exercise-10_Report.pdf"
    doc = SimpleDocTemplate(str(pdf), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title="Exercise 10 - Transactions, Atomicity, Durability & Journaling",
                            author="M Nagasai Girisharan")
    e = []
    e += [Paragraph("Exercise 10 - Transactions, Atomicity, Durability &amp; Journaling", st["title"]),
          Paragraph(escape(STUDENT), st["sub"])]
    url = docker_url or "(set DOCKER_URL / run ./publish.sh to fill in the Docker Hub link)"
    e += [Paragraph("<b>Docker Hub image (submission):</b>", st["p"]),
          Paragraph(f'<link href="{escape(url)}" color="#2f6fd0"><u>{escape(url)}</u></link>', st["p"]),
          Paragraph("<b>Demonstration for grading (from the uploaded image only):</b>", st["p"])]
    img = url.split("hub.docker.com/r/")[-1] if "hub.docker.com/r/" in url else "<user>/nosql-lab10"
    e += code_block(f"docker run --rm {img} compose > docker-compose.yml\ndocker compose up -d lab10   "
                    f"# starts Lab 7 -> 8 -> 9 -> 10 in order\nopen http://localhost:8000/#/lab10     "
                    f"# website; or: docker compose exec lab10 lab10 all", st)
    e.append(Paragraph(f"Generated {datetime.now():%d %b %Y %H:%M}. MongoDB {out['environment']['mongodb']}, replica set "
                       f"{out['environment']['replica_set']}, PyMongo {out['environment']['pymongo']}.", st["sub"]))

    # --- at a glance
    r1, r2 = out.get("lab_10_1"), out.get("lab_10_2")
    glance = [["Check", "Result"]]
    if r1:
        glance += [["10.1 transfer commits atomically", "PASS" if r1["success"]["ok"] else "FAIL"],
                   ["10.1 insufficient funds: balances unchanged (rollback)", "PASS" if r1["insufficient"]["unchanged"] else "FAIL"],
                   ["10.1 crash after debit: rollback", "PASS" if r1["crash"]["unchanged"] else "FAIL"],
                   ["10.1 activity: retry with exponential back-off, money conserved",
                    "PASS" if r1["conflict"]["A"]["ok"] and r1["conflict"]["B"]["ok"] and r1["conflict"]["money_conserved"] else "FAIL"]]
    if r2:
        w = r2["with_retry"]
        glance += [["10.2 with retry: 5 reserved, 3 out of stock, never oversold",
                    "PASS" if w["summary"]["reserved"] == 5 and w["summary"]["out_of_stock"] == 3 and not w["oversold"] else "FAIL"],
                   ["10.2 without retry: write conflicts visible", f"{r2['no_retry']['summary']['write_conflict']} request(s) failed"],
                   ["10.2 atomicity: transaction vs findAndModify",
                    "transaction consistent" if r2["atomicity"]["transactional"]["consistent"] else "FAIL"]]
    e += [Paragraph("Results at a glance", st["h1"]), tbl(glance, [120 * mm, 54 * mm])]

    # --- Lab 10.1
    e += [Paragraph("Lab 10.1 - Bank transfer transaction", st["h1"]),
          Paragraph("<b>Objective.</b> Move money between two accounts so that the debit, the credit and the history "
                    "record are one atomic unit (ACID). <b>Method.</b> PyMongo <font face='Courier'>client.start_session()</font> "
                    "+ <font face='Courier'>session.start_transaction()</font> on a 3-member replica set (snapshot read concern, "
                    "majority write concern). The debit uses the filter <font face='Courier'>balance &gt;= amount</font> and the code "
                    "checks <font face='Courier'>matched_count</font>: a non-matching filter does not raise by itself, so without "
                    "this check the credit would still run. Activity 10.1: the transaction is wrapped in a retry loop for "
                    "<font face='Courier'>TransientTransactionError</font> (the label MongoDB puts on a write conflict) "
                    "with delay 0.1 s x 2<super>attempt</super>.", st["p"])]
    k = next((k for k in sec if k.startswith("Lab 10.1")), None)
    if k:
        e += [Paragraph("Captured terminal output", st["h2"])] + code_block(sec[k], st)
    if r1:
        c = r1["conflict"]
        e += [Paragraph("Activity 10.1 - what the retry did", st["h2"]),
              tbl([["Transfer", "Outcome", "Attempts", "Conflicts", "Back-off delays (s)", "Time (ms)"],
                   ["A (holds 0.6 s)", c["A"]["outcome"], c["A"]["attempts"], c["A"]["conflicts"], str(c["A"]["delays"]), c["A"]["ms"]],
                   ["B (0.15 s later)", c["B"]["outcome"], c["B"]["attempts"], c["B"]["conflicts"], str(c["B"]["delays"]), c["B"]["ms"]]]),
              Spacer(1, 4),
              Paragraph(f"Total money before {c['total_before']}, after {c['total_after']}: "
                        f"{'conserved' if c['money_conserved'] else 'NOT conserved'}. The second transaction touched a document "
                        "that the first had written but not yet committed, so the server aborted it at once "
                        "(MongoDB does not wait for locks, so there are no classic deadlocks); the retry loop waited and "
                        "succeeded after the first commit.", st["p"])]

    # --- Lab 10.2
    e += [PageBreak(), Paragraph("Lab 10.2 - Inventory reservation with conflict handling", st["h1"]),
          Paragraph("<b>Objective.</b> Decrement stock only when an order is created, with many concurrent buyers. "
                    "<b>Method.</b> <font face='Courier'>reserve_inventory()</font> reads the product, checks stock, applies "
                    "<font face='Courier'>$inc</font> on stock and version (optimistic concurrency) and inserts a pending order "
                    "in one transaction. Eight threads (ThreadPoolExecutor, released together by a barrier) each reserve "
                    "1 unit of a product with stock 5, first without and then with retry; throughput is then measured with "
                    "1, 4, 8 and 16 threads and compared with a non-transactional "
                    "<font face='Courier'>find_one_and_update</font>.", st["p"])]
    k = next((k for k in sec if k.startswith("Lab 10.2 - Inventory")), None)
    if k:
        e += [Paragraph("Captured terminal output - concurrency and atomicity", st["h2"])] + code_block(sec[k], st)
    b = out.get("throughput")
    if b:
        e += [Paragraph("Throughput (measured)", st["h2"]),
              tbl([["Threads", "Txn ok/s", "Txn retries", "Txn failed", "findAndModify ok/s"]] +
                  [[r["threads"], r["transactional"]["per_second"], r["transactional"]["retries"],
                    r["transactional"]["failed"], r["non_transactional"]["per_second"]] for r in b["rows"]]),
              Spacer(1, 6), KeepTogether([chart(b["rows"])])]
        e += [Paragraph("Reading the numbers", st["h2"])] + [Paragraph("&bull; " + escape(x), st["p"]) for x in b["explanation"]]
        k = next((k for k in sec if "Throughput" in k), None)
        if k:
            e += [Paragraph("Captured terminal output - throughput", st["h2"])] + code_block(sec[k], st)

    # --- Mentor's update: Lab 10 applied to the Lab 7 classifier
    b = out.get("billing")
    if b:
        from reportlab.lib.units import mm as _mm
        Wd = A4[0] - 32 * _mm
        f, c, d, inv, du = b["free_trial_race"], b["crash_after_debit"], b["double_click"], b["invariants"], b["durability"]
        from reportlab.lib.styles import ParagraphStyle as _PS
        from xml.sax.saxutils import escape as _esc
        _cs = _PS("bcell", fontName="Helvetica", fontSize=7.8, leading=9.6)
        _C = lambda t: Paragraph(_esc(str(t)), _cs)
        e += [PageBreak(), Paragraph("Lab 10 applied to Lab 7 - the classifier paywall", st["h1"]),
              Paragraph("Mentor's update: the Lab 7 threat classifier is free for the first <b>3 classifications</b> of a "
                        "user and then costs <b>Rs 100 per month</b>. The billing is built from this exercise's transactions "
                        "(database cti_billing: accounts, usage, payments, revenue). <b>One use</b> = read the account, take a "
                        "free trial with a conditional $inc or check the plan, run the classifier, insert the usage record, "
                        "commit - the use is only counted if the classification succeeded. <b>One subscription</b> = check "
                        "wallet >= 100, debit it, extend the plan by 30 days, insert the payment (unique per user and "
                        "idempotency key), add the amount to revenue, commit. Payments are simulated: the wallet is topped up "
                        "by a simulated UPI payment, itself one transaction.", st["p"]),
              tbl([["ACID property", "Experiment", "Measured result", "Result"]] + [[_C(x) for x in row] for row in [
                   ["Atomicity + Isolation", f"{f['requests']} classifications at the same instant by a new user",
                    f"{f['granted']} granted, {f['refused']} refused, {f['retries']} write-conflict retries, free_used = {f['free_used']}",
                    "PASS" if f["pass"] else "FAIL"],
                   ["Atomicity", "crash simulated after the wallet debit", f"wallet Rs {c['wallet_before']} -> Rs {c['wallet_after']}, "
                    f"plan active {c['plan_active_after']}, payments {c['subscription_payments']}", "PASS" if c["pass"] else "FAIL"],
                   ["Consistency", "double click on Pay (same idempotency key)", f"{', '.join(d['responses'])}; wallet Rs "
                    f"{d['wallet_before']} -> Rs {d['wallet_after']}, {d['subscription_payments']} payment", "PASS" if d["pass"] else "FAIL"],
                   ["Consistency", "money invariant over all accounts", f"top-ups Rs {inv['topups']} - subscriptions Rs "
                    f"{inv['subscriptions']} = wallets Rs {inv['wallets']}; revenue Rs {inv['revenue']}",
                    "PASS" if inv["money_conserved"] and inv["revenue_matches"] else "FAIL"],
                   ["Durability", "commit write concern", f"w: majority, writeConcernMajorityJournalDefault = {du['journal_default']}",
                    "PASS" if du["pass"] else "FAIL"]]],
                  [28 * _mm, 50 * _mm, Wd - 96 * _mm, 18 * _mm]),
              Paragraph("Why the free-trial race needs a transaction: without one, all 10 requests could read free_used = 2 "
                        "and all get the last trial. Inside a transaction the conditional update {free_used: value read} plus "
                        "MongoDB's write-conflict detection lets exactly one of them win; the losers are retried by run_txn "
                        "and then see that no trial is left. On the website: Classifier page (sign in, use the 3 free "
                        "classifications, the 4th shows the paywall, add money, subscribe) and the 'Applied to the "
                        "classifier' panel on the Lab 10 page.", st["p"])]
        k = next((k for k in sec if k.startswith("Lab 10 applied to Lab 7")), None)
        if k:
            e += [Paragraph("Captured terminal output", st["h2"])] + code_block(sec[k], st)

    # --- Durability / journaling
    e += [Paragraph("Durability and journaling (theory used in this exercise)", st["h1"]),
          Paragraph("A transaction commits with write concern <font face='Courier'>w:majority</font>. With the default "
                    "<font face='Courier'>writeConcernMajorityJournalDefault: true</font> the acknowledgement is sent only after "
                    "a majority of members have written the change to their on-disk journal (write-ahead log), so a committed "
                    "transaction survives a crash or a primary change. Before the commit nothing is visible to other "
                    "sessions, and an abort discards all writes: atomicity and isolation (snapshot read concern). "
                    "Exercise 9 measured the cost of j:true / w:majority; Exercise 10 shows what they buy.", st["p"]),
          Paragraph("Conclusion", st["h1"]),
          Paragraph("Multi-document transactions make the bank transfer and the inventory reservation all-or-nothing, "
                    "also when the process crashes in the middle or many clients collide on the same document. The cost is "
                    "lower throughput and the need for a retry loop on TransientTransactionError. For a single-document "
                    "change findAndModify is faster and equally atomic - but it cannot protect a second collection.", st["p"])]

    # --- appendix
    e += [PageBreak(), Paragraph("Appendix - source code (backend/app/lab10.py)", st["h1"])]
    e += code_block((ROOT / "backend/app/lab10.py").read_text(encoding="utf-8"), st)
    doc.build(e)
    return pdf


if __name__ == "__main__":
    import json, os, sys
    res = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
    print(build(json.loads((res / "lab10_results.json").read_text()), res, os.getenv("DOCKER_URL", "")))

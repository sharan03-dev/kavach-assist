"""Builds results/Exercise-11_Report.pdf - the file to submit (contains the Docker Hub URL on page 1)."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

ROOT = Path(__file__).resolve().parent.parent.parent
INK, ACCENT, GREY = colors.HexColor("#13212c"), colors.HexColor("#b87700"), colors.HexColor("#56687a")
BLUE, GREEN, RED = colors.HexColor("#2f6fd0"), colors.HexColor("#178a58"), colors.HexColor("#d92638")
STUDENT = "M NAGASAI GIRISHARAN  |  24BTRCL114  |  5 AIML-B  |  23CSE528 NoSQL Database using MongoDB"
SHORT = {"order_id": "order_id", "customer_id": "customer_id", "order_date": "order_date",
         "hashed_customer_id": "hashed cust.", "category": "category*", "compound": "compound*"}


def styles():
    s = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=s["Title"], fontSize=22, textColor=INK, alignment=0, spaceAfter=4),
        "sub": ParagraphStyle("s", parent=s["Normal"], fontSize=10.5, textColor=GREY, spaceAfter=10),
        "h1": ParagraphStyle("h1", parent=s["Heading1"], fontSize=15, textColor=INK, spaceBefore=14, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=s["Heading2"], fontSize=11.5, textColor=ACCENT, spaceBefore=10, spaceAfter=3),
        "p": ParagraphStyle("p", parent=s["BodyText"], fontSize=10, leading=14.5, spaceAfter=5),
        "cell": ParagraphStyle("cell", parent=s["BodyText"], fontSize=7.6, leading=9.4),
        "cellb": ParagraphStyle("cellb", parent=s["BodyText"], fontSize=7.6, leading=9.4, fontName="Helvetica-Bold"),
        "code": ParagraphStyle("c", fontName="Courier", fontSize=6.6, leading=8.1, backColor=colors.HexColor("#f2f5f7"),
                               borderPadding=4, leftIndent=2),
    }


def sections(console: str) -> dict:
    chunks, parts = {}, re.split(r"\n={76}\n(.+)\n={76}\n", console)
    for i in range(1, len(parts) - 1, 2):
        chunks[parts[i].strip()] = parts[i + 1].rstrip()
    return chunks


def code_block(text: str, st, width=140):
    lines = []
    for ln in text.splitlines():
        while len(ln) > width:
            lines.append(ln[:width]); ln = "   " + ln[width:]
        lines.append(ln)
    return [Preformatted("\n".join(lines[i:i + 75]), st["code"]) for i in range(0, len(lines), 75)]


def tbl(data, widths=None, head=True, zebra=False):
    t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
    style = [("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c3ced7")),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if head:
        style += [("BACKGROUND", (0, 0), (-1, 0), INK), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    if zebra:
        style += [("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7f9")])]
    t.setStyle(TableStyle(style))
    return t


def P(text, st, key="cell"):
    return Paragraph(text, st[key])


# ----------------------------------------------------------------------------- drawings
def _box(d, x, y, w, h, title, sub=None, fill="#ffffff", stroke=INK, bold=True):
    d.add(Rect(x, y, w, h, rx=4, ry=4, fillColor=colors.HexColor(fill), strokeColor=stroke, strokeWidth=1.1))
    d.add(String(x + w / 2, y + h / 2 + (3 if sub else -3), title, fontName="Helvetica-Bold" if bold else "Helvetica",
                 fontSize=8.6, textAnchor="middle", fillColor=INK))
    if sub:
        d.add(String(x + w / 2, y + h / 2 - 8, sub, fontName="Helvetica", fontSize=6.8, textAnchor="middle", fillColor=GREY))


def _arrow(d, x1, y1, x2, y2, color=INK):
    d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=1))
    import math
    a = math.atan2(y2 - y1, x2 - x1)
    p1 = (x2 - 6 * math.cos(a - 0.4), y2 - 6 * math.sin(a - 0.4))
    p2 = (x2 - 6 * math.cos(a + 0.4), y2 - 6 * math.sin(a + 0.4))
    d.add(Polygon([x2, y2, p1[0], p1[1], p2[0], p2[1]], fillColor=color, strokeColor=color))


def architecture(env: dict | None):
    """The mentor's 'Scaling beyond a single server' picture, drawn with the members that really ran."""
    d = Drawing(480, 230)
    _box(d, 190, 196, 100, 26, "Application", "PyMongo / website")
    _box(d, 180, 146, 120, 28, "mongos router", "mongos:27030", fill="#fff4dc")
    _arrow(d, 240, 196, 240, 174)
    shards = (env or {}).get("shards") or [{"name": "shardA", "members": [{"name": "shard-a1:27111", "state": "PRIMARY"},
                                                                         {"name": "shard-a2:27112", "state": "SECONDARY"}]},
                                           {"name": "shardB", "members": [{"name": "shard-b1:27121", "state": "PRIMARY"},
                                                                         {"name": "shard-b2:27122", "state": "SECONDARY"}]}]
    cfg = (env or {}).get("config") or {"name": "cfgrs", "members": [{"name": "cfg1:27101", "state": "PRIMARY"}]}
    _box(d, 330, 120, 140, 34, "Config server replica set", f"{cfg['name']}: " + ", ".join(m["name"] for m in cfg["members"]),
         fill="#eef3fb")
    _arrow(d, 300, 160, 330, 145, BLUE)
    d.add(String(312, 158, "chunk map", fontSize=6.5, fillColor=BLUE))
    xs = [20, 250] if len(shards) == 2 else [20 + i * (440 / len(shards)) for i in range(len(shards))]
    for x, s in zip(xs, shards):
        d.add(Rect(x, 8, 210, 92, rx=5, ry=5, fillColor=colors.HexColor("#f6f8fa"), strokeColor=GREY, strokeWidth=0.8))
        d.add(String(x + 105, 86, f"{s['name']}  (replica set = one shard)", fontName="Helvetica-Bold", fontSize=8.4,
                     textAnchor="middle", fillColor=INK))
        for i, m in enumerate(s["members"][:3]):
            prim = m["state"] == "PRIMARY"
            _box(d, x + 12, 50 - i * 32, 186, 26, m["name"], m["state"].lower(),
                 fill="#fff4dc" if prim else "#ffffff", stroke=ACCENT if prim else GREY, bold=prim)
        _arrow(d, 240, 146, x + 105, 100)
    return d


def stacked_distribution(cands):
    d = Drawing(470, 200)
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 45, 40, 400, 130
    shards = list(cands[0]["distribution"]["live_by_shard"].keys())
    tot = [sum(c["distribution"]["live_by_shard"].values()) or 1 for c in cands]
    bc.data = [[100 * c["distribution"]["live_by_shard"][s] / t for c, t in zip(cands, tot)] for s in shards]
    bc.categoryAxis.style = "stacked"
    bc.categoryAxis.categoryNames = [SHORT.get(c["id"], c["id"]) for c in cands]
    bc.valueAxis.valueMin, bc.valueAxis.valueMax, bc.valueAxis.valueStep = 0, 100, 25
    bc.bars[0].fillColor, bc.bars[1].fillColor = ACCENT, BLUE
    bc.valueAxis.labels.fontSize = bc.categoryAxis.labels.fontSize = 7.5
    d.add(bc)
    d.add(Line(45, 40 + 65, 445, 40 + 65, strokeColor=RED, strokeWidth=0.8, strokeDashArray=[3, 2]))
    d.add(String(447, 103, "50 %", fontSize=7, fillColor=RED))
    d.add(String(45, 185, "Where the NEW orders were routed (% per shard, balancer off)", fontSize=9, fillColor=INK))
    lg = Legend(); lg.x, lg.y, lg.fontSize, lg.alignment, lg.columnMaximum = 170, 18, 7.5, "right", 1
    lg.colorNamePairs = [(ACCENT, shards[0]), (BLUE, shards[1])]
    d.add(lg)
    return d


def routing_lines(r2):
    d = Drawing(470, 205)
    lc = HorizontalLineChart()
    lc.x, lc.y, lc.width, lc.height = 50, 40, 400, 130
    a, b = r2["shards"]
    hd, ed = r2["collections"]["hotspot_data"]["timeline"], r2["collections"]["even_data"]["timeline"]
    lc.data = [[x["docs"][a] for x in hd], [x["docs"][b] for x in hd], [x["docs"][a] for x in ed], [x["docs"][b] for x in ed]]
    lc.categoryAxis.categoryNames = [str(x["batch"]) for x in hd]
    lc.valueAxis.valueMin = 0
    cols = [RED, colors.HexColor("#f0a0a8"), GREEN, colors.HexColor("#8fd3b4")]
    for i, c in enumerate(cols):
        lc.lines[i].strokeColor, lc.lines[i].strokeWidth = c, 1.6
        lc.lines[i].symbol = makeMarker("FilledCircle", size=3)
    lc.valueAxis.labels.fontSize = lc.categoryAxis.labels.fontSize = 7.5
    d.add(lc)
    d.add(String(50, 190, "Documents per shard after each batch of 950 inserts (phase 2)", fontSize=9, fillColor=INK))
    d.add(String(250, 26, "batch", fontSize=7.5, fillColor=GREY, textAnchor="middle"))
    lg = Legend(); lg.x, lg.y, lg.fontSize, lg.alignment, lg.columnMaximum = 60, 12, 7, "right", 1
    lg.colorNamePairs = [(cols[0], f"hotspot_data {a}"), (cols[1], f"hotspot_data {b}"), (cols[2], f"even_data {a}"),
                         (cols[3], f"even_data {b}")]
    lg.dx, lg.deltax = 8, 105
    d.add(lg)
    return d


def latency_plot(rs):
    d = Drawing(470, 200)
    series = [(float(t), float(ms)) for t, ms in rs["writes"]["series"]]
    if not series:
        return d
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 50, 35, 400, 130
    lp.data = [series]
    lp.lines[0].strokeColor, lp.lines[0].strokeWidth = BLUE, 0.7
    lp.xValueAxis.valueMin, lp.yValueAxis.valueMin = 0, 0
    lp.xValueAxis.labels.fontSize = lp.yValueAxis.labels.fontSize = 7.5
    d.add(lp)
    tmax = max(t for t, _ in series) or 1
    for ev in rs["timeline"]:
        if ev["source"] == "coordinator" and ev["state"] in ("cloning", "applying", "blocking-writes"):
            x = 50 + 400 * min(ev["t"], tmax) / tmax
            d.add(Line(x, 35, x, 165, strokeColor=ACCENT, strokeWidth=0.7, strokeDashArray=[2, 2]))
            d.add(String(x + 2, 158 - 9 * ["cloning", "applying", "blocking-writes"].index(ev["state"]), ev["state"],
                         fontSize=6.8, fillColor=ACCENT))
    d.add(String(50, 185, "Latency of every application insert during reshardCollection (ms)", fontSize=9, fillColor=INK))
    d.add(String(250, 18, "seconds since the writer started", fontSize=7.5, fillColor=GREY, textAnchor="middle"))
    return d


# ----------------------------------------------------------------------------- the report
def build(out: dict, results: Path, docker_url: str) -> Path:
    st = styles()
    console = (results / "console_11.txt").read_text(encoding="utf-8") if (results / "console_11.txt").exists() else ""
    sec = sections(console)
    pdf = results / "Exercise-11_Report.pdf"
    doc = SimpleDocTemplate(str(pdf), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=15 * mm,
                            bottomMargin=15 * mm, title="Exercise 11 - Sharding & Horizontal Scalability",
                            author="M Nagasai Girisharan")
    W = A4[0] - 32 * mm
    env = out.get("environment", {})
    r1, r2, rs = out.get("lab_11_1"), out.get("lab_11_2"), out.get("resharding")
    e = [Paragraph("Exercise 11 - Sharding &amp; Horizontal Scalability", st["title"]), Paragraph(escape(STUDENT), st["sub"])]
    url = docker_url or "(set DOCKER_URL / run ./publish.sh to fill in the Docker Hub link)"
    e += [Paragraph("<b>Docker Hub image (submission):</b>", st["p"]),
          Paragraph(f'<link href="{escape(url)}" color="#2f6fd0"><u>{escape(url)}</u></link>', st["p"]),
          Paragraph("<b>Demonstration for grading (from the uploaded image only):</b>", st["p"])]
    img = url.split("hub.docker.com/r/")[-1] if "hub.docker.com/r/" in url else "<user>/nosql-lab11"
    e += code_block(f"docker run --rm {img} compose > docker-compose.yml\n"
                    f"docker compose up -d lab11          # starts Lab 7 -> 8 -> 9 -> 10 -> 11 + the sharded cluster\n"
                    f"open http://localhost:8000/#/lab11   # website; or: docker compose exec lab11 lab11 all\n"
                    f"mongosh mongodb://localhost:27030    # mongos: sh.status(), db.hotspot_data.getShardDistribution()", st)
    shards_txt = "; ".join(f"{s['name']} = " + " + ".join(f"{m['name']} ({m['state'].lower()})" for m in s["members"])
                           for s in env.get("shards", []))
    e.append(Paragraph(f"Generated {datetime.now():%d %b %Y %H:%M}. MongoDB {env.get('mongodb', '?')} sharded cluster: "
                       f"mongos router, config server replica set {env.get('config', {}).get('name', 'cfgrs')}, "
                       f"{escape(shards_txt)}. PyMongo {env.get('pymongo', '?')}.", st["sub"]))

    if out.get("checks"):
        e += [Paragraph("Results at a glance", st["h1"]),
              tbl([["Check (measured on the running cluster)", "Result"]] +
                  [[c["name"], "PASS" if c["ok"] else "FAIL"] for c in out["checks"]], [W - 22 * mm, 22 * mm], zebra=True)]

    e += [Paragraph("The cluster", st["h1"]),
          Paragraph("One <b>mongos</b> router is the only address the application uses. It reads the chunk map (which "
                    "shard-key range lives on which shard) from the <b>config server replica set</b> and forwards every "
                    "operation to the shard(s) that own the matching ranges. Each <b>shard</b> is itself a replica set "
                    "(primary + secondary), so sharding adds write capacity while replication keeps each part available. "
                    "The picture below shows the members that were actually running when this report was generated.", st["p"]),
          architecture(env)]

    # ------------------------------------------------------------------ Lab 11.1
    if r1:
        cands = r1["candidates"]
        best = next(c for c in cands if c["id"] == r1["recommended"])
        by = {c["id"]: c for c in cands}
        e += [PageBreak(), Paragraph("Lab 11.1 - Evaluate shard key choices", st["h1"]),
              Paragraph("<b>Objective.</b> Choose the shard key of an e-commerce order-management system from "
                        "<font face='Courier'>order_id</font>, <font face='Courier'>customer_id</font>, "
                        "<font face='Courier'>order_date</font> and hashed <font face='Courier'>customer_id</font> by "
                        "assessing cardinality, distribution and query targeting.", st["p"]),
              Paragraph(f"<b>Method - measured, not assumed.</b> {r1['n_orders']:,} orders of "
                        f"{r1['customers']:,} customers over one year were generated (60 % of customers exist on 1 January, "
                        "40 % sign up during the year and get the next customer id; a few loyal customers order much more "
                        "than others; Electronics is 30 % of orders). Every candidate got its <b>own</b> sharded collection "
                        f"with exactly the same orders and the same secondary indexes. The first {r1['history']:,} orders "
                        "(history) were loaded, then the chunk was split at the median key and the lower half migrated to "
                        f"{r1['shards'][1]} - the balancer's job, done explicitly so it is repeatable. The remaining "
                        f"{r1['live']:,} orders then arrived in time order in {r1['batches']} batches with the balancer "
                        "stopped, so the per-shard counts show where mongos <i>routed</i> each insert. Query targeting is "
                        "read from <font face='Courier'>explain()</font> through mongos for four typical queries, weighted "
                        "by how often the system runs them.", st["p"])]
        e += [Paragraph("Workload used for query targeting", st["h2"]),
              tbl([["Query", "Share", "Filter used in explain()"]] +
                  [[q["label"], f"{q['weight']:.0%}", P(escape(r1["queries"][q["id"]]), st)] for q in r1["workload"]],
                  [58 * mm, 14 * mm, W - 72 * mm], zebra=True)]
        hdr = [P("<b>Shard key</b>", st), P("<b>Cardinality</b>", st), P("<b>Distribution of new orders</b>", st),
               P("<b>Hot-spot risk</b>", st), P("<b>Query targeting</b>", st), P("<b>Score</b>", st), P("<b>Verdict</b>", st)]
        rows = [hdr]
        sys_path_add()
        from backend.app.lab11 import matrix_cells
        for c in cands:
            m = matrix_cells(c)
            rows.append([P(f"<b>{escape(c['label'])}</b>{'' if c['required'] else ' *'}", st),
                         P(f"<b>{c['scores']['cardinality']}</b> {escape(m['cardinality'])}", st),
                         P(f"<b>{c['scores']['distribution']}</b> {escape(m['distribution'])}", st),
                         P(f"<b>{c['scores']['hotspot']}</b> {escape(m['hotspot'])}", st),
                         P(f"<b>{c['scores']['targeting']}</b> {escape(m['targeting'])}", st),
                         P(f"<b>{c['total']}</b>", st), P(f"<b>{escape(c['verdict'])}</b>", st)])
        t = tbl(rows, [27 * mm, 27 * mm, 28 * mm, 27 * mm, 30 * mm, 12 * mm, W - 151 * mm])
        vstyle = []
        for i, c in enumerate(cands, 1):
            col = {"RECOMMENDED": "#dff3e8", "REFINEMENT": "#e6eefb", "AVOID": "#fbe3e5"}.get(c["verdict"], "#fff6e0")
            vstyle.append(("BACKGROUND", (-1, i), (-1, i), colors.HexColor(col)))
        t.setStyle(TableStyle(vstyle))
        w = r1["criteria"]
        e += [Paragraph("Decision matrix (measured)", st["h2"]), t,
              Paragraph(f"Scores 1-5 (5 best). Weighted score = {w['cardinality']:.0%} cardinality + "
                        f"{w['distribution']:.0%} distribution + {w['hotspot']:.0%} hot-spot + {w['targeting']:.0%} "
                        "query targeting. Rules: cardinality 5 if distinct values >= half the documents, 4 if >= 1,000, "
                        "capped at 2 when one value holds more than 5 %; distribution 5 if the busiest shard got <= 56 % "
                        "of the new orders, 4 <= 62 %, 3 <= 72 %; hot-spot Low if the latest batch put <= 65 % on one shard, "
                        "High above 90 %; targeting = 1 + 4 x (share of the workload that reached one shard). "
                        "* = extra row from the course material: category is the low-cardinality row of the attached "
                        "'Shard Key Selection' matrix; the compound key refines the compound key suggested in the course "
                        "repository. The website lets you change the weights and re-rank.", st["sub"]),
              KeepTogether([stacked_distribution(cands)])]
        e += [Paragraph("Query targeting per query (shards contacted, out of 2)", st["h2"]),
              tbl([[P("<b>Shard key</b>", st)] + [P(f"<b>{escape(q['label'])}</b> ({q['weight']:.0%})", st) for q in r1["workload"]]] +
                  [[c["label"]] + [f"{c['targeting']['queries'][q['id']]['n_shards']} - "
                                   f"{'targeted' if c['targeting']['queries'][q['id']]['targeted'] else 'broadcast'}"
                                   for q in r1["workload"]] for c in cands],
                  [50 * mm] + [(W - 50 * mm) / 4] * 4, zebra=True)]
        hc, cu, od, oi = by["hashed_customer_id"], by["customer_id"], by["order_date"], by["order_id"]
        e += [Paragraph("Assessment of each candidate", st["h2"]),
              Paragraph(f"<b>order_id</b> - very high cardinality ({oi['cardinality']['distinct']:,} values) and even "
                        f"writes ({oi['distribution']['busiest_share']:.0%} on the busiest shard), but only the order "
                        "lookup is targeted: the customer-history queries, the most frequent ones, must ask every shard "
                        "(scatter-gather). Note: our order_id is random; an auto-increment number or an ObjectId would be "
                        "monotonic and behave like order_date.", st["p"]),
              Paragraph(f"<b>customer_id (ranged)</b> - high cardinality ({cu['cardinality']['distinct']:,} customers, the "
                        f"busiest holds {cu['cardinality']['top_share']:.1%}) and good targeting for customer queries, but "
                        f"uneven: {cu['distribution']['busiest_share']:.0%} of the new orders went to "
                        f"{cu['distribution']['busiest_shard']}, rising to {cu['hotspot']['last_batch_busiest_share']:.0%} in "
                        "the last batch. Two reasons: some customers order much more than others, and new customers get "
                        "ever-larger ids, so their orders pile up in the top range.", st["p"]),
              Paragraph(f"<b>order_date</b> - cardinality is fine, but the value only grows: "
                        f"{od['distribution']['busiest_share']:.0%} of the new orders went to {od['distribution']['busiest_shard']} "
                        f"(opcounters on the shard primaries: {od['opcounters_live']}). Date-range reports are targeted, "
                        "but only because all recent data sits on one shard - the hot-spot.", st["p"]),
              Paragraph(f"<b>hashed customer_id</b> - MongoDB hashes the value, so customers (old and new) are scattered "
                        f"over the whole range: {hc['distribution']['busiest_share']:.0%} of the new orders on the busiest "
                        "shard, low hot-spot risk, and an <i>equality</i> on customer_id is still sent to exactly one shard, "
                        f"which covers {hc['targeting']['targeted_weight']:.0%} of the workload.", st["p"]),
              Paragraph("Recommendation", st["h2"]),
              Paragraph(f"<b>Shard on hashed customer_id ({escape(best['key'])})</b> - weighted score {best['total']}, the "
                        "highest of the four candidates. It is the only one that is good on all three properties at once: "
                        "high cardinality, even distribution of writes (no hot-spot, also for brand-new customers) and "
                        "targeted customer-scoped queries. Trade-offs, and how to live with them: an order lookup by "
                        "order_id alone and the store-wide date report become broadcast queries - the order page can pass "
                        "the logged-in customer_id along (then it is targeted), and reports can run on secondaries or an "
                        "analytics node; range queries on customer_id itself are never targeted, but the application does "
                        "not need them.", st["p"])]
        if r1["best_overall"] != r1["recommended"]:
            x = by[r1["best_overall"]]
            e.append(Paragraph(f"<b>Refinement:</b> {escape(x['label'])} scores {x['total']}. It routes exactly like hashed "
                               "customer_id (same hash prefix) but adds order_date, so the orders of one very busy customer "
                               "can still be divided into several chunks - with hashed customer_id alone, all orders of one "
                               "customer share one hash value and can never be split (a possible jumbo chunk).", st["p"]))
        k = next((k for k in sec if k.startswith("Lab 11.1")), None)
        if k:
            e += [Paragraph("Captured terminal output", st["h2"])] + code_block(sec[k], st)

    # ------------------------------------------------------------------ Lab 11.2
    if r2:
        a, b = r2["shards"]
        hd, ed = r2["collections"]["hotspot_data"], r2["collections"]["even_data"]
        s = r2["summary"]
        e += [PageBreak(), Paragraph("Lab 11.2 - Shard key hotspot detection", st["h1"]),
              Paragraph("<b>Setup.</b> Sharded cluster with two shards (docker-compose.yml). "
                        "<font face='Courier'>sh.enableSharding('lab11')</font>, then "
                        "<font face='Courier'>db.hotspot_data</font> sharded on <font face='Courier'>{ts: 1}</font> and "
                        "<font face='Courier'>db.even_data</font> on <font face='Courier'>{user_id: 'hashed'}</font>. A "
                        f"Python script inserts {r2['n']:,} documents into each: <font face='Courier'>ts</font> is a "
                        "monotonically increasing ISODate, <font face='Courier'>user_id</font> a new ObjectId per document. "
                        f"Phase 1: {r2['history']} documents of December 2024; for hotspot_data that range is split off at "
                        f"2025-01-01 and migrated to {b} (what the balancer does with old data). Phase 2: the "
                        f"{r2['n'] - r2['history']:,} documents of 2025 in {r2['batches']} batches, balancer stopped. "
                        "even_data also gets an index on ts so both collections can answer the same ts query the same way.",
                        st["p"])]
        rows = [["Collection", "Shard key", "Shard", "count", "size", "avgObjSize", "chunks", "share of docs"]]
        for coll, data in (("hotspot_data", hd), ("even_data", ed)):
            cs = data["collstats"]
            tot = sum(v["count"] for v in cs["shards"].values()) or 1
            chs = {}
            for ch in data["chunks"]:
                chs[ch["shard"]] = chs.get(ch["shard"], 0) + 1
            for sh, v in cs["shards"].items():
                rows.append([coll, data["key"], sh, f"{v['count']:,}", f"{v['size'] / 1024:.1f} KiB", f"{v['avg_obj']} B",
                             chs.get(sh, 0), f"{100 * v['count'] / tot:.1f} %"])
        e += [Paragraph("db.hotspot_data.stats() and db.even_data.stats() - per shard (collStats)", st["h2"]),
              tbl(rows, [24 * mm, 27 * mm, 16 * mm, 16 * mm, 22 * mm, 20 * mm, 14 * mm, W - 139 * mm], zebra=True),
              Spacer(1, 6), KeepTogether([routing_lines(r2)]),
              Paragraph(f"<b>Inserts executed by each shard primary during phase 2</b> (serverStatus opcounters.insert): "
                        f"hotspot_data {s['hotspot_phase2_inserts']}, even_data {s['even_phase2_inserts']}. In hotspot_data "
                        f"one shard did all the insert work; {b} kept only the old December data it was given.", st["p"])]
        ex = r2["explain"]
        e += [Paragraph("Range queries compared with explain()", st["h2"]),
              tbl([[P("<b>Query</b>", st), P("<b>Stage</b>", st), P("<b>Shards</b>", st), P("<b>Returned</b>", st),
                    P("<b>Examined</b>", st), P("<b>Plan on each shard</b>", st), P("<b>Avg ms</b>", st)]] +
                  [[P(f"<b>{k}</b> {escape(v['ns'].split('.')[1])}: {escape(v['why'])}<br/><font face='Courier' size='6.5'>"
                      f"{escape(v['filter'])}</font>", st), P(v["stage"], st), P(", ".join(v["shards"]), st),
                    P(f"{v['nReturned']:,}", st), P(f"{v['docsExamined']:,}", st),
                    P(escape(v["kind"]) + "; " + escape(v["per_shard"][0]["access"] if v["per_shard"] else ""), st),
                    P(str(v["avg_ms"]), st)] for k, v in ex.items()],
                  [58 * mm, 25 * mm, 20 * mm, 15 * mm, 15 * mm, W - 145 * mm, 12 * mm], zebra=True),
              Paragraph(f"H1 is the classroom query: the range on the shard key is sent to <b>one</b> shard "
                        f"({', '.join(ex['H1']['shards'])}). The same range on even_data (E1) must ask every shard and merge "
                        f"(SHARD_MERGE). A range on the hashed key (E2) is also broadcast, and because a hashed index cannot "
                        f"serve a range, every shard scans the whole collection: {ex['E2']['docsExamined']:,} documents "
                        f"examined to return {ex['E2']['nReturned']:,}. An equality on the hashed key (E3) is targeted again.",
                        st["p"])]
        e += [Paragraph("Brief report: why monotonically increasing keys cause hotspots", st["h2"]),
              Paragraph("With a ranged shard key every chunk is a contiguous range [min, max) of key values, and the last "
                        "chunk ends at MaxKey. A monotonically increasing key (timestamp, auto-increment id, ObjectId) gives "
                        "every new document a value larger than all existing ones, so every insert falls into that last "
                        f"chunk and therefore onto the one shard that owns it - here {s['hotspot_phase2_inserts']} inserts. "
                        "Adding shards does not add write capacity: the cluster can insert only as fast as one shard primary. "
                        "Splitting does not help either - the balancer can move the <i>older</i> ranges away (which is how "
                        f"{b} got its {s['hotspot_by_shard'][b]:,} documents), but the newest range stays in one place, and "
                        "the balancer must keep migrating data afterwards, which costs extra I/O. A hashed key maps "
                        "neighbouring values to unrelated points of the 64-bit hash space, so consecutive inserts land on "
                        f"different chunks: even_data finished {s['even_by_shard'][a]:,} / {s['even_by_shard'][b]:,} although "
                        "its ObjectIds also increase.", st["p"]),
              Paragraph("When a ranged shard key is still appropriate", st["h2"]),
              Paragraph("&bull; <b>Range queries on the key dominate</b> and must be targeted - time-window reports, "
                        "\"all events of a device between t1 and t2\". H1 needed one shard, E1 needed all of them.", st["p"]),
              Paragraph("&bull; <b>The values are not monotonic</b> and spread over the range (postal codes, SKUs, random "
                        "codes); with pre-split chunks the writes are then distributed.", st["p"]),
              Paragraph("&bull; <b>A compound key with a non-monotonic, high-cardinality prefix</b>, such as "
                        "<font face='Courier'>{device_id: 1, ts: 1}</font> or <font face='Courier'>{customer_id: 1, "
                        "order_date: 1}</font>: the prefix spreads the writes, and a range on ts for one device stays on "
                        "one shard.", st["p"]),
              Paragraph("&bull; <b>Zone sharding / data locality</b> - zones are defined as key ranges: recent data on "
                        "fast hardware and old ranges on cheap storage, or customers of one region on servers in that "
                        "region.", st["p"]),
              Paragraph("&bull; <b>Low insert rate</b> - if one shard can easily absorb all inserts, the hot-spot does not "
                        "matter and the targeted range queries are a pure gain.", st["p"])]
        k = next((k for k in sec if k.startswith("Lab 11.2")), None)
        if k:
            e += [Paragraph("Captured terminal output (includes db.hotspot_data / db.even_data .getShardDistribution())",
                            st["h2"])] + code_block(sec[k], st)
        else:
            e += [Paragraph("db.hotspot_data.getShardDistribution()", st["h2"])] + code_block(hd["distribution_text"], st)
            e += [Paragraph("db.even_data.getShardDistribution()", st["h2"])] + code_block(ed["distribution_text"], st)

    # ------------------------------------------------------------------ Activity: resharding
    if rs:
        sys_path_add()
        from backend.app.lab11 import RESHARD_LIMITS, RESHARD_STEPS
        bb, aa = rs["before"], rs["after"]
        wr = rs["writes"]
        e += [PageBreak(), Paragraph("Activity 11.1 - Resharding in MongoDB 5.0+ (steps and limitations)", st["h1"]),
              Paragraph("Since MongoDB 5.0 the shard key of a collection can be changed online with "
                        "<font face='Courier'>reshardCollection</font>. Instead of only reading about it, this lab ran it: "
                        f"{rs['n']:,} orders sharded on the bad key <font face='Courier'>{{order_date: 1}}</font> were "
                        "resharded to <font face='Courier'>{customer_id: 'hashed'}</font> while a writer kept inserting one "
                        "order every 20 ms, and every phase was read from <font face='Courier'>$currentOp</font> and "
                        "<font face='Courier'>config.reshardingOperations</font>.", st["p"]),
              Paragraph("Steps", st["h2"]),
              tbl([[P("<b>#</b>", st), P("<b>Step</b>", st), P("<b>What happens</b>", st)]] +
                  [[str(i), P(f"<b>{escape(t)}</b>", st), P(escape(d), st)] for i, (t, d) in enumerate(RESHARD_STEPS, 1)],
                  [8 * mm, 38 * mm, W - 46 * mm], zebra=True),
              Paragraph("Phases observed in this run", st["h2"]),
              tbl([["t (s)", "Component", "State", "Progress"]] +
                  [[f"{ev['t']:.1f}", ev["source"], ev["state"],
                    (f"{ev.get('copied')} of ~{ev.get('to_copy')} docs copied, {ev.get('oplog_applied')} oplog entries applied"
                     if ev.get("to_copy") is not None else "")] for ev in rs["timeline"]],
                  [14 * mm, 32 * mm, 38 * mm, W - 84 * mm], zebra=True),
              Spacer(1, 6),
              KeepTogether([tbl([["", "Before", "After"],
                   ["Shard key", bb["key"], aa["key"]],
                   ["Documents per shard", ", ".join(f"{k}: {v:,}" for k, v in bb["by_shard"].items()),
                    ", ".join(f"{k}: {v:,}" for k, v in aa["by_shard"].items())],
                   ["Chunks", str(len(bb["chunks"])), f"{len(aa['chunks'])} (numInitialChunks {rs.get('num_initial_chunks')})"],
                   [f"Customer query {rs['probe_customer']}", f"{bb['explain_customer']['n_shards']} shards - {bb['explain_customer']['kind']}",
                    f"{aa['explain_customer']['n_shards']} shard - {aa['explain_customer']['kind']}"]],
                  [44 * mm, (W - 44 * mm) / 2, (W - 44 * mm) / 2]), Spacer(1, 6), latency_plot(rs)]),
              Paragraph(f"reshardCollection took {rs['seconds']} s (with the lab's 5-second minimum; the default minimum "
                        f"is {(rs.get('min_duration_default_ms') or 300000) / 60000:.0f} minutes). During it the application "
                        f"wrote {wr['count']:,} orders with <b>{wr['errors']} errors</b>; median latency {wr['p50_ms']} ms, "
                        f"99th percentile {wr['p99_ms']} ms, and one stall of <b>{wr['max_ms']} ms</b> at t = {wr['max_at_s']} s "
                        "- the write-blocking critical section just before the commit, within the documented limit of "
                        "about 2 seconds.", st["p"]),
              Paragraph("Limitations", st["h2"])]
        e += [Paragraph("&bull; " + escape(x), st["p"]) for x in RESHARD_LIMITS]
        e.append(Paragraph("Sources: MongoDB Manual, \"Reshard a Collection\" and \"reshardCollection\"; server parameter "
                           "defaults read from the running 7.0 config server with getParameter.", st["sub"]))
        k = next((k for k in sec if k.startswith("Activity 11.1")), None)
        if k:
            e += [Paragraph("Captured terminal output", st["h2"])] + code_block(sec[k], st)

    bl = out.get("billing")
    if bl:
        lt, mig, ac = bl["load"], bl["migration"], bl["acid"]
        shards = list(next(iter(lt["distribution"].values())).keys())
        ct = lt["commit_types"]
        e += [PageBreak(), Paragraph("Lab 11 applied to Lab 10 - the classifier billing on the shards", st["h1"]),
              Paragraph("Mentor's update: Exercise 10 put the Lab 7 classifier behind a paywall - 3 free classifications per "
                        "user, then Rs 100 per month - built from multi-document transactions on the replica set "
                        "(database cti_billing: accounts, usage, payments, revenue). This exercise scales that system out: "
                        "the data is moved to the sharded cluster once (verified copy, then a marker document switches the "
                        "website to the shards) and every use, top-up and payment keeps running as an ACID transaction - now "
                        "a <b>distributed</b> transaction when it touches more than one shard.", st["p"]),
              Paragraph("Shard keys chosen with the Lab 11.1 method", st["h2"]),
              tbl([[P("<b>Collection</b>", st), P("<b>Shard key</b>", st), P("<b>Why</b>", st)],
                   [P("accounts", st), P(escape(bl["keys"]["accounts"]), st), P("_id is the user id: every request of a user reads and writes one account on one shard; hashing spreads new sign-ups (no monotonic hot-spot).", st)],
                   [P("usage", st), P(escape(bl["keys"]["usage"]), st), P("the compound key Lab 11.1 recommended as refinement: targeted per user, and a very active user's history can still be split by time (no jumbo chunk).", st)],
                   [P("payments", st), P(escape(bl["keys"]["payments"]), st), P("targeted per user. The idempotency index is unique on {user_id, idem}: MongoDB REFUSED a unique index on idem alone in a sharded collection (observed) - unique indexes must contain the shard key.", st)],
                   [P("revenue", st), P(escape(bl["keys"]["revenue"]), st), P("8 bucket documents (bucket = hash of the user) instead of one counter that every payment would update - a single hot document would serialise all payments and live on one shard.", st)]],
                  [24 * mm, 46 * mm, W - 70 * mm], zebra=True),
              Paragraph("Load test on the shards", st["h2"]),
              Paragraph(f"Migration: {', '.join(f'{k} {v:,}' for k, v in mig.get('moved', {}).items()) or 'already on the shards'}"
                        f"{' - verified (wallet total and revenue identical on both sides)' if mig.get('verified') else ''}. "
                        f"Then {lt['users']} users ran at the same time ({lt['threads']} threads, {lt['seconds']} s): "
                        f"{lt['ok']:,} classifications, {lt['refused']:,} refused after the free trials, {lt['subscribed']} "
                        f"subscriptions, {lt['retries']} write-conflict retries, <b>{lt['errors']} errors</b>; "
                        f"{lt['transactions']:,} transactions ({lt['txn_per_s']}/s).", st["p"]),
              tbl([["Collection"] + shards] + [[c] + [f"{d.get(x, 0):,}" for x in shards] for c, d in lt["distribution"].items()],
                  [40 * mm] + [30 * mm] * len(shards), zebra=True),
              Spacer(1, 6),
              tbl([["How mongos committed the transactions (serverStatus.transactions.commitTypes)", "Count"]] +
                  [[k, f"{v:,}"] for k, v in ct.items() if v] , [W - 30 * mm, 30 * mm], zebra=True),
              Paragraph(f"<b>Distributed transactions.</b> A use, a top-up or a payment touches the user's account, usage and "
                        f"payments - with the same hashed value these land on the same shard, so most transactions commit on "
                        f"one shard ({ct.get('singleShard', 0):,} singleShard). A subscription also updates a revenue bucket; "
                        f"when that bucket lives on the other shard, mongos runs a <b>two-phase commit</b> across both shards "
                        f"({ct.get('twoPhaseCommit', 0):,} here) - still all-or-nothing.", st["p"]),
              tbl([["Query (explain through mongos)", "Shards", "Routing"]] +
                  [[k, f"{v['n_shards']} of {v['total_shards']}", v["kind"]] for k, v in lt["targeting"].items()],
                  [80 * mm, 22 * mm, W - 102 * mm], zebra=True),
              Paragraph(f"<b>ACID across shards</b> - the Lab 10 experiments repeated on the sharded cluster: free-trial race "
                        f"{ac['free_trial_race']['granted']} of {ac['free_trial_race']['requests']} granted; crash after debit "
                        f"rolled back ({'yes' if ac['crash_after_debit']['pass'] else 'NO'}); double click charged once "
                        f"({'yes' if ac['double_click']['pass'] else 'NO'}); money conserved "
                        f"({'yes' if lt['invariants']['money_conserved'] and lt['invariants']['revenue_matches'] else 'NO'}: wallets "
                        f"Rs {lt['invariants']['wallets']:,}, revenue Rs {lt['invariants']['revenue']:,}).", st["p"])]
        k = next((k for k in sec if k.startswith("Lab 11 applied to Lab 10")), None)
        if k:
            e += [Paragraph("Captured terminal output", st["h2"])] + code_block(sec[k], st)

    e += [Paragraph("Conclusion", st["h1"]),
          Paragraph("Sharding adds capacity only if the shard key spreads the work. On a real two-shard cluster the "
                    "monotonic keys (order_date, ts) sent every new write to one shard, the low-cardinality key (category) "
                    "can never be split finely enough, and order_id spreads writes but turns the most common query "
                    "(a customer's orders) into scatter-gather. Hashed customer_id gives even writes and targeted "
                    "customer queries, which is why it is recommended; a hashed key pays for this by broadcasting range "
                    "queries. When a key turns out to be wrong, MongoDB 5.0+ can reshard online - at the cost of time, "
                    "extra storage and a short write pause. Applied to the Lab 10 classifier billing, hashed user keys keep "
                    "every user on one shard, so most transactions stay single-shard and the rest use two-phase commit.", st["p"])]
    k = next((k for k in sec if k.startswith("SUMMARY")), None)
    if k:
        e += [Paragraph("Captured terminal output - summary", st["h2"])] + code_block(sec[k], st)

    e += [PageBreak(), Paragraph("Appendix - source code (backend/app/lab11.py)", st["h1"])]
    e += code_block((ROOT / "backend/app/lab11.py").read_text(encoding="utf-8"), st)
    doc.build(e)
    return pdf


def sys_path_add():
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


if __name__ == "__main__":
    import json, os, sys
    sys_path_add()
    res = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
    print(build(json.loads((res / "lab11_results.json").read_text()), res, os.getenv("DOCKER_URL", "")))

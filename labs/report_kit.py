"""Shared PDF pieces for the Exercise 16-18 reports (same look as Exercises 12-15: helpers from labs/lab12/make_report.py)."""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("lab12_report_kit", ROOT / "labs" / "lab12" / "make_report.py")
R12 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R12)
styles, tbl, P, code_block, box, arrow = R12.styles, R12.tbl, R12.P, R12.code_block, R12._box, R12._arrow
INK, ACCENT, GREY, BLUE, GREEN, RED = R12.INK, R12.ACCENT, R12.GREY, R12.BLUE, R12.GREEN, R12.RED
STUDENT = R12.STUDENT
W = A4[0] - 32 * mm
DBC = {"mongodb": colors.HexColor("#13aa52"), "cassandra": colors.HexColor("#1287b1"), "neo4j": colors.HexColor("#4581c3")}
DBN = {"mongodb": "MongoDB", "cassandra": "Cassandra", "neo4j": "Neo4j"}
esc = escape


def yn(v):
    return "PASS" if v else "FAIL"


def lab_url(docker_url: str, n: int) -> str:
    if not docker_url:
        return ""
    for k in ("nosql-lab18", "nosql-lab17", "nosql-lab16", "nosql-lab15"):
        if k in docker_url:
            return docker_url.replace(k, f"nosql-lab{n}")
    return docker_url


def doc(pdf: Path, title: str, n: int):
    def foot(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GREY)
        canvas.drawString(16 * mm, 8 * mm, f"Exercise {n} - {title}  |  M Nagasai Girisharan  |  24BTRCL114")
        canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"page {d.page}")
        canvas.restoreState()
    d = SimpleDocTemplate(str(pdf), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
                          title=f"Exercise {n} - {title}", author="M Nagasai Girisharan")
    return d, foot


def header(st, n: int, title: str, docker_url: str, commands: str, env: dict | None = None) -> list:
    url = lab_url(docker_url, n) or "(run ./publish.sh <dockerhub-user> to fill in the Docker Hub link)"
    img = url.split("hub.docker.com/r/")[-1] if "hub.docker.com/r/" in url else f"<user>/nosql-lab{n}"
    e = [Paragraph(f"Exercise {n} - {esc(title)}", st["title"]), Paragraph(esc(STUDENT), st["sub"]),
         Paragraph("<b>Docker Hub image (submission):</b>", st["p"]),
         Paragraph(f'<link href="{esc(url)}" color="#2f6fd0"><u>{esc(url)}</u></link>', st["p"]),
         Paragraph("<b>Demonstration for grading (from the uploaded image only):</b>", st["p"])]
    e += code_block(f"docker run --rm {img} compose > docker-compose.yml\n" + commands, st)
    be = {k: v for k, v in ((env or {}).get("backends") or {}).items() if v and not str(v).startswith("unavailable")}
    if be:
        e.append(Paragraph("Measured on: " + esc(", ".join(f"{k} {v}" for k, v in be.items())) + f". Generated {datetime.now():%d %b %Y %H:%M}.", st["sub"]))
    return e


def checks(out: dict, st) -> list:
    if not out.get("checks"):
        return []
    return [Paragraph("Results at a glance", st["h1"]),
            tbl([["Check (measured)", "Result"]] + [[P(esc(c["name"]), st), yn(c["ok"])] for c in out["checks"]], [W - 20 * mm, 20 * mm],
                extra=[("TEXTCOLOR", (1, i + 1), (1, i + 1), GREEN if c["ok"] else RED) for i, c in enumerate(out["checks"])])]


def bars3(groups: list[tuple[str, dict]], title: str, w: float = 500, h: float = 170, fmt="%.1f") -> Drawing:
    """groups = [(label, {mongodb: v, cassandra: v, neo4j: v})] -> grouped vertical bars, one colour per database."""
    d = Drawing(w, h)
    d.add(String(4, h - 10, title, fontSize=8, fontName="Helvetica-Bold", fillColor=INK))
    c = VerticalBarChart()
    c.x, c.y, c.width, c.height = 40, 30, w - 60, h - 55
    keys = ["mongodb", "cassandra", "neo4j"]
    c.data = [[float(g[1].get(k) or 0) for g in groups] for k in keys]
    c.categoryAxis.categoryNames = [g[0] for g in groups]
    c.categoryAxis.labels.fontSize = 7
    c.valueAxis.labels.fontSize = 7
    c.valueAxis.valueMin = 0
    c.barLabels.fontSize = 5.5
    c.barLabelFormat = fmt
    c.barLabels.nudge = 6
    for i, k in enumerate(keys):
        c.bars[i].fillColor = DBC[k]
        c.bars[i].strokeColor = None
    d.add(c)
    for i, k in enumerate(keys):
        d.add(Rect(w - 210 + i * 70, h - 14, 8, 8, fillColor=DBC[k], strokeColor=None))
        d.add(String(w - 198 + i * 70, h - 13, DBN[k], fontSize=7))
    return d


def hbars(items: list[tuple[str, float, object]], title: str, w: float = 500, unit: str = "") -> Drawing:
    rowh = 16
    h = len(items) * rowh + 26
    d = Drawing(w, h)
    d.add(String(4, h - 10, title, fontSize=8, fontName="Helvetica-Bold", fillColor=INK))
    mx = max([v for _, v, _ in items] + [1e-9])
    for i, (lab, v, col) in enumerate(items):
        y = h - 30 - i * rowh
        d.add(String(150, y + 3, lab[:40], fontSize=6.8, textAnchor="end"))
        bw = (w - 230) * v / mx
        d.add(Rect(156, y, max(bw, 1), rowh - 5, fillColor=col or ACCENT, strokeColor=None))
        d.add(String(160 + bw, y + 3, f"{v:,.2f}{unit}", fontSize=6.8))
    return d


def curve(series: list[tuple[str, object, list[tuple[float, float]]]], title: str, w: float = 500, h: float = 210) -> Drawing:
    """series = [(name, colour, [(latency_ms, recall), ...])]"""
    d = Drawing(w, h)
    d.add(String(4, h - 10, title, fontSize=8, fontName="Helvetica-Bold", fillColor=INK))
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 44, 34, w - 170, h - 60
    lp.data = [sorted(p) for _, _, p in series if p]
    cols = [c for _, c, p in series if p]
    for i, c in enumerate(cols):
        lp.lines[i].strokeColor = c
        lp.lines[i].strokeWidth = 1.4
        lp.lines[i].symbol = makeMarker("FilledCircle", size=3.2, fillColor=c)
    lp.xValueAxis.labels.fontSize = 7
    lp.yValueAxis.labels.fontSize = 7
    allp = [q for _, _, p in series for q in p]
    if allp:
        lp.yValueAxis.valueMin = max(0, min(q[1] for q in allp) - 3)
        lp.yValueAxis.valueMax = 100
        lp.xValueAxis.valueMin = 0
    d.add(lp)
    d.add(String(44 + (w - 170) / 2, 10, "average latency (ms)", fontSize=7, textAnchor="middle"))
    d.add(String(8, 34 + (h - 60) / 2, "recall@20 %", fontSize=7))
    for i, (name, c, p) in enumerate([s for s in series if s[2]]):
        d.add(Rect(w - 116, h - 30 - i * 13, 8, 8, fillColor=c, strokeColor=None))
        d.add(String(w - 104, h - 29 - i * 13, name[:22], fontSize=7))
    return d


def build_doc(pdf: Path, title: str, n: int, story: list) -> Path:
    d, foot = doc(pdf, title, n)
    d.build(story, onFirstPage=foot, onLaterPages=foot)
    return pdf


__all__ = ["styles", "tbl", "P", "code_block", "box", "arrow", "INK", "ACCENT", "GREY", "BLUE", "GREEN", "RED", "W", "DBC", "DBN", "esc",
           "yn", "header", "checks", "bars3", "hbars", "curve", "build_doc", "Drawing", "Rect", "String", "Line", "colors", "mm"]

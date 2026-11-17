"""Writes results/Final_Assessment_Submission.pdf - the PDF with the private GitHub repository URL that is uploaded to Classroom.

    python labs/final/make_submission.py https://github.com/<user>/<repo>
"""
import os
import sys
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))


def build(url: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "Final_Assessment_Submission.pdf"
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=19, leading=24, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12.5, spaceBefore=9, spaceAfter=4)
    p = ParagraphStyle("p", parent=ss["BodyText"], fontSize=10, leading=14.5)
    small = ParagraphStyle("s", parent=p, fontSize=9, textColor=colors.HexColor("#555555"))
    mono = ParagraphStyle("m", parent=p, fontName="Courier", fontSize=9.5, leading=13.5, backColor=colors.HexColor("#f2f4f6"), borderPadding=6, spaceAfter=6)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=15 * mm, bottomMargin=13 * mm,
                            title="Final Assessment - LLM-backed chat application on MongoDB", author="M Nagasai Girisharan (24BTRCL114)")
    s = [Paragraph("Final Assessment: Building and Deploying an LLM/SLM-backed Chat Application on MongoDB", h1),
         Paragraph("23CSE528 NoSQL Database using MongoDB &nbsp;|&nbsp; School of CSE, JGi, FET Campus", small),
         Paragraph("M Nagasai Girisharan &nbsp;|&nbsp; 24BTRCL114 &nbsp;|&nbsp; 5 AIML-B &nbsp;|&nbsp; " + datetime.now().strftime("%d %B %Y"), small), Spacer(1, 10),
         Paragraph("Private GitHub repository", h2),
         Paragraph(f'<link href="{url}" color="blue"><u>{url}</u></link>', ParagraphStyle("u", parent=p, fontSize=13, leading=18)),
         Paragraph("The repository is private; the collaborator invitation for the faculty is sent at the demonstration, as the instructions ask.", small),
         Paragraph("What was built", h2),
         Paragraph("<b>Kavach Assist</b>: a chat application on top of the Kavach threat-intelligence platform of Exercises 7-18. The user picks a platform "
                   "(Linux, Windows, macOS, Android, iOS, network devices, web/server software, cloud) and asks about <b>Anticipated, Active, Evolving and "
                   "Resolved</b> threats; the answer gives the threats with the reason for their status and precise mitigation procedures. "
                   "MongoDB is the operational database, the vector store and the conversation memory.", p),
         Paragraph("How to run it", h2),
         Paragraph("git clone &lt;repository&gt; &amp;&amp; cd &lt;repository&gt;<br/>cp .env.example .env &nbsp;&nbsp;# set ADMIN_PASSWORD and JWT_SECRET (or run: bash setup_assist.sh)<br/>"
                   "docker compose up -d lab7 atlas-local ollama scanner-bot<br/>open http://localhost:8000 &nbsp;&nbsp;# sign in, then Kavach Assist", mono),
         Paragraph("Mapping to the assessment instructions", h2)]
    rows = [["Section", "Where it is implemented"],
            ["A.1 LLM", "Ollama (local SLM, llama3.2) in Docker Compose; optional OpenAI / Anthropic key; built-in writer as a fallback - backend/app/assist/llm.py"],
            ["A.2 Ingestion", "Threat catalog + playbooks + lab documents -> chunks -> embeddings -> MongoDB with a vectorSearch index - assist/knowledge.py"],
            ["A.3 /chat", "FastAPI: POST /chat and a streaming endpoint; $vectorSearch + exact queries + history -> prompt -> model - assist/engine.py, api.py"],
            ["A.4 Memory", "Collections conversations + messages (w:majority on the replica set); follow-up questions reuse the context"],
            ["A.5 Frontend", "Chat page inside the Kavach website (HTML + JavaScript): streaming answers, platform and status board, threat cards, sources"],
            ["A.6 Containers", "Dockerfile + docker-compose.yml: MongoDB replica set, Atlas Local (vector search), web, Ollama, scanner bot"],
            ["A.7 Hardening", "Secrets in .env; rate limit on /chat (atomic MongoDB counter); HTTPS on :8443; latency / error / token metrics at /metrics; /health; graceful shutdown"],
            ["Login + admin", "One administrator; users sign in with Google or email + password (scrypt hashes, signed session cookie); admin console"],
            ["Bot (optional part)", "Background scanner: vulnerable dependencies, secrets, permissions, OS updates; fixes only after a 'yes' in the chat; undo"]]
    cell = ParagraphStyle("c", parent=p, fontSize=8.8, leading=11.5)
    head = ParagraphStyle("ch", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")
    t = Table([[Paragraph(c, head if i == 0 else cell) for c in r] for i, r in enumerate(rows)], colWidths=[32 * mm, 138 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2d3a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d1d8")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]), ("TOPPADDING", (0, 0), (-1, -1), 3),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    s += [t, Spacer(1, 8), Paragraph("Full documentation: README.md in the repository (section 'Final assessment: Kavach Assist').", small)]
    doc.build(s)
    return path


if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].startswith("http"):
        sys.exit("usage: python labs/final/make_submission.py https://github.com/<user>/<repo>")
    print(f"written: {build(sys.argv[1].rstrip('/').removesuffix('.git'))}")

"""Builds results/Exercise-12_Report.pdf - the file to submit (contains the Docker Hub URL on page 1).

    python labs/lab12/make_report.py            # uses results/lab12_results.json + results/console_12.txt
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent.parent
INK, ACCENT, GREY = colors.HexColor("#13212c"), colors.HexColor("#b87700"), colors.HexColor("#56687a")
BLUE, GREEN, RED = colors.HexColor("#2f6fd0"), colors.HexColor("#178a58"), colors.HexColor("#d92638")
STUDENT = "M NAGASAI GIRISHARAN  |  24BTRCL114  |  5 AIML-B  |  23CSE528 NoSQL Database using MongoDB"


def styles():
    s = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=s["Title"], fontSize=21, textColor=INK, alignment=0, spaceAfter=4),
        "sub": ParagraphStyle("s", parent=s["Normal"], fontSize=10, textColor=GREY, spaceAfter=10),
        "h1": ParagraphStyle("h1", parent=s["Heading1"], fontSize=15, textColor=INK, spaceBefore=14, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=s["Heading2"], fontSize=11.5, textColor=ACCENT, spaceBefore=10, spaceAfter=3),
        "p": ParagraphStyle("p", parent=s["BodyText"], fontSize=10, leading=14.5, spaceAfter=5),
        "cell": ParagraphStyle("cell", parent=s["BodyText"], fontSize=7.6, leading=9.4),
        "hcell": ParagraphStyle("hcell", parent=s["BodyText"], fontSize=7, leading=8.4, textColor=colors.white,
                                fontName="Helvetica-Bold"),
        "code": ParagraphStyle("c", fontName="Courier", fontSize=6.7, leading=8.2, backColor=colors.HexColor("#f2f5f7"),
                               borderPadding=4, leftIndent=2),
    }


def sections(console: str) -> dict:
    chunks, parts = {}, re.split(r"\n={76}\n(.+)\n={76}\n", console)
    for i in range(1, len(parts) - 1, 2):
        chunks[parts[i].strip()] = parts[i + 1].rstrip()
    return chunks


def code_block(text: str, st, width=124):
    lines = []
    for ln in text.splitlines():
        while len(ln) > width:
            lines.append(ln[:width]); ln = "   " + ln[width:]
        lines.append(ln)
    return [Preformatted("\n".join(lines[i:i + 78]), st["code"]) for i in range(0, len(lines), 78)]


def tbl(data, widths=None, head=True, zebra=True, extra=None):
    t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
    style = [("FONTSIZE", (0, 0), (-1, -1), 7.8), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c3ced7")),
             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if head:
        style += [("BACKGROUND", (0, 0), (-1, 0), INK), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    if zebra:
        style += [("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f7f9")])]
    t.setStyle(TableStyle(style + (extra or [])))
    return t


def P(text, st, key="cell"):
    return Paragraph(text, st[key])


def mono(x):
    return f"<font face='Courier'>{escape(str(x))}</font>"


# ----------------------------------------------------------------------------- drawings
def _box(d, x, y, w, h, lines, fill="#ffffff", stroke=INK, size=7.4, color=INK, bold_first=True):
    d.add(Rect(x, y, w, h, rx=3, ry=3, fillColor=colors.HexColor(fill), strokeColor=stroke, strokeWidth=0.9))
    n = len(lines)
    for i, ln in enumerate(lines):
        d.add(String(x + w / 2, y + h / 2 + (n - 1) * 4.6 - i * 9.2 - 2.5, ln, textAnchor="middle", fontSize=size,
                     fontName="Helvetica-Bold" if (i == 0 and bold_first) else "Helvetica", fillColor=color))


def _arrow(d, x1, y1, x2, y2, color=GREY):
    import math
    d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=0.8))
    a = math.atan2(y2 - y1, x2 - x1)
    d.add(Polygon([x2, y2, x2 - 5 * math.cos(a - 0.4), y2 - 5 * math.sin(a - 0.4), x2 - 5 * math.cos(a + 0.4),
                   y2 - 5 * math.sin(a + 0.4)], fillColor=color, strokeColor=color))


def threat_drawing(tm: dict):
    """The mentor's Threat Modelling picture, coloured with the measured result of every control."""
    d = Drawing(500, 330)
    d.add(Rect(4, 262, 492, 62, fillColor=colors.HexColor("#f7e8b0"), strokeColor=None))
    d.add(String(250, 314, "Attack surface", textAnchor="middle", fontSize=8, fillColor=INK))
    th = tm.get("threats", [])
    xs = [10, 132, 254, 376]
    for x, t in zip(xs, th):
        _box(d, x, 270, 114, 36, [t["threat"], t["cause"]], fill="#eceafc" if t["mitigated"] else "#fde2e4")
    d.add(Rect(4, 160, 492, 76, fillColor=colors.HexColor("#dff3e5"), strokeColor=None))
    d.add(String(250, 226, "MongoDB security controls (green = verified on the running server)", textAnchor="middle", fontSize=8, fillColor=INK))
    ctr = tm.get("controls", [])
    w = 92
    for i, c in enumerate(ctr):
        x = 10 + (i % 5) * 97
        y = 192 if i < 5 else 166
        fill = "#c9ecd5" if c["ok"] else ("#eef1f4" if c["ok"] is None else "#fde2e4")
        words = c["control"].split(" (")[0]
        _box(d, x, y, w, 22, [words[:24], words[24:48]] if len(words) > 24 else [words], fill=fill, size=6.6, bold_first=False)
    for x in xs:
        _arrow(d, x + 57, 270, x + 57, 236)
        d.add(String(x + 60, 248, "prevents", fontSize=6.3, fillColor=GREY))
    d.add(Rect(120, 4, 260, 140, fillColor=colors.HexColor("#fffbe0"), strokeColor=colors.HexColor("#c8c08a")))
    d.add(String(250, 134, "CIA triad defence layers", textAnchor="middle", fontSize=8, fillColor=INK))
    for i, (k, v) in enumerate(tm.get("cia", {}).items()):
        _box(d, 135, 90 - i * 42, 230, 36, [k, ", ".join(v)], fill="#24486b", stroke=colors.HexColor("#24486b"),
             color=colors.white, size=6.8)
    _arrow(d, 250, 160, 250, 144)
    return d


def architecture():
    d = Drawing(500, 170)
    _box(d, 6, 110, 120, 44, ["Kavach website", "analyst_app (read only)"], fill="#fff4dc")
    _box(d, 6, 60, 120, 44, ["Auto-updater (Lab 7)", "ingest_app (find/insert/update)"], fill="#fff4dc")
    _box(d, 6, 10, 120, 44, ["Classifier billing (Lab 10)", "finance_app + CSFLE key"], fill="#fff4dc")
    _box(d, 196, 40, 130, 90, ["mongo-secure :27040", "replica set rs12", "--auth (keyFile, SCRAM-SHA-256)",
                               "--tlsMode requireTLS", "client certificates required", "profiler + change streams"], fill="#eef3fb")
    _box(d, 380, 110, 114, 44, ["cti_secure", "reports, indicators, ...", ], fill="#f6f8fa")
    _box(d, 380, 60, 114, 44, ["kavach_billing", "accounts, payments, view"], fill="#f6f8fa")
    _box(d, 380, 10, 114, 44, ["lab12_company", "employees + views"], fill="#f6f8fa")
    for y in (132, 82, 32):
        _arrow(d, 126, y, 196, 85, BLUE)
        _arrow(d, 326, 85, 380, y)
    d.add(String(160, 140, "TLS 1.3 + client cert", fontSize=6.5, fillColor=BLUE))
    return d


# ----------------------------------------------------------------------------- the report
def build(out: dict, results: Path, docker_url: str) -> Path:
    st = styles()
    console = (results / "console_12.txt").read_text(encoding="utf-8") if (results / "console_12.txt").exists() else ""
    sec = sections(console)
    pdf = results / "Exercise-12_Report.pdf"
    doc = SimpleDocTemplate(str(pdf), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=15 * mm,
                            bottomMargin=15 * mm, title="Exercise 12 - Database Security & the CIA Triad",
                            author="M Nagasai Girisharan")
    W = A4[0] - 32 * mm
    env = out.get("environment", {})
    e = [Paragraph("Exercise 12 - Database Security &amp; the CIA Triad", st["title"]), Paragraph(escape(STUDENT), st["sub"])]
    url = docker_url or "(set DOCKER_URL / run ./publish.sh to fill in the Docker Hub link)"
    e += [Paragraph("<b>Docker Hub image (submission):</b>", st["p"]),
          Paragraph(f'<link href="{escape(url)}" color="#2f6fd0"><u>{escape(url)}</u></link>', st["p"]),
          Paragraph("<b>Demonstration for grading (from the uploaded image only):</b>", st["p"])]
    img = url.split("hub.docker.com/r/")[-1] if "hub.docker.com/r/" in url else "<user>/nosql-lab13"
    e += code_block(f"docker run --rm {img} compose > docker-compose.yml\n"
                    f"docker compose up -d lab12          # Lab 7 -> 8 -> 9 -> 10 -> 11 -> 12 + mongo-secure (auth + TLS)\n"
                    f"open http://localhost:8000/#/lab12   # website; or: docker compose exec lab12 lab12 all\n"
                    f"docker compose exec lab12 lab12 shell analyst_app   # mongosh over TLS as a least-privilege user", st)
    e.append(Paragraph(f"Generated {datetime.now():%d %b %Y %H:%M}. MongoDB {env.get('mongodb', '?')} secure server "
                       f"{escape(str(env.get('host', 'mongo-secure:27040')))} (replica set {env.get('set', 'rs12')}, tlsMode "
                       f"{env.get('tls_mode', '?')}, client certificates required: {env.get('client_certs_required')}, "
                       f"authentication mechanisms: {escape(str(env.get('mechanisms')))}). PyMongo {env.get('pymongo', '?')}.", st["sub"]))
    if out.get("checks"):
        e += [Paragraph("Results at a glance", st["h1"]),
              tbl([["Check (measured on the running secure server)", "Result"]] +
                  [[P(escape(c["name"]), st), "PASS" if c["ok"] else "FAIL"] for c in out["checks"]], [W - 20 * mm, 20 * mm])]

    e += [Paragraph("The secure deployment", st["h1"]),
          Paragraph("Lab 12 adds one hardened MongoDB, <b>mongo-secure</b>, next to the Lab 7-11 servers. It is a one-member "
                    "replica set (so change streams and transactions work) started with <b>--keyFile</b> (which turns on "
                    "authorization), <b>--tlsMode requireTLS</b>, a CA file (so every client must present a certificate signed "
                    "by the Kavach CA) and <b>authenticationMechanisms=SCRAM-SHA-256</b>. The very first administrator is created "
                    "by the bootstrap script while mongod listens only on 127.0.0.1 without TLS - the <i>localhost exception</i> - "
                    "and the server is then restarted in secure mode. Every Kavach service gets its own least-privilege account.",
                    st["p"]), architecture()]

    tm = out.get("threat_model")
    if tm:
        e += [Paragraph("Threat model (the mentor's diagram, with measured evidence)", st["h1"]), threat_drawing(tm),
              Spacer(1, 4),
              tbl([["Control", "CIA", "Status", "Evidence measured on the server"]] +
                  [[P(escape(c["control"]), st), c["cia"], "n/a" if c["ok"] is None else ("PASS" if c["ok"] else "FAIL"),
                    P(escape(c["evidence"]), st)] for c in tm["controls"]], [40 * mm, 24 * mm, 13 * mm, W - 77 * mm])]

    # ------------------------------------------------------------------ 12.1
    r = out.get("lab_12_1")
    if r:
        e += [PageBreak(), Paragraph("Lab 12.1 - Configure authentication and RBAC", st["h1"]),
              Paragraph("<b>Objective.</b> Set up authentication and role-based access control. <b>Steps.</b> (1) authentication "
                        "is enforced (keyFile implies --auth); (2) an admin and application users with different roles were "
                        "created; (3) each user was tested against every operation.", st["p"]),
              Paragraph("The classroom snippet, run exactly as given", st["h2"])]
        e += code_block(r["snippet"].get("source", ""), st)
        e.append(Paragraph(f"Result: <b>{escape(r['snippet']['result'])}</b> - {escape(r['snippet']['detail'])}. "
                           "<b>aggregate</b> is a command, not a privilege action: an aggregation is authorised by "
                           "<b>find</b> on the collections it reads. The same happens with the solution text's "
                           f"'find/insert/update/delete': <i>{escape(r['delete_action']['detail'][:60])}</i> - the action is "
                           "called <b>remove</b>. The role is therefore created with <b>find</b> (+ listCollections). Because the "
                           "user references the role with db 'admin', the role is created in the admin database.", st["p"]))
        e += [Paragraph("Roles created (createRole)", st["h2"]),
              tbl([["Role", "Privileges", "Why"]] +
                  [[P(mono(x["role"]), st), P("<br/>".join(f"{escape(p['resource']['db'])}.{escape(p['resource']['collection'] or '*')}: "
                                                    f"{escape(', '.join(p['actions']))}" for p in x["privileges"]), st),
                    P(escape(x["note"]), st)] for x in r["roles"]], [28 * mm, 70 * mm, W - 98 * mm]),
              Paragraph("Users (createUser, SCRAM-SHA-256, random 24-character passwords)", st["h2"]),
              tbl([["User", "Role", "Purpose"]] + [[P(mono(u["user"]), st), P(escape(", ".join(x["role"] for x in u["roles"])), st),
                                                    P(escape(u["purpose"]), st)] for u in r["users"]], [30 * mm, 36 * mm, W - 66 * mm])]
        e += matrix_table(r["matrix"], st, W, "Verification matrix (A = allowed, x = 'not authorized', code 13)")
        e.append(Paragraph("<b>Finding.</b> sec_admin (userAdminAnyDatabase) reads no data, but it may create users with any "
                           "role - including root - so it is as sensitive as root and must be protected like it.", st["p"]))

    red = out.get("redaction")
    if red:
        e += [Paragraph("Activity 12.1 - Field-level redaction with a view", st["h1"]),
              Paragraph("<font face='Courier'>db.createView('employees_public', 'employees', [{ $project: { salary: 0 } }])</font>. "
                        "Role <b>hrManager</b> has find on employees; role <b>staffReader</b> has find on the view only. A view runs "
                        "with the privileges of the view, so staff need no privilege on the underlying collection.", st["p"]),
              tbl([["Test (staff_user unless stated)", "Result"]] + [[P(escape(k), st), "PASS" if v else "FAIL"] for k, v in red["checks"].items()],
                  [W - 20 * mm, 20 * mm]),
              Spacer(1, 4),
              tbl([["Who", "Fields returned for employee 1"],
                   ["hr_user (collection)", P(escape(", ".join(red["fields_hr"])), st)],
                   ["staff_user (view)", P(escape(", ".join(red["fields_staff"])), st)]], [36 * mm, W - 36 * mm]),
              Paragraph(f"<b>Measured weakness.</b> HR added a new field <i>bank_account</i>. The exclusion view "
                        f"(salary: 0) now shows it to staff: <b>{red['leak']['exclusion_leaks']}</b>. The allow-list view "
                        f"<i>employees_directory</i> (only named fields) does not: <b>{not red['leak']['allowlist_leaks']}</b>. "
                        "Redaction views should therefore list the fields that may be shown, not the ones to hide. Kavach's "
                        "customer view is written that way.", st["p"])]

    t = out.get("lab_12_2")
    if t:
        hs = t["handshake"]
        e += [PageBreak(), Paragraph("Lab 12.2 - TLS / encryption in transit", st["h1"]),
              Paragraph("Certificates were generated by <font face='Courier'>labs/lab12/setup/certs.py</font> with the Python "
                        "<i>cryptography</i> library - the automation asked for in the solution - and the same chain can be made "
                        "with <font face='Courier'>labs/lab12/openssl_certs.sh</font> (genrsa / req -x509 / req -new / x509 -req "
                        "with subjectAltName). mongod: <font face='Courier'>--tlsMode requireTLS --tlsCertificateKeyFile server.pem "
                        "--tlsCAFile ca.crt</font>. PyMongo: <font face='Courier'>tls=true&amp;tlsCAFile=ca.crt&amp;"
                        "tlsCertificateKeyFile=client.pem</font>.", st["p"]),
              tbl([["Connection attempt", "Expected", "Result", "What the client saw"]] +
                  [[P(escape(x["test"]), st), x["expected"], x["result"].upper(), P(escape(str(x["detail"])[:110]), st)]
                   for x in t["tests"]], [62 * mm, 18 * mm, 20 * mm, W - 100 * mm],
                  extra=[("TEXTCOLOR", (2, i + 1), (2, i + 1), GREEN if x["ok"] else RED) for i, x in enumerate(t["tests"])]),
              Paragraph(f"Handshake (like <i>openssl s_client</i>): <b>{escape(hs['protocol'])}</b>, cipher "
                        f"<b>{escape(hs['cipher'])}</b> ({hs['bits']}-bit), host name checked against the SAN. Server counters "
                        f"(serverStatus.transportSecurity): {escape(json.dumps(t.get('transport_security', {})))} - every "
                        "connection used TLS 1.3; TLS 1.0 is disabled by mongod.", st["p"]),
              Paragraph("Certificate chain", st["h2"]),
              tbl([["File", "Subject (CN)", "Issuer", "Key / signature", "CA", "Valid until", "SAN"]] +
                  [[c["file"], P(escape(c["cn"]), st), P(escape(c["issuer_cn"]), st), P(f"RSA {c['key_bits']}<br/>{escape(c['signature'])}", st),
                    "yes" if c["is_ca"] else "no", c["not_after"][:10], P(escape(", ".join(c["san"]) or "-"), st)]
                   for c in t["chain"]["certs"]], [22 * mm, 24 * mm, 27 * mm, 26 * mm, 9 * mm, 18 * mm, W - 126 * mm]),
              Spacer(1, 4),
              tbl([["Leaf certificate", "Issuer = Kavach CA", "Signature verifies with CA key", "Trusted"]] +
                  [[v["cert"], str(v["issuer_matches_ca"]), str(v["signature_by_ca"]), "yes" if v["trusted"] else "NO (rejected)"]
                   for v in t["chain"]["verify"]], [40 * mm, 36 * mm, 50 * mm, W - 126 * mm]),
              Paragraph("Chain: <b>Kavach-MongoDB-CA</b> (self-signed root, CA:TRUE, pathlen 0) signs <b>mongo-secure</b> "
                        "(serverAuth, SAN mongo-secure / localhost / 127.0.0.1) and <b>kavach-app</b> (clientAuth). The server "
                        "trusts only this CA, so the certificate of the Rogue-Attacker-CA is refused during the handshake even "
                        "though its subject is identical.", st["p"])]

    k = out.get("kavach")
    if k:
        e += [PageBreak(), Paragraph("Lab 12 applied to Lab 7 - Kavach CTI on the secure server", st["h1"]),
              Paragraph("The mentor's rule applies Lab 12 to Lab 7: the threat-intelligence platform's data was copied to "
                        "mongo-secure and each part of the platform got a service account with only what it needs. The Lab 7 "
                        "auto-updater pushes its fresh reports to the secure server as <b>ingest_app</b> after every 5-minute "
                        "cycle, and the website's Security lab page reads through <b>analyst_app</b>.", st["p"]),
              tbl([["Collection copied", "Documents", "Source total", "Id checksum"]] +
                  [[c["collection"], f"{c['copied']:,}", f"{c['source_total']:,}", "matches" if c["verified"] else "DIFFERS"]
                   for c in k["copies"]], [50 * mm, 30 * mm, 30 * mm, W - 110 * mm]),
              Paragraph(f"Billing (Lab 10 paywall, read from {escape(k['billing']['source'])}): {k['billing']['accounts']} accounts "
                        f"({k['billing']['real_accounts']} real), {k['billing']['payments']} payments, "
                        f"{k['billing']['encrypted_upi']} UPI ids encrypted client-side.", st["p"]),
              tbl([["Role", "Service", "Privileges"]] +
                  [[P(mono(x["role"]), st), P(escape(x["service"]), st),
                    P("<br/>".join(f"{escape(p['resource']['db'])}.{escape(p['resource']['collection'])}: {escape(', '.join(p['actions']))}"
                                   for p in x["privileges"]), st)] for x in k["roles"]], [28 * mm, 50 * mm, W - 78 * mm])]
        e += matrix_table(k["matrix"], st, W, "Kavach permission matrix")
        ig, ip, f, a = k["integrity"], k["ip_weak"], k["csfle"], k["audit"]
        e += [Paragraph("Integrity - schema validation", st["h2"]),
              tbl([["Write (by a user who IS allowed to write)", "Result"]] +
                  [[k2.replace("_", " "), f"{v['result']}" + (f" (code {v.get('code')})" if v.get("code") else "")]
                   for k2, v in ig.items() if isinstance(v, dict)], [W - 50 * mm, 50 * mm]),
              Paragraph("Authentication hardening", st["h2"]),
              tbl([["Attempt", "Result"]] +
                  [[f"default password {w['user']}/{w['password']}", w["result"]] for w in ip["weak_passwords"]] +
                  [["SCRAM-SHA-1 log-in (mechanism disabled)", ip["scram_sha1"]["result"]],
                   ["no credentials: listDatabases", ip["anonymous"]["result"]],
                   ["ip_locked (clientSource 10.99.99.99/32)", ip["ip_locked"]["result"]],
                   ["finance_app (clientSource private networks)", ip["finance_from_private_net"]["result"]]], [W - 50 * mm, 50 * mm])]
        if f.get("available"):
            e += [Paragraph("Confidentiality against an over-privileged insider - CSFLE", st["h2"]),
                  Paragraph(f"The UPI id of every top-up is encrypted <b>in the application</b> with "
                            f"{escape(f['algorithm'])} using a data key from {mono(f['key_vault'])}, wrapped by a 96-byte local "
                            f"master key that never reaches the server. kavach_admin (root) reads: {mono(f['root_sees'][:70])}. "
                            f"finance_app with the key reads: <b>{escape(f['finance_decrypts'])}</b>. A server-side search for the "
                            f"plaintext finds {f['query_by_plaintext']} documents. (Encrypted storage engine at rest is an "
                            "Enterprise feature; field-level encryption works in Community.)", st["p"])]
        if a.get("available"):
            ev = a.get("change_event") or {}
            e += [Paragraph("Accountability - change stream + profiler", st["h2"]),
                  Paragraph(f"Change stream with pre-images on kavach_billing.accounts: <b>{escape(str(ev.get('operation')))}</b> of "
                            f"{mono(ev.get('document'))}, wallet {ev.get('wallet_before')} -> {ev.get('wallet_after')}. The "
                            "profiler (level 2) on kavach_billing stores the authenticated user of every operation "
                            "(auditLog is an Enterprise feature):", st["p"]),
                  tbl([["Time", "User", "Op", "Namespace", "Detail"]] +
                      [[r2["at"][11:19], r2["user"], r2["op"], P(escape(r2["ns"]), st), P(escape(r2["detail"][:90]), st)]
                       for r2 in a["profile"][:10]], [16 * mm, 32 * mm, 14 * mm, 42 * mm, W - 104 * mm])]

    e += [PageBreak(), Paragraph("Notes on correctness (observed on MongoDB 7.0)", st["h1"])]
    for n in NOTES:
        e.append(Paragraph("&bull; " + n, st["p"]))

    if sec:
        e += [PageBreak(), Paragraph("Console output (results/console_12.txt)", st["h1"])]
        for name, body in sec.items():
            e += [Paragraph(escape(name), st["h2"])] + code_block(body, st)
    doc.build(e, onFirstPage=_foot, onLaterPages=_foot)
    return pdf


NOTES = [
    "<b>actions: ['find', 'aggregate']</b> (classroom snippet) is refused with BadValue: 'aggregate' is not a privilege action; "
    "aggregation needs <b>find</b>. 'delete' is not an action either - it is <b>remove</b>.",
    "db.createRole() creates the role in the <i>current</i> database. The snippet's user refers to it with db 'admin', so it "
    "must be run after <font face='Courier'>use admin</font>.",
    "With <b>--tlsCAFile</b> and without <font face='Courier'>--tlsAllowConnectionsWithoutCertificates</font>, mongod requires "
    "a client certificate; a TLS connection without one is closed during the handshake (test 3).",
    "mongod connects to itself over TLS through its replica-set member name (index builds wait on it), so the member host "
    "<b>mongo-secure</b> must be in the server certificate's SAN - otherwise createIndexes hangs (observed while testing).",
    "The legacy <font face='Courier'>mongo</font> shell was removed in MongoDB 6.0; the plain-connection test uses mongosh / PyMongo.",
    "The server certificate needs a <b>subjectAltName</b>: PyMongo checks the host name against the SAN, not the CN.",
    "--keyFile implies --auth. The first user is created through the localhost exception before the server is exposed.",
    "A view used for redaction should be an allow-list ($project with the fields to show): an exclusion view leaks fields "
    "added later (measured).",
    "userAdminAnyDatabase cannot read data but can grant itself root - it is as sensitive as root.",
    "The Community edition has no auditLog and no encrypted storage engine; the lab uses the profiler + change streams and "
    "Client-Side Field Level Encryption instead. Refused (unauthorized) attempts are not written to the profiler.",
    "Availability: Kavach itself stays on the 3-member replica set rs0 (Labs 8-9, automatic failover); rs12 has one member "
    "because it is only the security test bed.",
]


def matrix_table(m, st, W, title):
    ops = m["ops"]
    head = [P("User", st, "hcell")] + [P(escape(o), st, "hcell") for o in ops]
    rows, extra = [head], []
    for i, r in enumerate(m["rows"], 1):
        row = [mono(r["user"])]
        for j, o in enumerate(ops, 1):
            c = r["cells"][o]
            row.append("A" if c["result"] == "allowed" else "x")
            extra.append(("TEXTCOLOR", (j, i), (j, i), (GREEN if c["result"] == "allowed" else RED) if c["ok"] else colors.magenta))
        rows.append([P(row[0], st)] + row[1:])
    cw = (W - 28 * mm) / len(ops)
    extra += [("ALIGN", (1, 1), (-1, -1), "CENTER"), ("FONTNAME", (1, 1), (-1, -1), "Helvetica-Bold")]
    return [Paragraph(escape(title), st["h2"]),
            tbl(rows, [28 * mm] + [cw] * len(ops), extra=extra),
            Paragraph(f"Every cell compared with the expected least-privilege result: <b>{'all as expected' if m['pass'] else 'MISMATCH'}</b>.",
                      st["p"])]


def _foot(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GREY)
    canvas.drawString(16 * mm, 8 * mm, "Exercise 12 - Database Security & the CIA Triad  |  M Nagasai Girisharan  |  24BTRCL114")
    canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"page {doc.page}")
    canvas.restoreState()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    res = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
    data = json.loads((res / "lab12_results.json").read_text())
    print(build(data, res, os.getenv("DOCKER_URL", "")))

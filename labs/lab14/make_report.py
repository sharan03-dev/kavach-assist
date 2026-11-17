"""Builds results/Exercise-14_Report.pdf - the file to submit (contains the Docker Hub URL on page 1).

    python labs/lab14/make_report.py            # uses results/lab14_results.json + results/metrics.log
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location("lab12_report", ROOT / "labs" / "lab12" / "make_report.py")
R12 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R12)                      # shared look with Exercises 12 and 13
styles, tbl, P, mono, code_block, _box, _arrow = R12.styles, R12.tbl, R12.P, R12.mono, R12.code_block, R12._box, R12._arrow
INK, ACCENT, GREY, BLUE, GREEN, RED = R12.INK, R12.ACCENT, R12.GREY, R12.BLUE, R12.GREEN, R12.RED
STUDENT = R12.STUDENT


def yn(v):
    return "PASS" if v else "FAIL"


def pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def s2(x):
    return "-" if x is None else f"{x:.2f} s"


# ----------------------------------------------------------------------------- drawings
def architecture():
    d = Drawing(500, 196)
    d.add(Rect(2, 100, 496, 94, fillColor=colors.HexColor("#eef3fb"), strokeColor=None))
    d.add(String(8, 184, "Lab 13 production replica set rs0 (keyFile) - watched and backed up by Lab 14", fontSize=7.5,
                 fillColor=INK, fontName="Helvetica-Bold"))
    for i in range(3):
        _box(d, 10 + i * 78, 138, 70, 34, [f"prod-mongo-{i + 1}", "serverStatus", "replSetGetStatus"], fill="#ffffff", size=6.2)
    _box(d, 250, 138, 112, 34, ["lab14 ops daemon (schedule)", "every 30 s: monitor_app", "every hour: backup_app"],
         fill="#fff4dc", size=6.2)
    _box(d, 374, 138, 118, 34, ["results/metrics.log", "WARNING lines (cache < 80 %,", "lag > 10 s)"], fill="#dff3e5", size=6.2)
    _box(d, 250, 104, 112, 28, ["webhook: requests.post", "Slack + Discord format"], fill="#dff3e5", size=6.2)
    _box(d, 374, 104, 118, 28, ["#mongodb-alerts (website)", "or a real Slack / Discord"], fill="#ffffff", size=6.2)
    _box(d, 10, 104, 226, 28, ["volume lab14-backups: backup-YYYYmmddTHHMMSSZ folders", "mongodump --gzip, manifest.json, 7-day retention"],
         fill="#ffffff", size=6.2)
    _arrow(d, 240, 155, 250, 155, BLUE)
    _arrow(d, 362, 155, 374, 155, BLUE)
    _arrow(d, 362, 118, 374, 118, BLUE)
    d.add(Rect(2, 4, 496, 88, fillColor=colors.HexColor("#fbf7ea"), strokeColor=None))
    d.add(String(8, 82, "Lab 14.2 and Lab 14 applied to Lab 13 + Lab 10 (mentor's rule)", fontSize=7.5, fillColor=INK,
                 fontName="Helvetica-Bold"))
    _box(d, 8, 30, 118, 42, ["Restore verification", "1,000 docs: backup, modify,", "mongorestore, assert"], fill="#ffffff", size=6.2)
    _box(d, 132, 30, 118, 42, ["Point-in-time restore", "base dump + oplog slice,", "--oplogLimit = incident"], fill="#ffffff", size=6.2)
    _box(d, 256, 30, 112, 42, ["Benchmark", "1k / 10k / 100k docs", "RPO + RTO"], fill="#ffffff", size=6.2)
    _box(d, 374, 30, 118, 42, ["Billing ledger (Lab 10)", "attacked on prod rs0,", "recovered to the second"], fill="#ffffff", size=6.2)
    d.add(String(8, 12, "Chain: Lab 7 -> 8 -> 9 -> 10 -> 11 -> 12 -> 13 -> 14   (docker compose up -d lab14)", fontSize=7,
                 fillColor=GREY, fontName="Helvetica"))
    return d


def lag_chart(r):
    s = r.get("series") or []
    d = Drawing(500, 130)
    if not s:
        return d
    W, H, L, B = 450, 90, 34, 22
    tmax = max(x["t"] for x in s) or 1
    vmax = max(max((v or 0) for v in x["lag"].values()) for x in s) * 1.15 or 1
    d.add(String(L, B + H + 8, f"Replication lag during the drill (fsyncLock on {r['target']})", fontSize=7.5, fillColor=INK,
                 fontName="Helvetica-Bold"))
    y10 = B + H * r.get("threshold_s", 10) / vmax
    d.add(Line(L, y10, L + W, y10, strokeColor=RED, strokeWidth=0.8, strokeDashArray=[3, 2]))
    d.add(String(L + W, y10 + 2, f"{r.get('threshold_s', 10):g} s threshold", fontSize=6, fillColor=RED, textAnchor="end"))
    for i, m in enumerate(s[0]["lag"]):
        pts = []
        for x in s:
            if x["lag"].get(m) is not None:
                pts += [L + W * x["t"] / tmax, B + H * x["lag"][m] / vmax]
        col = RED if m == r["target"] else [BLUE, GREEN, GREY][i % 3]
        if len(pts) >= 4:
            d.add(PolyLine(pts, strokeColor=col, strokeWidth=1.4))
        d.add(String(L + 4 + i * 150, B - 18, f"- {m}{' (locked)' if m == r['target'] else ''}", fontSize=6.2, fillColor=col))
    d.add(Line(L, B, L + W, B, strokeColor=GREY, strokeWidth=0.5))
    for f in (0, 0.5, 1):
        d.add(String(L - 4, B + H * f - 2, f"{vmax * f:.0f}s", fontSize=6, fillColor=GREY, textAnchor="end"))
    d.add(String(L + W, B - 8, f"{tmax:.0f} s", fontSize=6, fillColor=GREY, textAnchor="end"))
    return d


def bars(items, title, unit="s"):
    d = Drawing(500, 22 + 16 * len(items))
    mx = max([v for _, v, _ in items] + [1e-9])
    d.add(String(0, 10 + 16 * len(items), title, fontSize=7.5, fillColor=INK, fontName="Helvetica-Bold"))
    for i, (label, v, col) in enumerate(items):
        y = 4 + 16 * (len(items) - 1 - i)
        d.add(String(118, y + 3, label, fontSize=6.6, fillColor=INK, textAnchor="end"))
        w = 300 * v / mx
        d.add(Rect(124, y, max(w, 1), 10, fillColor=col, strokeColor=None))
        d.add(String(128 + w, y + 3, f"{v:.2f} {unit}" if unit == "s" else f"{v:.1f}{unit}", fontSize=6.4, fillColor=GREY))
    return d


# ----------------------------------------------------------------------------- the report
def build(out: dict, results: Path, docker_url: str) -> Path:
    st = styles()
    pdf = results / "Exercise-14_Report.pdf"
    doc = SimpleDocTemplate(str(pdf), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=15 * mm,
                            bottomMargin=15 * mm, title="Exercise 14 - Managing Database Services in Production",
                            author="M Nagasai Girisharan")
    W = A4[0] - 32 * mm
    e = [Paragraph("Exercise 14 - Managing Database Services in Production", st["title"]), Paragraph(escape(STUDENT), st["sub"])]
    url = docker_url or "(set DOCKER_URL / run ./publish.sh to fill in the Docker Hub link)"
    e += [Paragraph("<b>Docker Hub image (submission):</b>", st["p"]),
          Paragraph(f'<link href="{escape(url)}" color="#2f6fd0"><u>{escape(url)}</u></link>', st["p"]),
          Paragraph("<b>Demonstration for grading (from the uploaded image only):</b>", st["p"])]
    img = url.split("hub.docker.com/r/")[-1] if "hub.docker.com/r/" in url else "<user>/nosql-lab14"
    e += code_block(f"docker run --rm {img} compose > docker-compose.yml\n"
                    f"docker compose up -d lab14              # Lab 7 -> 8 -> ... -> 13 -> 14 (+ the Lab 14 ops daemon)\n"
                    f"open http://localhost:8000/#/lab14      # website: dashboard, alerts, backups, PITR, benchmark\n"
                    f"docker compose exec lab14 lab14 all     # every Lab 14 task + this report\n"
                    f"docker compose exec lab14 lab14 lag     # make the replication-lag WARNING fire live", st)
    env = out.get("environment") or {}
    e.append(Paragraph(f"Generated {datetime.now():%d %b %Y %H:%M}. Watched deployment: replica set "
                       f"{escape(str(env.get('replica_set', 'rs0')))} ({escape(', '.join(env.get('members', [])))}); "
                       f"{escape(str(env.get('tools', '')))}; PyMongo {escape(str(env.get('pymongo', '?')))}. "
                       f"{'Quick run.' if out.get('quick') else 'Full run.'}", st["sub"]))
    if out.get("checks"):
        e += [Paragraph("Results at a glance", st["h1"]),
              tbl([["Check (measured on the running deployment)", "Result"]] + [[P(escape(c["name"]), st), yn(c["ok"])] for c in out["checks"]],
                  [W - 20 * mm, 20 * mm], extra=[("TEXTCOLOR", (1, i + 1), (1, i + 1), GREEN if c["ok"] else RED)
                                                 for i, c in enumerate(out["checks"])])]
    e += [Paragraph("What was built", st["h1"]), architecture(),
          Paragraph("Lab 14 operates the <b>Lab 13 production replica set</b>: a monitoring script reads the metrics of every member "
                    "every 30 seconds and alerts a webhook channel; a backup script dumps the data every hour, keeps 7 days, proves "
                    "that restores work and recovers to any second covered by the oplog. Following the mentor's rule it is applied to "
                    "the layers below: the backups and the recovery drill protect the <b>Lab 10 billing ledger</b> that Lab 13 moved "
                    "onto the production set, and the Kavach platform of Lab 7.", st["p"])]

    u = out.get("users") or {}
    if u.get("matrix"):
        ops = list(u["matrix"][0]["ops"])
        e += [Paragraph("Least-privilege users (measured)", st["h2"]),
              tbl([[P(h, st, "hcell") for h in ["User", "Roles"] + [o.split(" (")[0] for o in ops]]] +
                  [[r["user"], P(escape(", ".join(r["roles"])), st)] + [r["ops"][o] for o in ops] for r in u["matrix"]],
                  [22 * mm, 26 * mm] + [(W - 48 * mm) / len(ops)] * len(ops)),
              Paragraph("monitor_app sees every metric and cannot read a single business document. backup_app can read everything "
                        "(it must) but cannot update or delete; note that the built-in <b>restore role allows createUser</b>, so backup "
                        "credentials are as sensitive as an administrator's.", st["p"])]

    # ---------------------------------------------------------------- 14.1
    a = out.get("lab_14_1") or {}
    e += [PageBreak(), Paragraph("Lab 14.1 - Build a monitoring dashboard", st["h1"]),
          Paragraph("<b>Objective.</b> A Python script that reports key MongoDB metrics. <b>Instructions.</b> (1) connect and retrieve "
                    "connection count, opcounters, WiredTiger cache statistics and replica set status; (2) format the output as a table and "
                    "write it to a log file every 30 seconds; (3) print a WARNING if the cache hit ratio drops below 80 % or the "
                    "replication lag exceeds 10 seconds. Script: <font face='Courier'>labs/lab14/monitor_dashboard.py</font>.", st["p"]),
          tbl([["Instruction", "Implementation"],
               [P("connection count, opcounters", st), P("serverStatus of <b>each member</b> over a direct connection (connections.current, "
                                                        "opcounters + opcountersRepl); rates = difference between two samples / seconds", st)],
               [P("WiredTiger cache", st), P("bytes currently in the cache / maximum bytes configured; hit ratio = 1 - pages read into cache / "
                                             "pages requested from the cache, per interval", st)],
               [P("replica set status + lag", st), P("replSetGetStatus: state, health, sync source; lag = optimeDate(PRIMARY) - optimeDate(member)", st)],
               [P("table + log every 30 s", st), P("tabulate (github format), appended with open('metrics.log', 'a'); the ops daemon runs it "
                                                    "with schedule.every(30).seconds", st)],
               [P("thresholds", st), P("check_thresholds(): WARNING lines below 80 % / above 10 s; also a member that is not PRIMARY/SECONDARY", st)]],
              [42 * mm, W - 42 * mm])]
    if (a.get("sample") or {}).get("table"):
        e += [Paragraph("A table from results/metrics.log", st["h2"])] + code_block(a["sample"]["table"], st, width=150)
    e.append(Paragraph("<b>Correction to the solution text.</b> serverStatus has no 'bytes read from cache' counter (checked on 7.0.34); "
                       "the counters that exist are <i>pages requested from the cache</i> and <i>pages read into cache</i> (misses). The ratio "
                       "since start-up is also shown, but it hides a cold cache: after the restart in drill 2 it still read 99 %.", st["p"]))

    act = out.get("activity") or {}
    lg, cd, wt = out.get("lag_drill") or act.get("lag_drill") or {}, out.get("cache_drill") or act.get("cache_drill"), act.get("webhook_test") or {}
    e += [PageBreak(), Paragraph("Activity - alerts to a webhook, and two drills", st["h1"]),
          Paragraph("send_webhook() calls <font face='Courier'>requests.post(webhook_url, json=alert_payload, timeout=5)</font> inside "
                    "try/except. build_payload() writes the Slack shape (text + blocks) or the Discord shape (content + embeds). Only "
                    "changes are posted - FIRING once, a reminder every 10 minutes, RESOLVED once - while the log keeps a WARNING every "
                    "30 s. Webhook URLs are redacted in logs (the token is the secret). The website contains a receiver that answers like "
                    "the real services (Slack 200 'ok', Discord 204); LAB14_WEBHOOK_URL adds a real channel.", st["p"])]
    if wt.get("sent"):
        e.append(tbl([["Webhook", "Result"]] + [[P(mono(s["url"]), st), ("HTTP " + str(s.get("http"))) if s.get("ok") else P(escape(s.get("error", "")[:90]), st)]
                                               for s in wt["sent"]], [80 * mm, W - 80 * mm]))
        e.append(Paragraph("The last URL is deliberately unreachable: the exception is caught and the monitor carries on.", st["p"]))
    if lg and lg.get("series"):
        e += [Paragraph("Drill 1 - replication lag above 10 s", st["h2"]),
              Paragraph(f"db.fsyncLock() on <b>{escape(lg['target'])}</b> stops it applying the oplog while a writer inserts 5 documents per "
                        f"second with w: majority. Lag peaked at <b>{lg['max_lag_s']:.0f} s</b>; <b>{lg['writes_ok']}</b> writes acknowledged, "
                        f"{lg['writes_err']} errors; WARNING + webhook fired: {lg['fired']}, resolved after fsyncUnlock: {lg['resolved']}.", st["p"]),
              lag_chart(lg),
              Paragraph("<b>Measured pitfall.</b> With chained replication one secondary syncs from the other. Locking that sync source "
                        "froze BOTH secondaries in a first test: the w: majority writes stopped completely (0 acknowledged). The drill "
                        "therefore locks a member that nobody replicates from (sync sources: " +
                        escape(", ".join(f"{k} <- {v}" for k, v in lg.get("sync_sources", {}).items())) + ").", st["p"])]
    if cd and cd.get("scans"):
        e += [Paragraph("Drill 2 - cache hit ratio below 80 %", st["h2"]),
              Paragraph(f"{escape(cd['target'])} was restarted ({cd['restart_s']} s) - its WiredTiger cache is empty - and 60,000 documents "
                        f"were read from it twice:", st["p"]),
              tbl([["Read", "Time", "Pages requested", "Read into cache", "Hit ratio"]] +
                  [[s["label"], s2(s["seconds"]), str(s["pages_requested"]), str(s["pages_read_into"]), pct(s["hit_ratio"])] for s in cd["scans"]],
                  [60 * mm, 22 * mm, 30 * mm, 30 * mm, W - 142 * mm]),
              Paragraph(f"WARNING + webhook fired: {cd['fired']}; resolved by the warm read: {cd['resolved']}. A secondary that has just "
                        "restarted serves its first reads from disk - exactly when a failover sends traffic to it.", st["p"])]

    # ---------------------------------------------------------------- 14.2
    b = out.get("lab_14_2") or {}
    e += [PageBreak(), Paragraph("Lab 14.2 - Automated backup and restore verification", st["h1"]),
          Paragraph("Script <font face='Courier'>labs/lab14/backup_verify.py --uri URI --out DIR</font> (accepts a connection URI and an output "
                    "directory, as the solution asks). run_backup() = subprocess.run of mongodump into <font face='Courier'>"
                    "backup-YYYYmmddTHHMMSSZ</font>, return code captured into manifest.json; the URI goes in a mode-600 --config file so "
                    "the password never appears in <font face='Courier'>ps</font>. The ops daemon runs it with "
                    "<font face='Courier'>schedule.every().hour.do(...)</font> for the billing ledger and the Kavach platform, then "
                    "apply_retention() sorts the folders by creation time and removes those older than 7 days with shutil.rmtree().", st["p"])]
    if b.get("backups"):
        e.append(tbl([["Backup", "rc", "Seconds", "Size"]] + [[P(mono(x.get("name")), st), str(x.get("rc")), s2(x.get("seconds")),
                                                               f"{(x.get('bytes') or 0) / 1048576:.2f} MB"] for x in b["backups"]],
                     [W - 60 * mm, 12 * mm, 22 * mm, 26 * mm]))
    r = out.get("retention") or b.get("retention") or {}
    if r.get("before"):
        e += [Paragraph("Retention (7 days) on a simulated history", st["h2"]),
              tbl([["Folder (oldest first)", "Age", "Result"]] + [[P(mono(x["name"]), st), f"{x['age_h']} h", "deleted"] for x in r["deleted"]] +
                  [[P(mono(x["name"]), st), f"{x['age_h']} h", "kept"] for x in r["kept"]], [W - 50 * mm, 22 * mm, 28 * mm])]
    v = out.get("verify") or b.get("verify") or {}
    if v.get("ns"):
        e += [Paragraph("Restore verification - 1,000 documents", st["h2"]),
              tbl([["Step", "Result"],
                   ["1. insert 1,000 documents with timestamps", f"{v['ns']}, sha256 {v['sha256_before'][:16]}"],
                   ["2. mongodump", f"{v['backup']['name']}  rc {v['backup']['rc']}"],
                   ["3. modify: verified=false on all, delete 10", f"checker finds {v['mismatches_before_restore']} mismatches (it can fail)"],
                   ["4. mongorestore --drop --nsInclude", f"rc {v['restore_rc']}, {s2(v['restore_seconds'])}"],
                   ["5. assertion loop: count + every field", f"{v['mismatches_after_restore']} mismatches, sha256 {v['sha256_after'][:16]} -> {yn(v['pass'])}"]],
                  [70 * mm, W - 70 * mm])]
    pt = out.get("pitr") or b.get("pitr") or {}
    if pt.get("timeline"):
        e += [Paragraph("Point-in-time restore", st["h2"]),
              tbl([["Time", "Event", "Documents"]] + [[x["at"][11:19], P(escape(x["what"]), st), str(x["count"])] for x in pt["timeline"]],
                  [18 * mm, W - 40 * mm, 22 * mm]),
              tbl([["Replay as", "Replay rc", "Docs", "sha256", "Result"]] +
                  [[a_["user"], str(a_["replay_rc"]), str(a_["count"]), a_["sha256"], "matches" if a_["pass"] else "HALF RESTORED"]
                   for a_ in pt.get("attempts", [])], [60 * mm, 18 * mm, 16 * mm, 34 * mm, W - 128 * mm]),
              Paragraph("Measured: replaying as backup_app (backup + restore) applies the inserts and is refused at the first update "
                        "(applyOps Unauthorized) - the collection is left half restored, visible only through the checksum. On 7.0 the "
                        "deleteMany is logged as batched applyOps entries in admin.$cmd, so the incident is located through "
                        "<font face='Courier'>o.applyOps.ns</font>; --oplogLimit stops the replay just before it.", st["p"])]
    bm = out.get("benchmark") or b.get("benchmark") or {}
    if bm.get("rows"):
        rr = bm["rpo_rto"]
        e += [Paragraph("Benchmark, RPO and RTO", st["h2"]),
              tbl([[P(h, st, "hcell") for h in ("Documents", "Data", "mongodump", "Dump size", "--gzip", "gzip size", "mongorestore", "Restored")]] +
                  [[f"{x['docs']:,}", f"{x['data_mb']} MB", s2(x["dump_s"]), f"{x['dump_mb']} MB", s2(x["gzip_s"]), f"{x['gzip_mb']} MB",
                    s2(x["restore_s"]), f"{x['restored']:,} {'OK' if x['verified'] else 'FAIL'}"] for x in bm["rows"]],
                  [22 * mm, 20 * mm, 22 * mm, 22 * mm, 20 * mm, 22 * mm, 24 * mm, W - 152 * mm]),
              bars([(f"{x['docs']:,} {k}", x[f], c) for x in bm["rows"] for k, f, c in
                    (("dump", "dump_s", BLUE), ("restore", "restore_s", ACCENT))], "Wall-clock time (time module)"),
              tbl([["", "Value", "How"], ["RPO", f"{rr['rpo_s'] // 60} min (worst {rr['rpo_worst_s']:.0f} s)", P("backup interval (hourly)", st)],
                   ["RTO", f"{rr['rto_s']} s", P(f"restore {rr['restore_s']} s + application restart {rr['app_restart_s']} s (measured: a new process "
                                                  f"connects and answers its first query), {rr['for_docs']:,} documents", st)]],
                  [16 * mm, 40 * mm, W - 56 * mm]),
              Paragraph(escape(rr["explain"]) + " mongodump scales roughly linearly with the data; mongorestore is slower because "
                        "every document is inserted, replicated with w: majority and indexed again.", st["p"])]

    dr = out.get("billing_dr") or {}
    if dr.get("timeline"):
        e += [PageBreak(), Paragraph("Lab 14 applied to Lab 13 and Lab 10 - the billing ledger under attack", st["h1"]),
              Paragraph("The classifier billing (Lab 10) on the production set (Lab 13) is backed up; new customers then pay - money a "
                        "plain restore would lose; an attacker sets every wallet to Rs 0 and deletes all payments. Recovery = base restore "
                        "+ oplog replay (including the multi-document transactions, logged as applyOps) to the second before the attack.", st["p"]),
              tbl([[P(h, st, "hcell") for h in ("Moment", "Accounts", "Payments", "Wallets", "Revenue", "money conserved", "revenue = subs")]] +
                  [[P(escape(x["what"]), st), str(x["accounts"]), str(x["payments"]), f"Rs {x['invariants']['wallets']:,}",
                    f"Rs {x['invariants']['revenue']:,}", str(x["invariants"]["money_conserved"]), str(x["invariants"]["revenue_matches"])]
                   for x in dr["timeline"]], [W - 104 * mm, 16 * mm, 16 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm]),
              Paragraph(f"Recovery took <b>{dr['recovery_s']} s</b>; the ledger digest equals the one taken just before the attack: "
                        f"<b>{dr['digest_match']}</b>. Note: <b>money_conserved stayed true during the attack</b> (0 top-ups - 0 plans = 0 in "
                        f"wallets); only revenue = subscriptions caught it. One invariant is not enough, hence the digest.", st["p"])]

    e += [Paragraph("Notes on correctness (measured on MongoDB 7.0.34, database tools 100.13)", st["h1"])]
    for n in NOTES:
        e.append(Paragraph("&bull; " + n, st["p"]))
    log = results / "metrics.log"
    if log.exists():
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-22:]
        e += [Paragraph("Excerpt of results/metrics.log", st["h2"])] + code_block("\n".join(tail), st, width=150)
    doc.build(e, onFirstPage=_foot, onLaterPages=_foot)
    return pdf


NOTES = [
    "serverStatus has no 'bytes read from cache' counter; hit ratio = 1 - pages read into cache / pages requested from the cache, "
    "computed per interval (the ratio since start-up hides a cold cache).",
    "A SECONDARY counts replicated writes in opcountersRepl, not opcounters - the dashboard shows both.",
    "fsyncLock on a secondary that is the sync source of the other secondary stalls both, and w: majority writes stop: lock a leaf member.",
    "deleteMany() is written to the oplog as batched applyOps entries in admin.$cmd (10 deletes each), not under the collection's namespace.",
    "mongorestore --oplogReplay with only the backup + restore roles applies inserts but is refused at the first update: the collection "
    "is left half restored. Only verification reveals it; the replay needs a user that may run applyOps (admin here).",
    "The built-in restore role can create users: backup credentials must be protected like an administrator's.",
    "Multi-document transactions are replayed correctly by --oplogReplay (one applyOps entry per transaction).",
    "Passwords are given to mongodump/mongorestore in a mode-600 --config file instead of --uri on the command line (visible in ps).",
    "Alerts are posted on change only (FIRING / RESOLVED, reminder after 10 min) - one post every 30 s would be alert fatigue.",
    "The cold-cache drill never restarts prod-mongo-1: its exporter sidecar (Lab 13) shares that container's network namespace.",
]


def _foot(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GREY)
    canvas.drawString(16 * mm, 8 * mm, "Exercise 14 - Managing Database Services in Production  |  M Nagasai Girisharan  |  24BTRCL114")
    canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"page {doc.page}")
    canvas.restoreState()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    res = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
    data = json.loads((res / "lab14_results.json").read_text())
    print(build(data, res, os.getenv("DOCKER_URL", "")))

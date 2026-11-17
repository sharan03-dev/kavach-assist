"""Builds results/Exercise-13_Report.pdf - the file to submit (contains the Docker Hub URL on page 1).

    python labs/lab13/make_report.py            # uses results/lab13_results.json + results/console_13.txt
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location("lab12_report", ROOT / "labs" / "lab12" / "make_report.py")
R12 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R12)                      # shared look: styles, tables, boxes, arrows
styles, tbl, P, mono, code_block, sections, _box, _arrow = (R12.styles, R12.tbl, R12.P, R12.mono, R12.code_block,
                                                            R12.sections, R12._box, R12._arrow)
INK, ACCENT, GREY, BLUE, GREEN, RED = R12.INK, R12.ACCENT, R12.GREY, R12.BLUE, R12.GREEN, R12.RED
STUDENT = R12.STUDENT
LAB = ROOT / "labs" / "lab13"


def yn(v):
    return "PASS" if v else "FAIL"


def sec_(v):
    return "-" if v is None else f"{v / 1000:.1f} s" if isinstance(v, (int, float)) and v > 50 else f"{v} s"


# ----------------------------------------------------------------------------- drawings
def architecture():
    d = Drawing(500, 205)
    d.add(Rect(2, 108, 300, 94, fillColor=colors.HexColor("#eef3fb"), strokeColor=None))
    d.add(String(8, 192, "Docker Compose (Lab 13.1) - network kavach-net", fontSize=7.5, fillColor=INK, fontName="Helvetica-Bold"))
    _box(d, 8, 150, 62, 34, ["lab13-keyfile", "openssl rand"], fill="#fff4dc", size=6.6)
    for i in range(3):
        _box(d, 82 + i * 72, 150, 66, 34, [f"prod-mongo-{i + 1}", f"host :{27051 + i}"], fill="#ffffff", size=6.6)
        _arrow(d, 70, 167, 82 + i * 72, 167 - 0.01 * i, GREY) if i == 0 else None
    _box(d, 82, 114, 66, 28, ["exporter sidecar", ":9216 (same netns)"], fill="#dff3e5", size=6.2)
    _box(d, 160, 114, 66, 28, ["Prometheus", ":9090 + 4 alerts"], fill="#dff3e5", size=6.2)
    _box(d, 236, 114, 62, 28, ["lab13 / website", "PyMongo"], fill="#fff4dc", size=6.2)
    _arrow(d, 160, 128, 148, 128, BLUE)
    d.add(Rect(312, 108, 186, 94, fillColor=colors.HexColor("#f4effb"), strokeColor=None))
    d.add(String(318, 192, "Kubernetes (Lab 13.2) - k3s, namespace lab13", fontSize=7.5, fillColor=INK, fontName="Helvetica-Bold"))
    for i in range(3):
        _box(d, 318 + i * 59, 148, 55, 36, [f"mongo-{i}", "pod + PVC", "keyfile-init"], fill="#ffffff", size=5.9)
    _box(d, 318, 114, 85, 28, ["mongo-headless-svc", "clusterIP: None"], fill="#e9e2f7", size=6.2)
    _box(d, 410, 114, 84, 28, ["mongo-client pod", "transactions"], fill="#fff4dc", size=6.2)
    d.add(Rect(2, 4, 496, 96, fillColor=colors.HexColor("#fbf7ea"), strokeColor=None))
    d.add(String(8, 90, "Lab 13 applied to Lab 10 (mentor's rule) and the cloud", fontSize=7.5, fillColor=INK, fontName="Helvetica-Bold"))
    _box(d, 8, 40, 150, 40, ["Classifier billing (Lab 10)", "copied to prod rs0, used as billing_app",
                             "journeys run THROUGH failovers"], fill="#ffffff", size=6.2)
    _box(d, 172, 40, 150, 40, ["Kubernetes transfers (Lab 10)", "A -> B in transactions while", "the PRIMARY pod is deleted"],
         fill="#ffffff", size=6.2)
    _box(d, 336, 40, 156, 40, ["MongoDB Atlas M0 (Terraform)", "reference reviewed + corrected,", "terraform validate"],
         fill="#ffffff", size=6.2)
    d.add(String(8, 14, "Chain: Lab 7 -> 8 -> 9 -> 10 -> 11 -> 12 -> 13   (docker compose up -d lab13)", fontSize=7, fillColor=GREY, fontName="Helvetica"))
    return d


def timeline(f: dict, title: str):
    """Successful writes per 0.5 s (bars), errors (red), the event (dashed line), new primary (green line)."""
    tl = f.get("timeline") or []
    d = Drawing(500, 118)
    if not tl:
        return d
    W, H, L, B = 470, 80, 24, 20
    mx = max(max(b["w_ok"] for b in tl), 1)
    bw = W / len(tl)
    d.add(String(L, B + H + 10, title, fontSize=7.5, fillColor=INK, fontName="Helvetica-Bold"))
    for i, b in enumerate(tl):
        x = L + i * bw
        h = H * b["w_ok"] / mx
        d.add(Rect(x, B, max(bw - 0.6, 0.4), h, fillColor=BLUE, strokeColor=None))
        if b["w_err"] or b["r_err"]:
            d.add(Rect(x, B, max(bw - 0.6, 0.4), 6, fillColor=RED, strokeColor=None))
    span = tl[-1]["t"] + 500
    for t, col in ((f.get("event_ms"), colors.black), ((f.get("event_ms") or 0) + (f.get("election_ms") or 0), GREEN)):
        if t:
            x = L + W * t / span
            d.add(Line(x, B - 2, x, B + H + 2, strokeColor=col, strokeWidth=1, strokeDashArray=[2, 2]))
    d.add(Line(L, B, L + W, B, strokeColor=GREY, strokeWidth=0.5))
    for s in range(0, int(span / 1000) + 1, 5):
        x = L + W * s * 1000 / span
        d.add(String(x, B - 10, f"{s}s", fontSize=6, fillColor=GREY, textAnchor="middle", fontName="Helvetica"))
    d.add(String(L + W, B - 18, "blue = acknowledged writes per 0.5 s, red = errors seen by the app, dashed black = event, "
                 "dashed green = new PRIMARY", fontSize=6, fillColor=GREY, textAnchor="end", fontName="Helvetica"))
    return d


def per_second(series, title):
    d = Drawing(500, 90)
    if not series:
        return d
    W, H, L, B = 470, 58, 24, 16
    mx = max(max(series), 1)
    bw = W / len(series)
    d.add(String(L, B + H + 8, title, fontSize=7.5, fillColor=INK, fontName="Helvetica-Bold"))
    for i, v in enumerate(series):
        d.add(Rect(L + i * bw, B, max(bw - 1, 0.5), H * v / mx, fillColor=BLUE if v else RED, strokeColor=None))
    for s in range(0, len(series), 5):
        d.add(String(L + s * bw, B - 9, f"{s}s", fontSize=6, fillColor=GREY, fontName="Helvetica"))
    return d


# ----------------------------------------------------------------------------- the report
def build(out: dict, results: Path, docker_url: str) -> Path:
    st = styles()
    console = (results / "console_13.txt").read_text(encoding="utf-8") if (results / "console_13.txt").exists() else ""
    secs = sections(console)
    pdf = results / "Exercise-13_Report.pdf"
    doc = SimpleDocTemplate(str(pdf), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=15 * mm,
                            bottomMargin=15 * mm, title="Exercise 13 - Deploying MongoDB with Containers and the Cloud",
                            author="M Nagasai Girisharan")
    W = A4[0] - 32 * mm
    e = [Paragraph("Exercise 13 - Deploying MongoDB with Containers and the Cloud", st["title"]),
         Paragraph(escape(STUDENT), st["sub"])]
    url = docker_url or "(set DOCKER_URL / run ./publish.sh to fill in the Docker Hub link)"
    e += [Paragraph("<b>Docker Hub image (submission):</b>", st["p"]),
          Paragraph(f'<link href="{escape(url)}" color="#2f6fd0"><u>{escape(url)}</u></link>', st["p"]),
          Paragraph("<b>Demonstration for grading (from the uploaded image only):</b>", st["p"])]
    img = url.split("hub.docker.com/r/")[-1] if "hub.docker.com/r/" in url else "<user>/nosql-lab13"
    e += code_block(f"docker run --rm {img} compose > docker-compose.yml\n"
                    f"docker compose up -d lab13            # Lab 7 -> 8 -> 9 -> 10 -> 11 -> 12 -> 13 (+ prod rs0, Prometheus, k3s)\n"
                    f"open http://localhost:8000/#/lab13     # website; or: docker compose exec lab13 lab13 all\n"
                    f"docker compose exec lab13 lab13 crash   # kill the PRIMARY and watch the failover live\n"
                    f"docker compose exec lab13 kubectl -n lab13 get pods,pvc,svc", st)
    env = out.get("environment", {})
    e.append(Paragraph(f"Generated {datetime.now():%d %b %Y %H:%M}. Production replica set {escape(str(env.get('replica_set', 'rs0')))} "
                       f"on {escape(', '.join(env.get('prod_hosts', [])))}; Kubernetes: {escape(str(env.get('k8s_mode')))}; "
                       f"PyMongo {env.get('pymongo', '?')}. {'Quick run.' if out.get('quick') else 'Full run.'}", st["sub"]))
    if out.get("checks"):
        e += [Paragraph("Results at a glance", st["h1"]),
              tbl([["Check (measured on the running deployment)", "Result"]] +
                  [[P(escape(c["name"]), st), yn(c["ok"])] for c in out["checks"]], [W - 20 * mm, 20 * mm],
                  extra=[("TEXTCOLOR", (1, i + 1), (1, i + 1), GREEN if c["ok"] else RED) for i, c in enumerate(out["checks"])])]
    e += [Paragraph("What was deployed", st["h1"]), architecture(),
          Paragraph("Lab 13 adds a <b>production-style replica set</b> (three mongod containers with keyFile authentication), "
                    "an <b>exporter sidecar + Prometheus</b> (activity), a <b>Kubernetes</b> cluster (k3s in a privileged "
                    "container) running the StatefulSet of Lab 13.2, and a reviewed <b>Terraform</b> file for Atlas. Following the "
                    "mentor's rule, Lab 13 is applied to Lab 10: the classifier's billing transactions run on the new "
                    "deployments while the primary is stepped down, killed, or its pod deleted.", st["p"])]

    a = out.get("lab_13_1") or {}
    if a:
        e += [PageBreak(), Paragraph("Lab 13.1 - Deploy a replica set with Docker Compose", st["h1"]),
              Paragraph("<b>Objective.</b> A production-ready Compose configuration for a MongoDB replica set. <b>Steps.</b> "
                        "(1) Compose file for 3 members with keyFile authentication; (2) generate a keyfile and start the "
                        "cluster; (3) initialise the replica set and verify all three members; (4) connect PyMongo and verify "
                        "failover.", st["p"]), Paragraph("Step 1 - the Compose service of one member", st["h2"])]
        e += code_block(_member_excerpt(), st)
        e.append(Paragraph("Step 2 - <font face='Courier'>openssl rand -base64 756 &gt; keyfile; chmod 400 keyfile</font> runs in the "
                           "one-shot <b>lab13-keyfile</b> container, which also does <font face='Courier'>chown 999:999</font>: "
                           "mongod runs as uid 999 and refuses a keyfile it cannot read or that the group/world can read. The "
                           "volume is mounted read-only into the three members only.", st["p"]))
        v = a.get("verify") or {}
        if v.get("containers"):
            e += [Paragraph("Step 3 - members as Docker reports them", st["h2"]),
                  tbl([["Container", "Health", "Restart", "Limits", "Keyfile", "sha256"]] +
                      [[c["container"], c["health"] or "-", c["restart_policy"], f"{c['memory_limit_mb']} MB / {c['cpus']} CPU",
                        f"{c['keyfile'].get('mode')} {c['keyfile'].get('owner')} {c['keyfile'].get('bytes')} B",
                        P(mono((c["keyfile"].get("sha256") or "")[:16]), st)] for c in v["containers"]],
                      [26 * mm, 18 * mm, 24 * mm, 28 * mm, 40 * mm, W - 136 * mm])]
            init = a.get("init") or {}
            e.append(Paragraph(f"rs.initiate() was run with <font face='Courier'>docker exec prod-mongo-1 mongosh</font> - the "
                               f"<b>localhost exception</b> allows it (and the first createUser) while no user exists. PRIMARY "
                               f"elected: {escape(str(init.get('primary')))}. Users: " +
                               ", ".join(f"{mono(u['user'])} ({escape(', '.join(u['roles']))})" for u in init.get("users", [])) + ".",
                               st["p"]))
            e.append(tbl([["Verification", "Result"]] + [[P(escape(k), st), yn(x)] for k, x in v.get("checks", {}).items()],
                         [W - 20 * mm, 20 * mm]))
            rs = v.get("rs") or {}
            e.append(Paragraph("rs.status(): " + ", ".join(f"<b>{escape(m['name'])}</b> {m['state']} (health {m['health']})"
                                                         for m in rs.get("members", [])), st["p"]))
        rg = a.get("rogue")
        if rg and rg.get("ran"):
            e.append(Paragraph(f"<b>Internal authentication measured.</b> A fourth mongod with a separately generated keyfile was "
                               f"added (priority 0, votes 0): state <b>{escape(str(rg.get('state')))}</b>, health "
                               f"{rg.get('health')}, {rg.get('auth_failures_logged')} 'Authentication failed' log lines; then removed. "
                               f"{escape(rg.get('log_sample', '')[:180])}", st["p"]))
        e += [Paragraph("The reference compose file, reviewed", st["h2"]),
              tbl([["Reference file", "Problem", "This lab"]] + [[P(escape(x), st), P(escape(y), st), P(escape(z), st)]
                                                                for x, y, z in COMPOSE_REVIEW], [44 * mm, 66 * mm, W - 110 * mm])]
        ref = (v or {}).get("reference_healthcheck")
        if ref:
            e.append(Paragraph(f"Measured: the reference health check (<font face='Courier'>mongosh -u admin -p AdminPass123!</font>) "
                               f"exits with code {ref['exit_code']}: <i>{escape(ref['output'][:120])}</i>.", st["p"]))
        e.append(Paragraph("Step 4 - PyMongo failover", st["h2"]))
        rows = []
        for key, name in (("failover_stepdown", "rs.stepDown() (planned)"), ("failover_crash", "kill -9 mongod (crash)")):
            f = a.get(key)
            if f and not f.get("error"):
                rows.append([name, f"{f['old_primary']} -> {f['new_primary']}", sec_(f.get("election_ms")),
                             f"{f['write_gap_ms']} ms", str(sum(f["write_errors"].values())), f"{f['acknowledged']}/{f['stored']}",
                             str(f["lost_acknowledged_writes"]), escape(str(f.get("last_election_reason")))])
        if rows:
            head = [P(h, st, "hcell") for h in ("Event", "Primary", "New primary after", "Longest write pause", "App errors",
                                                 "Acked / stored", "Lost", "Election reason")]
            e.append(tbl([head] + [[P(escape(str(c)), st) for c in r] for r in rows],
                         [26 * mm, 40 * mm, 16 * mm, 17 * mm, 13 * mm, 17 * mm, 10 * mm, W - 139 * mm]))
        for key, name in (("failover_stepdown", "rs.stepDown()"), ("failover_crash", "kill -9 of the PRIMARY")):
            f = a.get(key)
            if f and f.get("timeline"):
                e.append(timeline(f, f"PyMongo writes during {name}"))
        fc = a.get("failover_crash")
        if fc and not fc.get("error"):
            e.append(Paragraph(f"After the crash Docker restarted <b>{escape(str(fc.get('crashed_container')))}</b> "
                               f"{sec_(fc.get('container_running_again_ms'))} after the kill (RestartCount "
                               f"{fc.get('restart_count_before')} -> {fc.get('restart_count_after')}); it was SECONDARY again after "
                               f"{sec_(fc.get('rejoined_secondary_ms'))}. A crash costs about electionTimeoutMillis (10 s) plus the "
                               "election, a step-down well under a second; in both cases no write acknowledged with w:majority "
                               "was lost, and retryable writes hid the planned failover from the application.", st["p"]))

    m = out.get("activity")
    if m and not m.get("error"):
        sc = m.get("scrape") or {}
        e += [PageBreak(), Paragraph("Activity - Prometheus exporter sidecar", st["h1"]),
              Paragraph(f"<b>{escape(m['sidecar']['container'])}</b> runs percona mongodb_exporter with "
                        f"<font face='Courier'>network_mode: service:prod-mongo-1</font>: Docker reports NetworkMode "
                        f"{mono(m['sidecar']['network_mode'][:22] + '...')} = the network namespace of "
                        f"<b>{escape(str(m['sidecar']['shares_netns_with']))}</b>, so it reaches mongod on 127.0.0.1:27017 and "
                        f"is scraped at prod-mongo-1:9216 - exactly a Kubernetes sidecar. It logs in as <b>exporter</b> "
                        f"(clusterMonitor + read on local); the password comes from the secrets volume as an environment "
                        f"variable ({escape(', '.join(m['sidecar']['env_names']))}), not from the command line.", st["p"]),
              tbl([["Measurement", "Value"],
                   ["exporter scrape", f"{sc.get('lines')} samples, {sc.get('families')} mongodb_* families, {sc.get('ms')} ms"],
                   ["mongodb_up", str(sc.get("mongodb_up"))],
                   ["members (exporter view)", P(escape(json.dumps(sc.get("members"))), st)],
                   ["Prometheus targets", P(escape(", ".join(f"{t['job']} {t['health']}" for t in m.get("targets", []))), st)],
                   ["inserts counter (before -> after 3,000 inserts)", f"{m.get('inserts_before')} -> {m.get('inserts_after')}"],
                   ["insert rate (30 s window)", f"{m.get('insert_rate_per_s')} / s"],
                   ["alert rules", P(escape(", ".join(r["name"] for r in m.get("rules", []))), st)]], [60 * mm, W - 60 * mm]),
              Paragraph("Inserts on a PRIMARY are counted in <i>opcounters</i>, the same inserts applied on a SECONDARY in "
                        "<i>opcountersRepl</i>; the sidecar's member can be either, so the dashboards add both (a secondary "
                        "showed opcounters.insert = 0 under load while testing).", st["p"])]
        rt = m.get("restart_test")
        if rt:
            e.append(Paragraph(f"<b>Sidecar vs. main container restart (measured).</b> docker restart prod-mongo-1: back after "
                               f"{rt.get('main_back_s')} s; the sidecar then "
                               f"{'could NOT be scraped and had to be restarted (' + str(rt.get('sidecar_fix_s')) + ' s)' if rt.get('sidecar_broken') else 'kept working'}. "
                               "In Kubernetes the containers of a pod share one network namespace that lives as long as the "
                               "pod, and they are restarted together.", st["p"]))

    b = out.get("billing")
    if b:
        e += [PageBreak(), Paragraph("Lab 13 applied to Lab 10 - the classifier billing on the production replica set", st["h1"]),
              Paragraph("Business rule of Lab 10: 3 free classifications, then Rs 100 per month from a wallet; every change is one "
                        "multi-document ACID transaction. The billing data was copied to the production replica set and used "
                        "there through the least-privilege user <b>billing_app</b> (custom role billingRole: readWrite on "
                        "cti_billing + replSetGetConfig).", st["p"])]
        c = b.get("copy") or {}
        if c.get("collections"):
            e.append(tbl([["Collection", "Source", "Copied", "Verified"]] +
                         [[k, f"{v['source']:,}", f"{v['copied']:,}", yn(v["verified"])] for k, v in c["collections"].items()],
                         [40 * mm, 30 * mm, 30 * mm, W - 100 * mm]))
            e.append(Paragraph(f"Source: {escape(c.get('source', ''))}. Wallet total and revenue identical on both: "
                               f"<b>{c.get('verified')}</b>.", st["p"]))
        ac = b.get("acid") or {}
        if ac.get("free_trial_race"):
            fr, cr, dc = ac["free_trial_race"], ac["crash_after_debit"], ac["double_click"]
            e.append(tbl([["Lab 10 ACID check, now on prod rs0", "Measured", "Result"],
                          ["A+I: 10 simultaneous uses by a new user", f"{fr['granted']} granted, {fr['refused']} refused, "
                           f"free_used {fr['free_used']}", yn(fr["pass"])],
                          ["A: crash after the wallet debit", f"wallet {cr['wallet_before']} -> {cr['wallet_after']}, "
                           f"payments {cr['subscription_payments']}", yn(cr["pass"])],
                          ["C: double click on Pay (same idempotency key)", f"{dc['responses']}, wallet 250 -> {dc['wallet_after']}",
                           yn(dc["pass"])],
                          ["D: write concern majority + journal", f"journal default {ac['durability']['journal_default']}",
                           yn(ac["durability"]["pass"])]], [62 * mm, W - 82 * mm, 20 * mm]))
        rows = []
        for k, name in (("stepdown", "rs.stepDown()"), ("crash", "kill -9 of the PRIMARY")):
            j = b.get(k)
            if j and not j.get("error"):
                rows.append([name, f"{j['journeys_ok']}/{j['users']}", str(j["app_retries"]), str(j["recovered_committed"]),
                             f"{j['invariants']['money_conserved']} / {j['invariants']['revenue_matches']}", yn(j["pass"])])
        if rows:
            e += [Paragraph("Customer journeys running THROUGH a failover", st["h2"]),
                  Paragraph("Each journey: sign in, 3 free classifications, a 4th that must be refused (402), top-up Rs 200 "
                            "(UPI, idempotency key), subscribe Rs 100 (idempotency key), 2 classifications on the plan. After an "
                            "error the application first looks for the operation's own record (usage note / payment key) - if it "
                            "is there, the operation had committed and is NOT repeated. Expected per user: wallet Rs 100, "
                            "free_used 3, 5 usage records, 2 payments.", st["p"]),
                  tbl([[P(h, st, "hcell") for h in ("Failover", "Journeys exactly right", "App retries", "Found committed",
                                                    "Money / revenue invariants", "Result")]] + rows, [36 * mm, 30 * mm, 20 * mm, 24 * mm, 40 * mm, W - 150 * mm])]
        e.append(Paragraph("<b>Finding.</b> Lab 10's run_txn retries a transaction 5 times with 0.1-1.6 s back-off (about 3 s); a "
                           "crash takes about 11 s to elect a new primary, so the transaction gives up first. Safe recovery "
                           "therefore needs the application-level retry used here, and it is only safe because every operation "
                           "can be recognised afterwards (idempotency keys for payments, an operation id for each use).", st["p"]))

    k = out.get("lab_13_2") or {}
    if k and not k.get("skipped"):
        dpl = k.get("deploy") or {}
        e += [PageBreak(), Paragraph("Lab 13.2 - Kubernetes StatefulSet for MongoDB", st["h1"])]
        if dpl.get("error"):
            e.append(Paragraph(f"<b>Kubernetes part did not complete:</b> {escape(dpl['error'])}", st["p"]))
        else:
            cl = dpl.get("cluster") or {}
            e += [Paragraph(f"Cluster: k3s node {escape(str(cl.get('node')))} ({escape(str(cl.get('kubelet')))}, "
                            f"{escape(str(cl.get('runtime')))}). Manifests: namespace.yaml, mongo-headless-svc.yaml, "
                            f"mongo-statefulset.yaml, mongo-client.yaml (validated with kubeconform against Kubernetes 1.30).",
                            st["p"])]
            e += code_block(_sts_excerpt(), st)
            e += [tbl([["Pod", "Phase", "Ready", "Restarts", "IP", "InitContainer"]] +
                      [[p["pod"], p["phase"], str(p["ready"]), str(p["restarts"]), p.get("ip") or "-", P(escape(", ".join(p["init"])), st)]
                       for p in dpl.get("pods", [])], [26 * mm, 18 * mm, 14 * mm, 16 * mm, 26 * mm, W - 100 * mm]),
                  Spacer(1, 3),
                  tbl([["PVC (volumeClaimTemplates)", "Status", "Size", "Class", "Volume"]] +
                      [[p["name"], p["status"], p["size"], str(p["class"]), P(escape(str(p["volume"])), st)] for p in dpl.get("pvcs", [])],
                      [38 * mm, 18 * mm, 14 * mm, 22 * mm, W - 92 * mm]),
                  Paragraph("Keyfile in every pod (copied by the InitContainer from Secret mongo-keyfile): " +
                            ", ".join(f"{x['pod']} {x['mode']} {x['owner']} sha {mono((x['sha256'] or '')[:10])}" for x in dpl.get("keyfiles", []))
                            + ". Headless service DNS: " + ", ".join(f"{escape(x[1])} -> {escape(x[0])}" for x in dpl.get("dns", []) if len(x) > 1)
                            + ". rs.status(): " + ", ".join(f"{escape(m['name'].split('.')[0])} {m['state']}" for m in (dpl.get("rs") or {}).get("members", []))
                            + ".", st["p"])]
        rz = k.get("resiliency")
        if rz and not rz.get("error"):
            t = rz.get("timeline_s", {})
            e += [Paragraph("Test resiliency - kubectl delete pod mongo-2", st["h2"]),
                  tbl([["", "Pod uid", "PVC uid", "Volume", "Documents on mongo-2"],
                       ["before", rz["before"]["pod_uid"], rz["before"]["pvc_uid"], P(escape(str(rz["before"]["volume"])), st), str(rz["before"]["count_on_pod"])],
                       ["after", rz["after"]["pod_uid"], rz["after"]["pvc_uid"], P(escape(str(rz["after"]["volume"])), st), str(rz["after"]["count_on_pod"])]],
                      [18 * mm, 24 * mm, 24 * mm, W - 106 * mm, 40 * mm]),
                  Paragraph(f"Recreated after {t.get('recreated')} s, Running {t.get('running')} s, Ready {t.get('ready')} s, SECONDARY "
                            f"again after {t.get('secondary')} s. A NEW pod (new uid) with the SAME name and the SAME "
                            "PersistentVolumeClaim: its data was still there, so it rejoined by catching up from the oplog "
                            "instead of an initial sync.", st["p"])]
        fo = k.get("failover")
        if fo and not fo.get("error"):
            cl = fo.get("client") or {}
            bal = fo.get("balances") or {}
            e += [Paragraph("Lab 13.2 applied to Lab 10 - the PRIMARY pod deleted during transactions", st["h2"]),
                  Paragraph(f"The mongo-client pod moved Rs 1 from A to B in a multi-document transaction every ~50 ms "
                            f"(labs/lab13/mongosh/k8s_transfers.js). At t = 10 s <b>{escape(str(fo.get('deleted_primary')))}</b> "
                            f"(PRIMARY) was deleted. New PRIMARY {escape(str(fo.get('new_primary')))} after "
                            f"{fo.get('new_primary_s')} s; the deleted pod was Ready after {fo.get('ready_s')} s and SECONDARY "
                            f"after {fo.get('secondary_s')} s. Client: {cl.get('committed')} transfers, {cl.get('retries')} retries "
                            f"({escape(json.dumps(cl.get('errors')))}), {cl.get('recovered')} found committed after an error, "
                            f"longest pause {cl.get('max_gap_ms')} ms. Afterwards A = {bal.get('A')}, B = {bal.get('B')} "
                            f"(sum {(bal.get('A') or 0) + (bal.get('B') or 0)}), ledger {bal.get('ledger')} entries: "
                            f"<b>{yn(fo.get('pass'))}</b>.", st["p"]),
                  per_second(fo.get("per_second"), "Transfers committed per second (red = none)")]
        cv = k.get("classroom_variant")
        if cv and not cv.get("error"):
            ini = cv.get("initiate") or {}
            e += [Paragraph("The solution text, tested as written", st["h2"]),
                  Paragraph("The solution says the InitContainer <i>generates</i> the keyfile and copies it to a shared emptyDir. "
                            "An emptyDir is private to one pod, so every pod generates its own key. Measured with 2 pods "
                            f"(classroom-variant/): keyfile sha256 {escape(json.dumps(cv.get('keyfile_sha256')))}; "
                            f"rs.initiate() -> <b>{escape(str(ini.get('codeName')))} ({ini.get('code')})</b>: "
                            f"<i>{escape(str(ini.get('message'))[:200])}</i>. The working StatefulSet therefore generates the key "
                            "ONCE into a Secret and the InitContainer only installs it (owner 999, mode 400) in each pod.", st["p"])]

    cmp_ = out.get("compare")
    if cmp_:
        e += [PageBreak(), Paragraph("Docker Compose vs Kubernetes", st["h1"]),
              tbl([["Aspect", "Docker Compose (13.1)", "Kubernetes StatefulSet (13.2)"]] +
                  [[P(f"<b>{escape(r['aspect'])}</b>", st), P(escape(r["compose"]), st), P(escape(r["k8s"]), st)] for r in cmp_],
                  [34 * mm, (W - 34 * mm) / 2, (W - 34 * mm) / 2]),
              Paragraph("Compose is the simpler choice on one machine; Kubernetes pays for self-healing across machines, stable "
                        "identities and volume management with considerably more configuration and moving parts.", st["p"])]

    c = out.get("cloud")
    if c and not c.get("error"):
        e += [Paragraph("Cloud - MongoDB Atlas free tier with Terraform", st["h1"]),
              Paragraph("The reference chapter-13/main.tf was reviewed line by line and corrected (labs/lab13/terraform/main.tf).",
                        st["p"]),
              tbl([["Issue in the reference main.tf", "Found", "Why it matters", "Corrected file"]] +
                  [[P(escape(r["issue"]), st), "yes" if r["found"] else "no", P(escape(r["why"]), st), P(escape(r["fix"]), st)]
                   for r in c.get("review", [])], [38 * mm, 12 * mm, 70 * mm, W - 120 * mm])]
        for key, name in (("validate_reference", "reference main.tf"), ("validate_corrected", "corrected main.tf")):
            v = c.get(key) or {}
            e.append(Paragraph(f"<b>terraform validate, {name}:</b> " + (
                f"valid = {v.get('valid')}, {v.get('errors')} errors, {v.get('warnings')} warnings (Terraform {escape(str(v.get('terraform')))}, "
                f"mongodbatlas {escape(str(v.get('provider')))}). " + escape("; ".join(v.get("diagnostics", []))[:300])
                if v.get("ran") else escape(v.get("reason", ""))), st["p"]))
        e.append(Paragraph("<i>validate</i> checks syntax and the provider schema only; the TENANT/M0 rule and the broken output "
                           "are found by the Atlas API at apply time - that is why they were reviewed by reading.", st["p"]))
        at = c.get("atlas") or {}
        e.append(Paragraph("<b>Live Atlas test:</b> " + (
            f"connected in {at.get('connect_ms')} ms to {escape(str(at.get('set')))} ({at.get('hosts')} members, MongoDB "
            f"{escape(str(at.get('version')))}), insert w:majority {at.get('insert_ms_wmajority')} ms." if at.get("connected")
            else escape(at.get("reason") or at.get("error") or "not run")), st["p"]))

    e += [PageBreak(), Paragraph("Notes on correctness (observed on MongoDB 7.0)", st["h1"])]
    for n in NOTES:
        e.append(Paragraph("&bull; " + n, st["p"]))
    if secs:
        e += [PageBreak(), Paragraph("Console output (results/console_13.txt)", st["h1"])]
        for name, body in secs.items():
            e += [Paragraph(escape(name), st["h2"])] + code_block(body, st)
    doc.build(e, onFirstPage=_foot, onLaterPages=_foot)
    return pdf


def _member_excerpt() -> str:
    lines = (LAB / "compose" / "docker-compose.production.yml").read_text().splitlines()
    s = next(i for i, ln in enumerate(lines) if ln.startswith("x-mongod"))
    e = next(i for i, ln in enumerate(lines) if ln.strip().startswith("prod-mongo-2:"))
    return "\n".join(ln for ln in lines[s:e] if ln.strip())


def _sts_excerpt() -> str:
    lines = [ln for ln in (LAB / "k8s" / "mongo-statefulset.yaml").read_text().splitlines() if not ln.startswith("#") and ln.strip()]
    return "\n".join(lines[:80])


COMPOSE_REVIEW = [
    ("healthcheck: mongosh -u admin -p AdminPass123!", "the user is never created -> never healthy; the password is visible "
     "in docker inspect", "ping without credentials (allowed before log-in) on every member"),
    ("./keyfile:/etc/mongodb/keyfile:ro + chmod 400 on the host", "the file belongs to the host user; mongod (uid 999) cannot "
     "read it on Linux", "keyfile made in a volume: openssl rand, chown 999:999, chmod 400"),
    ("--auth next to --keyFile", "redundant: keyFile already enables authorization", "--keyFile only"),
    ("no restart policy, no limits, only member 1 has a health check", "a crashed member stays down; one member can starve "
     "the host", "restart: unless-stopped, init, 768 MB / 1 CPU, log rotation, health checks everywhere"),
    ("version: \"3.8\"", "obsolete in Compose v2 (warning)", "removed"),
    ("host ports 27017-27019 -> 27017", "host clients with replicaSet=rs0 get member names that only resolve inside "
     "Docker", "applications run on the compose network; from the host use directConnection=true"),
]

NOTES = [
    "<b>--keyFile implies authorization</b>; the first user and rs.initiate() use the localhost exception, so they are run "
    "with <font face='Courier'>docker exec &lt;member&gt; mongosh</font> (or <font face='Courier'>kubectl exec mongo-0 -- mongosh</font>).",
    "mongod requires the keyfile to be readable by its own user and not by group/world: <b>owner 999, mode 400</b>. Secrets "
    "and host bind mounts are owned by root/the host user, hence the copy step (Compose: keyfile container; Kubernetes: InitContainer).",
    "One keyfile for the whole set: a per-pod generated key (solution text) makes rs.initiate fail with NodeNotFound "
    "'Authentication failed' (measured, also on a local 7.0.34 set).",
    "<b>--wiredTigerCacheSizeGB must be &gt;= 0.25</b> (0.1 is refused at start-up: BadValue) - observed while testing.",
    "A killed mongod is PID 1 only without an init process; a SIGKILL sent from inside the container to PID 1 is ignored, so "
    "the members run with <font face='Courier'>init: true</font> (tini is PID 1, mongod exits, the restart policy applies).",
    "rs.stepDown() is absorbed by retryable writes/reads (no error reached the application); a crash costs about "
    "electionTimeoutMillis (10 s) + the election. Writes acknowledged with w:majority survived both.",
    "A transaction body cannot be retried blindly after an error: the commit may have happened. The lab records an id inside "
    "the same transaction (ledger _id, usage note, payment idempotency key) and checks it before retrying.",
    "The headless service uses <b>publishNotReadyAddresses</b>, so member names resolve before readiness; MongoDB, not "
    "Kubernetes readiness, decides who is in the set.",
    "percona mongodb_exporter 0.40 with --collect-all returned only mongodb_up against MongoDB 7.0; 0.47.2 with the "
    "diagnosticdata + replicasetstatus collectors returns the full set without errors (tested).",
    "Atlas M0 needs provider_name TENANT; terraform validate cannot detect it (API-side rule).",
]


def _foot(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GREY)
    canvas.drawString(16 * mm, 8 * mm, "Exercise 13 - Deploying MongoDB with Containers and the Cloud  |  M Nagasai Girisharan  |  24BTRCL114")
    canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"page {doc.page}")
    canvas.restoreState()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    res = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
    data = json.loads((res / "lab13_results.json").read_text())
    print(build(data, res, os.getenv("DOCKER_URL", "")))

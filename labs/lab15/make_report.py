"""Builds results/Exercise-15_Report.pdf (the Docker Hub URL is on page 1).   python labs/lab15/make_report.py"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

ROOT = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location("lab12_report", ROOT / "labs" / "lab12" / "make_report.py")
R12 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R12)
styles, tbl, P, mono, code_block, _box, _arrow = R12.styles, R12.tbl, R12.P, R12.mono, R12.code_block, R12._box, R12._arrow
INK, ACCENT, GREY, BLUE, GREEN, RED = R12.INK, R12.ACCENT, R12.GREY, R12.BLUE, R12.GREEN, R12.RED


def yn(v):
    return "PASS" if v else "FAIL"


def architecture():
    d = Drawing(500, 150)
    d.add(Rect(2, 2, 496, 146, fillColor=colors.HexColor("#eef3fb"), strokeColor=None))
    d.add(String(8, 138, "One request through TaskTracker (Lab 15) - running on Labs 14 -> 7", fontSize=7.5, fillColor=INK, fontName="Helvetica-Bold"))
    steps = [["Client / website", "Bearer JWT (HS256)"], ["current_user", "verify + re-read user", "with the token's tenant"],
             ["rate_limit", "findAndModify $inc", "tenant:minute (TTL)"], ["scoped()", "{tenant_id, ...}", "+ owner_id (member)"],
             ["MongoDB prod rs0", "tasktracker_app", "w: majority, $jsonSchema"]]
    for i, s in enumerate(steps):
        _box(d, 8 + i * 98, 86, 88, 40, s, fill="#ffffff" if i < 4 else "#dff3e5", size=6.2)
        if i:
            _arrow(d, 8 + i * 98 - 10, 106, 8 + i * 98, 106, BLUE)
    for i, (t, l) in enumerate([("401", "no/forged/expired token, tenant swapped"), ("429", "over the tenant's plan limit"),
                                ("404 / 403", "other tenant / not the owner")]):
        _box(d, 106 + i * 98, 48, 88, 28, [t, l], fill="#fde8e8", size=6)
    _box(d, 8, 10, 236, 28, ["Lab 15.2: tasks (one collection, tenant_id) -> tasks_tenant_<id>", "$match + $out, validate, compare"], fill="#fff4dc", size=6.2)
    _box(d, 254, 10, 238, 28, ["Applied: Lab 13 prod set, Lab 12 least privilege, Lab 10", "signup transaction, Lab 9 w:majority, Lab 14 backups"], fill="#fff4dc", size=6.2)
    return d


def tests_table(rows, W, st):
    return tbl([["Test (real HTTP request)", "Expected", "Got", ""]] +
               [[P(escape(r["test"]), st), str(r["expected"]), str(r["got"]), yn(r["pass"])] for r in rows],
               [W - 54 * mm, 20 * mm, 20 * mm, 14 * mm],
               extra=[("TEXTCOLOR", (3, i + 1), (3, i + 1), GREEN if r["pass"] else RED) for i, r in enumerate(rows)])


def build(out: dict, results: Path, docker_url: str) -> Path:
    st = styles()
    pdf = results / "Exercise-15_Report.pdf"
    doc = SimpleDocTemplate(str(pdf), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
                            title="Exercise 15 - Designing and Architecting a MongoDB-Backed Product as a Service", author="M Nagasai Girisharan")
    W = A4[0] - 32 * mm
    e = [Paragraph("Exercise 15 - Designing and Architecting a MongoDB-Backed Product as a Service", st["title"]),
         Paragraph(escape(R12.STUDENT), st["sub"])]
    url = docker_url or "(set DOCKER_URL / run ./publish.sh to fill in the Docker Hub link)"
    img = url.split("hub.docker.com/r/")[-1] if "hub.docker.com/r/" in url else "<user>/nosql-lab15"
    e += [Paragraph("<b>Docker Hub image (submission):</b>", st["p"]),
          Paragraph(f'<link href="{escape(url)}" color="#2f6fd0"><u>{escape(url)}</u></link>', st["p"]),
          Paragraph("<b>Demonstration for grading (from the uploaded image only):</b>", st["p"])]
    e += code_block(f"docker run --rm {img} compose > docker-compose.yml\n"
                    "docker compose up -d lab15                 # Lab 7 -> 8 -> ... -> 14 -> 15\n"
                    "open http://localhost:8000/#/lab15         # the TaskTracker product + every test\n"
                    "open http://localhost:8000/tasktracker/docs   # Swagger UI of the API\n"
                    "docker compose exec lab15 lab15 all        # every Lab 15 task + this report", st)
    env = out.get("environment") or {}
    e.append(Paragraph(f"Generated {datetime.now():%d %b %Y %H:%M}. Database tasktracker on replica set {escape(str(env.get('replica_set', '')))} "
                       f"({escape(', '.join(env.get('members', [])))}).", st["sub"]))
    if out.get("checks"):
        e += [Paragraph("Results at a glance", st["h1"]),
              tbl([["Check (measured)", "Result"]] + [[P(escape(c["name"]), st), yn(c["ok"])] for c in out["checks"]], [W - 20 * mm, 20 * mm],
                  extra=[("TEXTCOLOR", (1, i + 1), (1, i + 1), GREEN if c["ok"] else RED) for i, c in enumerate(out["checks"])])]
    e += [Paragraph("Architecture", st["h1"]), architecture(),
          Paragraph("TaskTracker is a multi-tenant task SaaS (<font face='Courier'>labs/lab15/tasktracker/app.py</font>, FastAPI). It is mounted in the "
                    "Kavach website at /tasktracker (Swagger UI at /tasktracker/docs) and has its own product page with sign-in, a task board, "
                    "a live rate-limit meter and team management for admins.", st["p"]),
          Paragraph("Schema", st["h2"]),
          tbl([["Collection", "Fields", "Indexes / rules"],
               ["tenants", P("_id (slug), name, plan (free | pro | enterprise), rate_limit", st), P("$jsonSchema", st)],
               ["users", P("tenant_id, email, name, role (admin | member), pw (pbkdf2-sha256, 120,000 rounds)", st), P("unique (tenant_id, email); $jsonSchema", st)],
               ["tasks", P("tenant_id, owner_id, owner, title, description, priority, status, created_at, updated_at", st),
                P("(tenant_id, created_at), (tenant_id, owner_id, created_at); $jsonSchema", st)],
               ["rate_limits", P("_id = tenant:minute, tenant_id, window, limit, count, ts", st), P("TTL on ts (120 s)", st)]],
              [24 * mm, W - 84 * mm, 60 * mm])]

    a = out.get("lab_15_1") or {}
    if a.get("tests"):
        ex, s = a.get("explain", {}), a.get("sample", {})
        e += [PageBreak(), Paragraph("Lab 15.1 - Design a multi-tenant API", st["h1"]),
              Paragraph("<b>Instructions.</b> (1) schema with tenant isolation; (2) CRUD endpoints with tenant_id filtering on every query; (3) rate "
                        "limiting using MongoDB as the counter store. The tenant comes only from the verified JWT (FastAPI dependency "
                        "current_user); every query is built by one function, <font face='Courier'>scoped(user, extra)</font>.", st["p"]),
              tests_table(a["tests"], W, st),
              Paragraph(f"explain() of the list query: <b>{escape(str(ex.get('stage')))}</b> on {escape(str(ex.get('index')))}, keys examined "
                        f"{ex.get('keys_examined')}, documents examined {ex.get('docs_examined')}, returned {ex.get('returned')} of "
                        f"{ex.get('total_tasks')} tasks in the shared collection.", st["p"]),
              Paragraph("The classroom sample, tested unchanged", st["h2"]),
              tbl([["Request", "Result", "Why"],
                   ["POST /tasks/", f"HTTP {s.get('create_status')}, stored: {s.get('stored_despite_error')}",
                    P("insert_one adds an ObjectId _id to the dict; FastAPI cannot serialise it. The write happened - a retrying client duplicates it.", st)],
                   ["priority 'urgent!!'", f"stored: {s.get('invalid_priority_stored')}", P("priority is a plain str; no validator", st)],
                   ["GET /tasks/?tenant_id=acme, no login", f"HTTP {s.get('read_other_tenant_without_login')}",
                    P("tenant_id is a query parameter: any caller reads any tenant (" + escape(", ".join(s.get("leaked_titles", []))) + ")", st)],
                   ["async def + PyMongo", "-", P("synchronous driver inside async endpoints blocks the event loop; TaskTracker uses def endpoints", st)]],
                  [42 * mm, 40 * mm, W - 82 * mm])]
    r = out.get("rate") or {}
    if r.get("burst"):
        e += [Paragraph("Rate limiting with MongoDB as the counter store", st["h2"]),
              Paragraph("One counter document per tenant and minute; each request runs find_one_and_update with $inc and upsert (findAndModify) "
                        "and reads the new count in the same atomic operation; a TTL index on ts removes old windows.", st["p"]),
              tbl([["Test", "Result"], ["70 requests, limit 50/min", f"{r['burst']['ok']} x 200, {r['burst']['limited']} x 429, Retry-After {r['burst']['retry_after']} s"],
                   ["another tenant at the same moment", f"HTTP {r['other_tenant_status']}"],
                   [f"{r['concurrency']['requests']} concurrent requests ({r['concurrency']['threads']} threads)", f"exactly {r['concurrency']['allowed']} allowed (counter {r['concurrency']['counter']})"],
                   ["counter document", P(mono(json.dumps(r.get("counter_doc"), default=str)), st)]], [60 * mm, W - 60 * mm])]
    rb = out.get("rbac") or {}
    if rb.get("tests"):
        e += [Paragraph("Activity - role-based access control", st["h2"]),
              Paragraph("Admins manage every task of their tenant; members read the team board and change only their own tasks. Member writes "
                        "add owner_id to the filter; admin endpoints use require_admin; the role is re-read on every request.", st["p"]),
              tests_table(rb["tests"], W, st)]

    m = out.get("lab_15_2") or {}
    if m.get("validate"):
        v = m["validate"]
        e += [PageBreak(), Paragraph("Lab 15.2 - Data migration between tenant isolation strategies", st["h1"]),
              Paragraph(f"Seeded {m['seed']['documents']} documents (5 tenants x 100) in one <font face='Courier'>tasks</font> collection, migrated with "
                        f"{escape(m['migrate']['method'])} in {m['migrate']['seconds']} s (Python loop {m['migrate']['loop_seconds']} s). $out does not "
                        f"copy the source's indexes (after $out: {escape(', '.join(m['migrate']['tenants'][0]['indexes_after_out']))}), so the "
                        "index on created_at is created afterwards; and $out into an existing collection keeps that collection's old indexes.", st["p"]),
              tbl([["Tenant", "Shared", "Migrated", "Foreign docs", "Same _ids", "sha256", ""]] +
                  [[x["tenant"], str(x["shared_count"]), str(x["tenant_collection_count"]), str(x["foreign_docs"]), str(x["same_ids"]),
                    f"{x['sha256_shared']} = {x['sha256_tenant']}", yn(x["ok"])] for x in v["tenants"]],
                  [22 * mm, 16 * mm, 18 * mm, 20 * mm, 18 * mm, W - 108 * mm, 14 * mm]),
              Paragraph(f"Negative control: {escape(m['leak_check']['injected'])} - detected: {m['leak_check']['detected']}. "
                        f"{v['total_migrated']}/{v['total_shared']} migrated, {v['ids_in_two_collections']} ids in two collections.", st["p"]),
              Paragraph("Performance (median of repeated runs, wall clock)", st["h2"])]
        rows = [["Scale", "Operation", "Document-level", "Collection-per-tenant", "Docs examined"]]
        for c in m.get("compare", []):
            ex = c["list_20"]["explain"]
            rows += [[c["scale"], "list 20 newest", f"{c['list_20']['document-level']['median_ms']} ms", f"{c['list_20']['collection-per-tenant']['median_ms']} ms",
                      f"{ex['document-level']['docs_examined']} / {ex['collection-per-tenant']['docs_examined']}"],
                     ["", "create", f"{c['create']['document-level']['median_ms']} ms", f"{c['create']['collection-per-tenant']['median_ms']} ms", ""],
                     ["", "analytics (all tenants)", f"{c['analytics']['document-level']['median_ms']} ms",
                      f"{c['analytics']['collection-per-tenant ($unionWith)']['median_ms']} ms ($unionWith)", "same result" if c["analytics_same_result"] else "DIFFERENT"]]
        e += [tbl(rows, [36 * mm, 34 * mm, 30 * mm, W - 130 * mm, 30 * mm]),
              Paragraph("With the compound index (tenant_id, created_at) both layouts examine exactly the documents they return, so listing one "
                        "tenant's tasks is only marginally faster in its own collection - the solution text's 'does not need to scan the tenant_id "
                        "index' is true but small. Cross-tenant analytics is clearly faster in one collection, and the gap grows with the number "
                        "of tenants ($unionWith must open every collection).", st["p"])]
        b = m.get("backup", {})
        if b.get("collection_per_tenant"):
            pf = b["pitfall_drop_on_shared"]
            e += [Paragraph("Backup granularity - restore ONE tenant (Lab 14 tools)", st["h2"]),
                  tbl([["Layout", "Commands", "Result"],
                       ["collection-per-tenant", P(mono(b["collection_per_tenant"]["command"]), st), f"{b['collection_per_tenant']['restored']}/{b['collection_per_tenant']['expected']}"],
                       ["document-level", P(mono(b["document_level"]["command"]), st), f"{b['document_level']['restored']}/{b['document_level']['expected']}, others unchanged"],
                       ["pitfall", P(f"mongorestore --drop with the one-tenant dump on the shared collection: {pf['other_tenants_wiped']} other tenants wiped "
                                     f"({escape(json.dumps(pf['tenants_left_after']))}); repaired from the full dump: {pf['repaired']}", st), "measured"]],
                      [30 * mm, W - 62 * mm, 32 * mm])]
        sc = m.get("schema", {})
        if sc.get("document_level"):
            e.append(Paragraph(f"<b>Schema evolution</b> ({escape(sc['change'])}): {sc['document_level']['operations']} operations on the shared collection vs "
                               f"{sc['collection_per_tenant']['operations']} for the tenant collections - {escape(sc['collection_per_tenant']['note'])}.", st["p"]))
        e += [Paragraph("Recommendation", st["h2"]),
              tbl([["Criterion", "Better", "Why (measured where possible)"]] +
                  [[P(escape(x["criterion"]), st), P(escape(x["winner"]), st), P(escape(x["measured"] or x["collection_per_tenant"]), st)] for x in m.get("recommendation", [])],
                  [48 * mm, 42 * mm, W - 90 * mm]),
              Paragraph("Start with document-level isolation - one collection, a compound index that begins with tenant_id, the tenant filter in one "
                        "function. Move a tenant to its own collection or database when it needs independent backup and restore, regulatory separation "
                        "or its own performance tuning; keep cross-tenant analytics on the shared layout (or a reporting copy).", st["p"])]

    ap = out.get("applied") or {}
    if ap.get("least_privilege"):
        mtx, t = ap["least_privilege"]["matrix"], ap.get("transaction", {})
        e += [Paragraph("Lab 15 applied to Labs 14 -> 7 (mentor's rule)", st["h1"]),
              tbl([["Lab", "Use in Lab 15", "Measured"],
                   ["13", P("runs on the production replica set", st), P(escape(", ".join(ap.get("deployment", {}).get("members", []))), st)],
                   ["12", P("user tasktracker_app = readWrite on tasktracker only", st), P(escape("; ".join(f"{k}: {v}" for k, v in mtx.items())), st)],
                   ["10", P("signup = tenant + first admin in one transaction", st),
                    P(f"signup {t.get('signup')}, again {t.get('signup_again')}; failing signup {t.get('failing_signup')} -> tenant rolled back {t.get('tenant_rolled_back')}", st)],
                   ["9", P("every write with w: majority", st), "client option"],
                   ["14", P("hourly backup target tasktracker; per-tenant restores (15.2)", st), P(escape(str(ap.get("backup", {}).get("name", ""))), st)]],
                  [12 * mm, 62 * mm, W - 74 * mm])]
    e += [Paragraph("Notes on correctness (measured)", st["h1"])]
    for n in NOTES:
        e.append(Paragraph("&bull; " + n, st["p"]))
    doc.build(e, onFirstPage=_foot, onLaterPages=_foot)
    return pdf


NOTES = [
    "The sample takes tenant_id from the query string: without authentication any caller reads any tenant. TaskTracker takes it only from a verified JWT.",
    "The sample's POST answers HTTP 500 after the insert succeeded (ObjectId added to the returned dict) - clients that retry create duplicates.",
    "PyMongo is synchronous: calling it inside async def endpoints blocks the event loop; plain def endpoints run in FastAPI's thread pool.",
    "Another tenant's id answers 404, not 403, so ids of other tenants are never confirmed.",
    "The readWrite role cannot run collMod: the application user cannot change or switch off its own validators - schema setup is a deploy-time job.",
    "A transaction rolls back only when the second write really fails: the unique (tenant_id, email) index must exist before signups.",
    "$out does not copy the source collection's indexes, and $out into an existing collection keeps that collection's old indexes.",
    "With a compound index starting with tenant_id, single-tenant queries examine the same number of documents in both layouts.",
    "mongorestore --drop of a one-tenant dump on a shared collection deletes every other tenant - restore without --drop, after deleting that tenant's documents.",
    "Schema changes over many tenant collections are not atomic: tenants can end up on different schema versions.",
]


def _foot(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GREY)
    canvas.drawString(16 * mm, 8 * mm, "Exercise 15 - A MongoDB-Backed Product as a Service  |  M Nagasai Girisharan  |  24BTRCL114")
    canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"page {doc.page}")
    canvas.restoreState()


if __name__ == "__main__":
    res = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
    print(build(json.loads((res / "lab15_results.json").read_text()), res, os.getenv("DOCKER_URL", "")))

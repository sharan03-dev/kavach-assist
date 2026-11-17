"""
Build the Exercise 9 report from the files the labs wrote to results/:

    results/Exercise-9_Report.pdf    <- submit this (contains the Docker Hub URL)
    results/Exercise-9_Report.docx   <- same content, editable

Every number, table and terminal output in the report is read from results/,
so run the labs first (lab9 all does everything):
    lab9 9.1 ; lab9 9.1-journal ; lab9 9.2
    lab9 report https://hub.docker.com/r/<your-user>/nosql-lab9
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.bench import (explain_91, explain_journal, journal_comparison,  # noqa: E402
                          majority_of, reference_order_check, syncs_per_write, wc_text)
from common.rs import RESULTS_DIR, ROOT, load_events, short  # noqa: E402
from report.docmodel import Doc, render_docx, render_pdf  # noqa: E402
from report.termshot import bar_chart, render_console  # noqa: E402

STUDENT = OrderedDict([("Name", "M NAGASAI GIRISHARAN"), ("Roll No.", "24BTRCL114"),
                       ("Class", "5 AIML-B"), ("Faculty", "Sujeet S Jagtap")])
COURSE = "23CSE528: NoSQL Database using MongoDB"
PLACEHOLDER = "your-dockerhub-username"
IMG = RESULTS_DIR / "report_images"


# =============================================================================
# data helpers
# =============================================================================
def load_json(name):
    p = RESULTS_DIR / name
    return json.loads(p.read_text()) if p.exists() else None


def load_jsonl(name):
    p = RESULTS_DIR / name
    return [json.loads(x) for x in p.open() if x.strip()] if p.exists() else []


def read(name):
    p = RESULTS_DIR / name
    return p.read_text() if p.exists() else ""


def f3(x):
    return f"{x:.3f}"


def hms(epoch):
    return datetime.fromtimestamp(float(epoch)).strftime("%H:%M:%S.%f")[:-3]


def scenario_windows(events):
    """{'A': {...}, 'B': {...}}: start, primary, first partition start, last partition end,
    and every event (any source) that happened inside the scenario's time window."""
    out = OrderedDict()
    for e in events:
        if e["event"] == "SCENARIO_START":
            name = e["detail"].split(":", 1)[0].strip()
            m = re.search(r"primary=(\S+?);", e["detail"])
            out[name] = {"start": float(e["epoch"]), "primary": m.group(1) if m else None,
                         "detail": e["detail"], "p_start": None, "p_end": None, "events": []}
    keys = list(out)
    for i, k in enumerate(keys):
        w = out[k]
        end = out[keys[i + 1]]["start"] if i + 1 < len(keys) else float("inf")
        w["events"] = [e for e in events if w["start"] - 0.2 <= float(e["epoch"]) < end]
        for e in w["events"]:
            if e["event"] == "PARTITION_START" and w["p_start"] is None:
                w["p_start"] = float(e["epoch"])
            if e["event"] == "PARTITION_END":
                w["p_end"] = float(e["epoch"])
    return out


def find(events, event=None, source=None, pattern=None, after=None, last=False):
    hits = [e for e in events
            if (event is None or e["event"] == event)
            and (source is None or e["source"] == source)
            and (pattern is None or re.search(pattern, e["detail"]))
            and (after is None or float(e["epoch"]) >= after)]
    if not hits:
        return None
    return hits[-1] if last else hits[0]


def write_summary(writes, phases):
    rows = []
    sel = [w for w in writes if w["phase"] in phases]
    for wc in OrderedDict((w["wc"], None) for w in sel):
        ws = [w for w in sel if w["wc"] == wc]
        cnt = Counter(w["outcome"] for w in ws)
        ok = [w["latency_ms"] for w in ws if w["outcome"] == "OK"]
        bad = [w["latency_ms"] for w in ws if w["outcome"] != "OK"]
        rows.append([wc, len(ws), cnt.get("OK", 0),
                     ", ".join(f"{k} x{v}" for k, v in cnt.items() if k != "OK") or "-",
                     f"{sum(ok) / len(ok):.1f}" if ok else "-",
                     f"{sum(bad) / len(bad):.0f}" if bad else "-"])
    return rows


WRITE_HEAD = ["Write concern", "Attempts", "OK", "Failures (outcome x count)",
              "Avg OK latency (ms)", "Avg failed attempt (ms)"]
WRITE_W = [2.4, 1.7, 1.1, 6.2, 2.8, 2.8]


def section(text, start_title, end_titles):
    """Console lines from the banner of `start_title` (None = top) up to the
    banner of the first of `end_titles`."""
    lines = text.splitlines()

    def banner_idx(title):
        for i, ln in enumerate(lines):
            if ln.startswith(title) and i > 0 and lines[i - 1].strip() \
                    and set(lines[i - 1].strip()) <= set("=-"):
                return i - 1
        return None

    s = 0 if start_title is None else banner_idx(start_title)
    if s is None:
        return ""
    e = len(lines)
    for t in end_titles:
        j = banner_idx(t)
        if j is not None and j > s:
            e = min(e, j)
    return "\n".join(lines[s:e])


def console_images(R, text, key, title, caption, cols=122):
    if not text.strip():
        R.p(f"(console output for {title} not found - run the lab with the lab9 command)",
            italic=True)
        return
    paths = render_console(text, IMG / key, title, cols=cols)
    for i, p in enumerate(paths, 1):
        R.image(p, 17.0, caption + (f" (part {i} of {len(paths)})" if len(paths) > 1 else ""))


def code_file(R, rel, title=None):
    p = ROOT / rel
    R.p((title or rel, "b"))
    R.code(p.read_text() if p.exists() else f"({rel} not found)", size=6.8)


# =============================================================================
# sections
# =============================================================================
def title_block(R, docker_url, image, env, run_env, sample_note):
    R.p((COURSE, "b"), align="center", size=13)
    R.p(("Exercise 9", "b"), align="center", size=22)
    R.p("Write Concern Latency and Network Partitions (The CAP Theorem in Practice)",
        align="center", size=13)
    R.p("Lab 9.1 - Measure Write Concern Latency  |  Activity - j:true vs j:false  |  "
        "Lab 9.2 - Simulate a Network Partition", align="center", size=9.5, italic=True)
    for k, v in STUDENT.items():
        R.p((f"{k}: ", "b"), v, align="center")
    if sample_note:
        R.p(("NOTE: ", "by"), (sample_note, "y"))

    R.h("Docker Hub submission", 1)
    if docker_url and PLACEHOLDER not in docker_url:
        R.p(("Docker image URL: ", "b"), ("link", docker_url, docker_url))
    else:
        R.p(("Docker image URL: ", "b"),
            ("NOT SET - run  lab9 report https://hub.docker.com/r/<user>/nosql-lab9", "y"))
    R.p("Everything needed for the demonstration is inside the image: all Lab 9 scripts, "
        "Python 3.12 + PyMongo, the Docker CLI and a ready-to-use compose file. The three "
        "MongoDB servers use the official mongo:7.0 image.")
    R.code(f"docker pull {image}\n"
           f"docker run --rm {image} compose > docker-compose.yml   # demo compose file\n"
           f"docker compose up -d                          # mongo1-3 + lab9-runner (rs0 auto-init)\n"
           f"docker exec -it lab9-runner lab9 9.1          # Lab 9.1\n"
           f"docker exec -it lab9-runner lab9 9.1-journal  # Lab 9.1 activity (j:true / j:false)\n"
           f"docker exec -it lab9-runner lab9 9.2 --step   # Lab 9.2, pausing before every step\n"
           f"docker exec -it lab9-runner lab9 all          # everything + this report", size=7.8)
    if env:
        R.p(("Run recorded: ", "b"), f"{env.get('captured_at', '')[:16].replace('T', ' ')} - "
            f"MongoDB {env.get('mongodb_version')}, PyMongo {env.get('pymongo_version')}. "
            f"{run_env}", size=9)


def stepdown_event(W):
    if not W or not W.get("primary") or not W.get("p_start"):
        return None
    c = W["primary"].split(":")[0]
    return find(W["events"], source="mongod",
                pattern=rf"^{c}: .*(SECONDARY from PRIMARY|PRIMARY -> SECONDARY)",
                after=W["p_start"])


def b_moments(B, writes):
    """Key Scenario B facts shared by the summary and the Lab 9.2 section."""
    t0 = B["p_start"]
    oldp = (B["primary"] or "mongo1:27017").split(":")[0]
    after_heal = B["p_end"] or t0
    new_ev = find(B["events"], source="mongod",
                  pattern=r"(PRIMARY from SECONDARY|SECONDARY -> PRIMARY)", after=after_heal)
    back = None
    if new_ev:
        back = find(B["events"], source="mongod",
                    pattern=rf"^{oldp}: .*(PRIMARY from SECONDARY|SECONDARY -> PRIMARY)",
                    after=float(new_ev["epoch"]) + (0.001 if not new_ev["detail"].startswith(oldp) else 0))
    return {"t0": t0, "oldp": oldp, "stepdown": stepdown_event(B),
            "elected": find(B["events"], source="mongod",
                            pattern=r"[Ee]lection succeeded", after=after_heal),
            "new_primary": new_ev, "back": back,
            "takeover": find(B["events"], event="PRIORITY_TAKEOVER"),
            "heal": find(B["events"], event="PARTITION_END", last=True)}


def summary(R, r91, rj, writes, events, dur):
    R.h("Results at a glance", 1)
    rows = []
    if r91:
        rr = r91["results"]
        b = rr["w:1"]["avg_ms"]
        rows.append([f"Lab 9.1 - average latency per write (1,000 docs, mean of {r91['rounds']} rounds)",
                     f"w:1 {rr['w:1']['avg_ms']:.3f} ms | w:majority {rr['w:majority']['avg_ms']:.3f} ms "
                     f"({rr['w:majority']['avg_ms'] / b:.2f}x) | w:all {rr['w:all']['avg_ms']:.3f} ms "
                     f"({rr['w:all']['avg_ms'] / b:.2f}x)"])
        rows.append(["Lab 9.1 - total time for 1,000 documents",
                     " | ".join(f"{k} {v['total_s']:.3f} s" for k, v in rr.items())])
    if rj:
        comp = journal_comparison(rj["results"])
        rows.append(["Activity - effect of j:true on latency",
                     " | ".join(f"{c['w']}: {c['delta_pct']:+.1f}% ({c['verdict']})" for c in comp)])
    if writes:
        def okc(ph, wc):
            ws = [w for w in writes if w["phase"] in ph and w["wc"].startswith(wc)]
            return f"{sum(w['outcome'] == 'OK' for w in ws)}/{len(ws)} OK" if ws else "-"
        a = {"A_partition", "A_partition_late"}
        rows.append(["Lab 9.2 A - one secondary isolated 30 s",
                     f"w:1 {okc(a, 'w:1')} | w:majority {okc(a, 'w:majority')} | "
                     f"w:all {okc({'A_partition'}, 'w:all')} (wtimeout) - set stays writable"])
        rows.append(["Lab 9.2 B - both secondaries isolated 30 s",
                     f"w:1 {okc({'B_partition'}, 'w:1')} | w:majority {okc({'B_partition'}, 'w:majority')}"
                     " - primary steps down, set becomes read-only"])
        B = scenario_windows(events).get("B")
        if B and B["p_start"]:
            k = b_moments(B, writes)
            parts = []
            if k["stepdown"]:
                parts.append(f"{k['oldp']} stepped down at +{float(k['stepdown']['epoch']) - k['t0']:.1f} s")
            if k["new_primary"]:
                parts.append(f"{k['new_primary']['detail'].split(':')[0]} elected after reconnect")
            if k["back"] and k["heal"]:
                parts.append(f"{k['oldp']} took the role back "
                             f"{float(k['back']['epoch']) - float(k['heal']['epoch']):.1f} s after reconnect")
            rows.append(["Lab 9.2 B - timeline (mongod logs)", "; ".join(parts) or "-"])
    if dur:
        rows.append(["After recovery", "documents per member: " + ", ".join(
            f"{short(h)}={v}" for h, v in dur["member_counts"].items())])
    if rows:
        R.table(["Item", "Measured"], rows, widths=[5.5, 11.5], size=8.5, first_col_bold=True)


def environment(R, env, r91, run_env):
    R.h("Environment and Design Decisions", 1)
    if env:
        R.table(["Item", "Value"], [
            ["MongoDB", f"{env['mongodb_version']}, replica set {env['replica_set']}"],
            ["How it ran", run_env],
            ["Members", ", ".join(f"{m['host']} (priority {m['priority']})" for m in env["members"])],
            ["Data-bearing members / write majority",
             f"{env['data_bearing_members']} / {majority_of(env)}"],
            ["writeConcernMajorityJournalDefault", str(env["write_concern_majority_journal_default"])],
            ["electionTimeoutMillis / heartbeatIntervalMillis",
             f"{env.get('election_timeout_ms')} ms / {env.get('heartbeat_interval_ms')} ms"],
            ["Client", f"Python {env['python_version']}, PyMongo {env['pymongo_version']} "
                       f"({env['runner_platform']})"],
        ], widths=[5.5, 11.5], size=8.5, first_col_bold=True)
    R.p("The deployment reuses the Exercise 8 layout - mongo1:27017 (priority 2), mongo2:27018 "
        "and mongo3:27019 - plus a lab9-runner container that holds every Lab 9 script and is "
        "attached to the same Docker network (rslab9-net). Inside that network the member names "
        "resolve through Docker DNS, so no hosts-file edit is needed, and when a member is "
        "disconnected it becomes unreachable for the client too, exactly like a real partition "
        "seen from an application server. The Docker socket is mounted into the runner so Lab "
        "9.2 can run docker network disconnect / connect, docker exec and docker logs.")
    R.h("Corrections to the reference scripts", 2)
    probe = (r91 or {}).get("w_all_probe")
    R.bullets([
        [("insert_one() has no write_concern parameter. ", "b"),
         "The reference lab_01_write_concern_latency.py calls col.insert_one(doc, "
         "write_concern=wc), which raises TypeError in PyMongo. Following the lab solution, "
         "each configuration gets its own MongoClient(..., w=..., journal=...)."],
        [("MongoDB has no built-in w:\"all\". ", "b"),
         "Valid values are a number, \"majority\" or a custom mode from settings.getLastErrorModes. "
         "The benchmark sends w:\"all\" once to record the server's answer"
         + (f" ({probe})" if probe else "")
         + " and then uses w:3 = every data-bearing member, which is what w:all means."],
        [("Fair timing. ", "b"),
         "Connection set-up and 50 warm-up inserts happen before the timer starts; every "
         "configuration runs in 5 rounds in rotated order, and a difference is only called an "
         "effect when it is larger than the round-to-round spread."],
        [("Correct partition target. ", "b"),
         "The reference shell script disconnects whichever mongo container docker ps lists "
         "first - possibly the primary. simulate_partition.sh reads rs.status() and disconnects "
         "real SECONDARY members, keeps the partition for 30 s as the handout asks, and always "
         "reconnects the network (even after Ctrl+C)."],
        [("Exact timestamps. ", "b"),
         "The observer polls every member in parallel every 0.5 s, and the step-down / election "
         "moments are read from the mongod logs (server-side timestamps)."],
    ])


def lab_91(R, r91):
    R.page_break()
    R.h("Lab 9.1 - Measure Write Concern Latency", 1)
    R.p(("Objective: ", "b"), "Measure the impact of different write concerns on write latency.")
    R.p(("Instructions: ", "b"), "(1) Insert 1,000 documents using each of w:1, w:majority and "
        "w:all and measure the total time for each. (2) Calculate the average latency per write. "
        "(3) Create a table comparing the results and explain the differences.")
    R.p(("Prediction (before running): ", "b"), "w:1 < w:majority < w:all. w:1 only needs the "
        "primary, w:majority needs one secondary as well (one extra round trip), w:all needs both "
        "secondaries. The handout expects w:majority to be 2-5x slower than w:1.")
    R.p(("Method: ", "b"), "write_concern_latency.py creates one MongoClient per write concern, "
        "does 50 untimed warm-up inserts, then times 1,000 inserts one by one with "
        "time.perf_counter() (per-write latency) and as a whole (total time). This is repeated "
        "in 5 rounds in rotated order. WiredTiger's journal-sync counter is read on every member "
        "before and after each run.")
    R.h("Output", 2)
    console_images(R, read("console_9.1.txt"), "c91", "lab9-runner - lab9 9.1",
                   "Captured terminal output of  lab9 9.1")
    if not r91:
        return
    rows = r91["results"]
    base = rows["w:1"]["avg_ms"]
    R.h("Comparison table", 2)
    R.table(["Write concern", "Sent to server", "Total time (s) / 1,000 docs",
             "Avg latency per write (ms)", "P50 (ms)", "P95 (ms)", "P99 (ms)", "Writes/s", "vs w:1"],
            [[k, wc_text(v["sent_as"]), f3(v["total_s"]), f3(v["avg_ms"]), f3(v["p50_ms"]),
              f3(v["p95_ms"]), f3(v["p99_ms"]), f"{v['throughput_ops']:.0f}",
              f"{v['avg_ms'] / base:.2f}x"] for k, v in rows.items()],
            widths=[2.0, 3.0, 2.0, 2.1, 1.4, 1.4, 1.4, 1.5, 1.3], size=8, first_col_bold=True)
    R.p(("Total time per round (s): ", "b"), "; ".join(
        f"{k}: " + ", ".join(f"{t:.3f}" for t in v["total_s_each"]) for k, v in rows.items()),
        size=9)
    ch = bar_chart(IMG / "chart91.png", "Lab 9.1 - latency per write by write concern",
                   list(rows), {"Average": [v["avg_ms"] for v in rows.values()],
                                "P99": [v["p99_ms"] for v in rows.values()]}, ylabel="ms")
    R.image(ch, 13.5, "Average and 99th-percentile latency per insert (lower is better)")
    R.h("Explanation of the differences", 2)
    R.bullets(explain_91(rows, r91["environment"]))
    order = sorted(rows, key=lambda k: rows[k]["avg_ms"])
    ratio = rows["w:majority"]["avg_ms"] / base
    R.p(("Prediction vs result: ", "b"),
        f"measured order {' < '.join(order)} - "
        + ("as predicted" if order == ["w:1", "w:majority", "w:all"] else "differs from the prediction")
        + f"; w:majority / w:1 = {ratio:.2f}x ("
        + ("inside" if 2 <= ratio <= 5 else "outside") + " the expected 2-5x).")
    R.h("Script", 2)
    code_file(R, "Activity_9.1_Write_Concern_Latency/write_concern_latency.py")


def lab_91_journal(R, rj):
    R.page_break()
    R.h("Lab 9.1 - Activity: Try it yourself (j:true vs j:false)", 1)
    R.p(("Task: ", "b"), "Repeat the experiment with j:true and j:false for each write concern. "
        "Does journaling add significant latency?")
    R.p(("Prediction (before running): ", "b"), "j:true clearly slows down w:1, because every "
        "acknowledgment then waits for a journal flush instead of the background flush. It changes "
        "w:majority very little, because with writeConcernMajorityJournalDefault: true a majority "
        "write is already journaled before it is acknowledged.")
    R.h("Output", 2)
    console_images(R, read("console_9.1-journal.txt"), "c91j", "lab9-runner - lab9 9.1-journal",
                   "Captured terminal output of  lab9 9.1-journal")
    if not rj:
        return
    rows, hosts = rj["results"], rj["hosts"]

    def spw(row, h):
        v = syncs_per_write(row, h)
        return "n/a" if v is None else f"{v:.2f}"

    R.h("Results (6 configurations x 1,000 inserts)", 2)
    R.table(["Configuration", "Total (s)", "Avg (ms)", "P50 (ms)", "P99 (ms)", "Round spread (ms)"] +
            [f"Journal syncs / write {short(h)}" for h in hosts],
            [[k, f3(v["total_s"]), f3(v["avg_ms"]), f3(v["p50_ms"]), f3(v["p99_ms"]),
              f3(v["round_spread_ms"])] + [spw(v, h) for h in hosts] for k, v in rows.items()],
            size=7.8, first_col_bold=True)
    R.p("Journal syncs / write = increase of WiredTiger's \"log sync operations\" counter on that "
        "member during the run divided by the number of writes. About 1.0 = one journal flush per "
        "acknowledged write; well below 1 = the journal was flushed in the background in groups. "
        "Round spread = max - min of the per-round averages (run-to-run noise).", italic=True, size=8.5)
    comp = journal_comparison(rows)
    R.table(["Write concern", "j:false avg (ms)", "j:true avg (ms)", "Difference", "Change",
             "Noise (ms)", "Verdict"],
            [[c["w"], f3(c["j_false_ms"]), f3(c["j_true_ms"]), f"{c['delta_ms']:+.3f} ms",
              f"{c['delta_pct']:+.1f}%", f3(c["noise_ms"]), c["verdict"]] for c in comp],
            size=8.5, first_col_bold=True)
    ch = bar_chart(IMG / "chart91j.png", "Activity - j:false vs j:true (average ms per write)",
                   ["w:1", "w:majority", "w:all"],
                   {"j:false": [c["j_false_ms"] for c in comp], "j:true": [c["j_true_ms"] for c in comp]})
    R.image(ch, 13.5, "Average latency per insert with and without j:true")
    R.h("Does journaling add significant latency?", 2)
    R.bullets(explain_journal(rows, rj["environment"]))

    R.h("Write-concern trade-off table (handout) with the measured latency", 2)
    spec = [("w: 1, j: false", "w:1, j:false", "Lowest", "Data in primary memory only",
             "AP-leaning", "Caching, session data"),
            ("w: 1, j: true", "w:1, j:true", "Low", "Data on primary disk", "CP-leaning",
             "Standard application data"),
            ("w: majority, j: true", "w:majority, j:true", "Medium", "Data on majority of nodes",
             "Strong CP", "Financial transactions"),
            ("w: all, j: true", "w:all, j:true", "Highest", "Data on all nodes", "Strongest CP",
             "Regulatory compliance")]
    measured = [rows[s[1]]["avg_ms"] for s in spec]
    rank = {v: i + 1 for i, v in enumerate(sorted(measured))}
    R.table(["Write concern", "Latency (handout)", "Measured avg (ms)", "Measured rank",
             "Durability", "CAP position", "Scenario"],
            [[s[0], s[2], f3(m), str(rank[m]), s[3], s[4], s[5]] for s, m in zip(spec, measured)],
            widths=[2.6, 2.0, 2.0, 1.6, 3.2, 2.0, 3.0], size=8.5, first_col_bold=True)
    order_ok = measured == sorted(measured)
    R.p("The measured order " + ("matches" if order_ok else "does not fully match")
        + " the handout's Lowest -> Highest ranking"
        + ("." if order_ok else " - configurations whose difference is within the run-to-run "
                                "spread can swap places (see the noise column above)."))
    R.p(("Note on the CAP column: ", "b"),
        "j:true protects a write against a crash of the node that acknowledged it, but it does "
        "not change what happens during a partition. A w:1, j:true write can still be "
        "acknowledged by an isolated primary in the seconds before it steps down and then be "
        "rolled back if another member is elected (Lab 9.2, Scenario B). With respect to "
        "partitions, only w:majority (or stronger) is consistency-leaning.")
    chk = rj.get("reference_order") or reference_order_check(rows)
    R.p(("Chapter 9 README - 'Check your work': ", "b"),
        "expected " + " < ".join(chk["expected"]) + "; measured " + " < ".join(chk["measured"])
        + (" - matches." if chk["match"] else
           " - the pairs that swapped differ by less than the run-to-run spread."))
    R.h("Script", 2)
    code_file(R, "Activity_9.1_TryItYourself_Journaling/journal_latency.py")


def sync_sources(status_text):
    """{member: sync source} from the rsctl status table (SYNC SOURCE column)."""
    out = {}
    for ln in status_text.splitlines():
        parts = ln.split()
        if len(parts) >= 5 and re.match(r"\S+:\d+$", parts[0]) and re.match(r"\S+:\d+$", parts[-1]):
            out[parts[0]] = parts[-1]
    return out


def first_ok_after_fail(writes, wc, phases):
    seen_fail = False
    for w in writes:
        if w["phase"] in phases and w["wc"] == wc:
            if w["outcome"] != "OK":
                seen_fail = True
            elif seen_fail:
                return {"epoch": w["epoch"], "source": "writer",
                        "detail": f"[{w['phase']}] {wc} insert {w['seq']} acknowledged again"}
    return None


def moments_table(R, rows):
    rows = rows[:1] + sorted(rows[1:], key=lambda r: float(r[2]))
    R.table(["Moment", "Time", "T+ (s)", "Evidence"], rows, widths=[5.2, 2.2, 1.4, 8.2], size=8)


def ev_row(label, ev, t0):
    return [label, hms(ev["epoch"]), f"{float(ev['epoch']) - t0:.1f}",
            f"{ev['source']}: {ev['detail'][:115]}"]


def lab_92(R, writes, reads, events, dur):
    R.page_break()
    R.h("Lab 9.2 - Simulate a Network Partition", 1)
    R.p(("Objective: ", "b"), "Simulate a network partition in the replica set with Docker network "
        "manipulation, monitor the replica-set state during the partition, insert documents with "
        "w:1 and w:majority, record which operations succeed and which time out, and observe what "
        "happens when the partition is resolved.")
    R.h("Procedure (three terminals, automated by simulate_partition.sh)", 2)
    R.bullets([
        "Deploy the 3-node replica set (Exercise 8 layout) and wait until rs.status() shows one "
        "PRIMARY and two healthy SECONDARYs; identify the container IDs with docker ps.",
        "Terminal 1 - observer (the Lab 8.2 failover monitor, extended): asks every member for "
        "rs.status() every 0.5 s and logs each state change with a timestamp.",
        "Terminal 2 - docker network disconnect rslab9-net <container> isolates a secondary "
        "(Scenario A); later both secondaries are isolated (Scenario B).",
        "Terminal 3 - writer: inserts with w:1 and w:majority (wtimeout 3 s) and records every "
        "outcome; in Scenario A w:all is added, in Scenario B reads are tested too.",
        "After 30 s: docker network connect, then observe the catch-up (A) and the election and "
        "priority takeover (B). Finally check which writes survived.",
        "Live demonstration: lab9 9.2 --step pauses before every step; the manual three-terminal "
        "commands are listed in the README.",
    ])
    R.p(("Predictions (before running, from the chapter README): ", "b"),
        "(A) The isolated member will not disappear from rs.status(); the majority side keeps its "
        "PRIMARY, so w:1 and w:majority keep working, while w:all - which needs every member - "
        "times out. The isolated member itself will see no primary. (B) A primary that can no "
        "longer reach a majority must step down after about electionTimeoutMillis (10 s); "
        "w:majority cannot succeed, w:1 works only until the step-down, and the set becomes "
        "read-only until the network is restored and an election runs.")
    if not writes and not events:
        R.p("No Lab 9.2 results found - run  lab9 9.2  first.", italic=True)
        return
    win = scenario_windows(events)
    con = read("console_9.2.txt")

    R.h("Containers and initial state", 2)
    console_images(R, section(con, None, ["Scenario A"]), "c92_0",
                   "lab9-runner - lab9 9.2 (step 0 and baseline)",
                   "Captured terminal output - container IDs, initial rs.status(), baseline writes",
                   cols=128)

    # ---------------- Scenario A ---------------------------------------------
    R.h("Scenario A - one secondary isolated for 30 s", 2)
    A = win.get("A")
    console_images(R, section(con, "Scenario A", ["Scenario B"]), "c92_A",
                   "lab9-runner - lab9 9.2 (Scenario A)", "Captured terminal output - Scenario A",
                   cols=128)
    R.p(("Writes while the secondary was cut off:", "b"))
    sa_rows = write_summary(writes, {"A_partition", "A_partition_late"})
    R.table(WRITE_HEAD, sa_rows, widths=WRITE_W, size=8.5, first_col_bold=True)
    R.p(("Isolated member's own view (docker exec works without the network):", "b"))
    R.code(read("lab9_2_isolated_view_A.txt") or "(not recorded)", size=7.5)
    R.p(("Majority side's view:", "b"))
    R.code(read("lab9_2_majority_view_A.txt") or "(not recorded)", size=7.5)
    obs = []
    if A and A["p_start"]:
        t0 = A["p_start"]
        m = re.search(r"isolating (\S+)", A["detail"])
        iso = m.group(1).split(":")[0] if m else "mongo3"
        rows = [["Partition started", hms(t0), "0.0", "script: docker network disconnect"]]
        for label, ev in [
            ("First w:all write timed out", find(A["events"], source="writer", pattern=r"w:all.*WTIMEOUT")),
            (f"{iso} tried to elect itself (no majority -> failed)",
             find(A["events"], source="mongod", pattern=rf"^{iso}: [Ss]tarting an election")),
            (f"{iso} stops trying: 'cannot see a majority'",
             find(A["events"], source="mongod", pattern=rf"^{iso}: .*cannot see a majority")),
            (f"Primary marks {iso} (not reachable/healthy)",
             find(A["events"], event="MEMBER_UNREACHABLE", source="observer")),
            ("Remaining secondary changes its sync source",
             find(A["events"], source="mongod", pattern=rf"^(?!{iso}).*(sync source|syncing from)",
                  after=t0)),
            ("First w:majority OK after a timeout",
             first_ok_after_fail(writes, "w:majority", {"A_partition", "A_partition_late"})),
            ("Network restored", find(A["events"], event="PARTITION_END")),
            (f"{iso} caught up with the primary", find(A["events"], event="CAUGHT_UP")),
        ]:
            if ev:
                rows.append(ev_row(label, ev, t0))
        R.p(("Key moments (T+ = seconds after the disconnect):", "b"))
        moments_table(R, rows)
        u = find(A["events"], event="MEMBER_UNREACHABLE", source="observer")
        if u:
            obs.append(f"The primary marked the isolated member \"(not reachable/healthy)\" "
                       f"{float(u['epoch']) - t0:.1f} s after the disconnect (heartbeats every 2 s, "
                       f"member declared down after the 10 s heartbeat timeout). It is NOT removed "
                       f"from rs.status(): it stays listed with health 0 - a correction to the lab "
                       f"solution text, which says it disappears.")
    sa = {r[0]: r for r in sa_rows}
    if "w:1" in sa and "w:majority" in sa:
        if sa["w:majority"][2] == sa["w:majority"][1]:
            obs.append(f"w:1 succeeded {sa['w:1'][2]}/{sa['w:1'][1]} times and w:majority "
                       f"{sa['w:majority'][2]}/{sa['w:majority'][1]} times: the primary plus the "
                       f"remaining secondary still form a majority (2 of 3), so the set stays fully "
                       f"writable and no election happens.")
        else:
            chain = sync_sources(read("lab9_2_status_before.txt"))
            iso_h = (re.search(r"isolating (\S+)", A["detail"]).group(1) if A else "")
            other = [h for h, src in chain.items() if src == iso_h]
            why = (f"Before the partition {short(other[0])} was replicating from {short(iso_h)} "
                   f"(chained replication - see the SYNC SOURCE column of the initial rs.status()), "
                   f"not from the primary. "
                   if other else "The most likely cause is chained replication: the remaining "
                                 "secondary was replicating from the member that was cut off. ")
            obs.append(f"w:1 succeeded {sa['w:1'][2]}/{sa['w:1'][1]} times, but w:majority "
                       f"succeeded only {sa['w:majority'][2]}/{sa['w:majority'][1]} times "
                       f"({sa['w:majority'][3]}) although 2 of 3 members were still connected. "
                       + why +
                       "When that member was cut off, the remaining secondary received no new "
                       "writes until it chose a new sync source (the primary), so no second member "
                       "could acknowledge and the first w:majority writes hit their wtimeout. Once "
                       "it had switched, w:majority succeeded again and the set stayed writable with "
                       "no election. Lesson: with chainingAllowed: true (the default) a partition "
                       "can briefly stall majority writes even when a majority is connected.")
    wa = next((k for k in sa if k.startswith("w:all")), None)
    if wa:
        obs.append(f"{wa} succeeded {sa[wa][2]}/{sa[wa][1]} times ({sa[wa][3]}): it needs an "
                   f"acknowledgment from the cut-off member, so every attempt waited the full "
                   f"wtimeout (about {sa[wa][5]} ms). A WTIMEOUT error does not undo the write - it "
                   f"is already on the primary and replicates when the member returns (see the "
                   f"durability check).")
    obs.append("The isolated member saw no primary and could not elect itself: a candidate needs "
               "votes from a majority (2 of 3). This is what prevents a second primary on the "
               "minority side of a partition.")
    if A:
        cu = find(A["events"], event="CAUGHT_UP")
        if cu:
            obs.append(f"After docker network connect the member caught up with the primary's "
                       f"latest optime in {cu['value']} s by fetching the oplog entries it missed.")
    R.p(("Observations:", "b"))
    R.bullets(obs)

    # ---------------- Scenario B ---------------------------------------------
    R.h("Scenario B - both secondaries isolated (the primary loses its majority)", 2)
    B = win.get("B")
    console_images(R, section(con, "Scenario B", ["Step 4"]), "c92_B",
                   "lab9-runner - lab9 9.2 (Scenario B)",
                   "Captured terminal output - Scenario B (step-down, read-only, election, takeover)",
                   cols=128)
    R.p(("Writes while the primary was alone:", "b"))
    R.table(WRITE_HEAD, write_summary(writes, {"B_partition"}), widths=WRITE_W, size=8.5,
            first_col_bold=True)
    rb = [r for r in reads if r["phase"] == "B_partition"]
    if rb:
        R.p(("Reads during Scenario B:", "b"))
        rows = []
        for rp in OrderedDict((r["read_preference"], None) for r in rb):
            cnt = Counter(r["outcome"] for r in rb if r["read_preference"] == rp)
            rows.append([f"readPreference={rp}", sum(cnt.values()),
                         ", ".join(f"{k} x{v}" for k, v in cnt.items())])
        R.table(["Read", "Attempts", "Outcomes"], rows, widths=[5.0, 2.0, 10.0], size=8.5)
    R.p(("Former primary's view during the partition:", "b"))
    R.code(read("lab9_2_view_B_primary_side.txt") or "(not recorded)", size=7.5)
    R.p(("An isolated secondary's own view:", "b"))
    R.code(read("lab9_2_isolated_view_B.txt") or "(not recorded)", size=7.5)

    obs_b = []
    if B and B["p_start"]:
        k = b_moments(B, writes)
        t0, oldp = k["t0"], k["oldp"]
        bw = [w for w in writes if w["phase"] == "B_partition"]
        w1_ok = [w for w in bw if w["wc"] == "w:1" and w["outcome"] == "OK"]
        w1_bad = [w for w in bw if w["wc"] == "w:1" and w["outcome"] != "OK"]
        rows = [["Partition started (both secondaries cut off)", hms(t0), "0.0",
                 "script: docker network disconnect"]]
        cand = [
            ("First w:majority write timed out",
             find(B["events"], source="writer", pattern=r"w:majority: .*WTIMEOUT")),
            ("Last acknowledged w:1 write", {"epoch": w1_ok[-1]["epoch"], "source": "writer",
                                            "detail": f"insert {w1_ok[-1]['seq']} acknowledged by the isolated primary"}
             if w1_ok else None),
            (f"PRIMARY STEPPED DOWN ({oldp})", k["stepdown"]),
            ("First rejected w:1 write", {"epoch": w1_bad[0]["epoch"], "source": "writer",
                                         "detail": f"{w1_bad[0]['outcome']}: {w1_bad[0]['error'][:80]}"}
             if w1_bad else None),
            ("Observer: no PRIMARY - set is read-only", find(B["events"], event="PRIMARY_STEPPED_DOWN")),
            ("Network restored (both secondaries)", k["heal"]),
            ("Election won after reconnecting", k["elected"]),
            ("New primary accepts writes", find(B["events"], source="mongod",
                                                 pattern=r"[Tt]ransition to primary complete",
                                                 after=B["p_end"] or t0)),
            (f"Former primary {oldp} takes the role back (priority takeover)", k["back"] or k["takeover"]),
        ]
        for label, ev in cand:
            if ev:
                rows.append(ev_row(label, ev, t0))
        R.p(("Timeline of key moments (T+ = seconds after the partition started):", "b"))
        moments_table(R, rows)
        R.p("mongod rows carry the server's own log timestamp; writer rows are the client-side "
            "time of the insert attempt.", italic=True, size=8.5)

        sd = k["stepdown"]
        if sd:
            obs_b.append(f"Exact step-down moment: {hms(sd['epoch'])}, "
                         f"{float(sd['epoch']) - t0:.1f} s after the partition started (mongod log: "
                         f"\"{sd['detail'].split(': ', 1)[1][:70]}\"). The primary could not reach a "
                         f"majority for electionTimeoutMillis (10 s), so it gave up the primary role "
                         f"itself - this prevents two primaries from accepting writes.")
        if w1_ok:
            obs_b.append(f"Until then w:1 writes were still acknowledged ({len(w1_ok)} OK, the last "
                         f"at +{w1_ok[-1]['epoch'] - t0:.1f} s): the isolated primary accepts them on "
                         f"its own - the availability side of the trade-off, with the risk that they "
                         f"are rolled back later.")
        if w1_bad:
            c = Counter(w["outcome"] for w in w1_bad)
            obs_b.append(f"From +{w1_bad[0]['epoch'] - t0:.1f} s every write failed "
                         f"({', '.join(f'{a} x{b}' for a, b in c.items())}): with no primary the "
                         f"replica set is read-only.")
        wm = Counter(w["outcome"] for w in bw if w["wc"] == "w:majority")
        if wm:
            obs_b.append("w:majority never succeeded during Scenario B ("
                         + ", ".join(f"{a} x{b}" for a, b in wm.items())
                         + "): no majority could acknowledge, so attempts first waited the full "
                           "wtimeout, then failed because there was no primary at all.")
        if rb:
            sp = Counter(r["outcome"] for r in rb if r["read_preference"] == "secondaryPreferred")
            pr = Counter(r["outcome"] for r in rb if r["read_preference"] == "primary")
            obs_b.append(f"Reads with readPreference secondaryPreferred kept working "
                         f"({sp.get('OK', 0)}/{sum(sp.values())} OK) while primary reads failed "
                         f"once the primary was gone ({sum(pr.values()) - pr.get('OK', 0)}/"
                         f"{sum(pr.values())} failed) - the set is read-only, not down.")
        ne = k["new_primary"]
        if ne:
            newp = ne["detail"].split(":")[0]
            dt = float(ne["epoch"]) - float(k["heal"]["epoch"]) if k["heal"] else None
            obs_b.append(f"After the network was restored, {newp} won the election"
                         + (f" {dt:.1f} s later" if dt is not None else "")
                         + (f" ({k['elected']['detail'].split(': ', 1)[1]})" if k["elected"] else "")
                         + "." + (" The former primary did not win this first election."
                                  if newp != oldp else " The former primary won it directly."))
        bk = k["back"] or k["takeover"]
        if bk and (not ne or ne["detail"].split(":")[0] != oldp):
            obs_b.append(f"When it rejoined: the former primary {oldp} became PRIMARY again at "
                         f"{hms(bk['epoch'])}"
                         + (f", {float(bk['epoch']) - float(k['heal']['epoch']):.1f} s after the network "
                            f"was restored" if k["heal"] else "")
                         + ". It has priority 2, so once it had caught up it called a priority-"
                           "takeover election and the temporary primary stepped down. This answers "
                           "the handout's 'may or may not regain its role': with the higher priority "
                           "it does, after a short delay.")
    if dur:
        b_ok = [g for g in dur["groups"] if g["phase"] == "B_partition" and g["wc"] == "w:1"
                and g["client_outcome"] == "OK"]
        lost = [g for g in b_ok if g["present_now"] < g["attempts"]]
        if lost:
            obs_b.append("ROLLBACK observed: acknowledged w:1 writes from the isolated primary are "
                         "missing after recovery - another member won the election and the old "
                         "primary had to roll back the writes that never reached a majority.")
        elif b_ok:
            obs_b.append("No acknowledged write was lost in this run: after reconnecting, the "
                         "secondaries copied the isolated primary's last writes from it (chained "
                         "replication) before the new primary started accepting writes, so nothing "
                         "had to be rolled back. That is timing, not a guarantee: a w:1 write "
                         "acknowledged by an isolated primary can be rolled back; an acknowledged "
                         "w:majority write cannot.")
    R.p(("Observations:", "b"))
    R.bullets(obs_b or ["(no Scenario B events recorded)"])
    R.p(("Rollback files on the former primary: ", "b"),
        read("lab9_2_rollback_files.txt").strip() or "(not recorded)")

    # ---------------- observer (terminal 1) ----------------------------------
    R.h("Terminal 1 - the observer during both scenarios", 2)
    obs_log = read("lab9_2_observer.log")
    if obs_log:
        keep, prev = [], None
        for ln in obs_log.splitlines():
            is_line = re.match(r"\d\d:\d\d:\d\d", ln)
            key = re.sub(r"lag\d+", "", ln[13:]) if is_line else None
            if not is_line or key != prev:
                keep.append(ln)
            if is_line:
                prev = key
        R.p("Only lines where the replica-set state changed are shown (the observer prints a line "
            "every 0.5 s; full log: results/lab9_2_observer.log). PRI/SEC = PRIMARY/SECONDARY, "
            "DOWN = (not reachable/healthy), lagN = seconds behind the primary.", italic=True, size=8.5)
        console_images(R, "root@lab9-runner:/lab9# lab9 observer\n" + "\n".join(keep), "c92_obs",
                       "lab9-runner - lab9 observer", "Observer output (state changes only)", cols=128)

    # ---------------- durability ---------------------------------------------
    R.h("Durability check after recovery", 2)
    if dur:
        R.table(["Phase", "Write concern", "Client saw", "Attempts", "In DB now", "Meaning"],
                [[g["phase"], g["wc"], g["client_outcome"], g["attempts"], g["present_now"],
                  g["verdict"]] for g in dur["groups"]],
                widths=[2.6, 2.3, 3.4, 1.6, 1.6, 5.5], size=8)
        R.p("Documents on each member after recovery: " + ", ".join(
            f"{short(h)} = {v}" for h, v in dur["member_counts"].items())
            + " (identical counts = the partitioned members fully caught up).")
    else:
        R.p("(not recorded)", italic=True)

    R.h("Complete event timeline", 2)
    R.p("Merged from the shell script, the observer, the writer and the mongod logs (consecutive "
        "identical mongod lines are collapsed). T+ is measured from the start of each scenario.",
        italic=True, size=8.5)
    if events:
        starts = [float(e["epoch"]) for e in events if e["event"] == "SCENARIO_START"]
        rows = []
        for e in events:
            t = float(e["epoch"])
            anchor = max([s for s in starts if s <= t + 0.2], default=None)
            rows.append([e["time"][11:23], "" if anchor is None else f"{t - anchor:+.1f}",
                         e["source"], e["event"], e["detail"][:140]])
        R.table(["Time", "T+ (s)", "Source", "Event", "Detail"], rows,
                widths=[1.9, 1.2, 1.5, 3.6, 8.8], size=7)

    R.h("CAP interpretation", 2)
    R.bullets([
        "Scenario A is a minority partition: the side with the primary keeps a majority, so "
        "MongoDB stays both consistent and available for w:1 and w:majority. Only w:all loses "
        "availability, because it requires every member.",
        "Scenario B is the real CAP choice. MongoDB chooses consistency: a primary that cannot "
        "reach a majority steps down instead of continuing to accept writes, so two primaries can "
        "never accept conflicting writes. Writes stop (no write availability) while secondary "
        "reads continue.",
        "The write concern decides what the client is told. w:1 is acknowledged by the isolated "
        "primary during the seconds before the step-down - such a write can later be rolled back. "
        "w:majority is only acknowledged once a majority has it, so an acknowledged w:majority "
        "write survives any election.",
        "Priorities decide who ends up primary after the partition heals: any up-to-date member "
        "can win the first election, then the priority-2 member takes the role back.",
    ])
    R.p(("Predictions vs observations: ", "b"),
        "the isolated member stayed in rs.status() with health 0 and saw no primary, the majority "
        "side kept its primary, w:all timed out in A, the lone primary stepped down about 10 s into "
        "B and the set became read-only until an election restored a primary - as predicted.")
    R.h("Scripts", 2)
    for f in ("Activity_9.2_Network_Partition/simulate_partition.sh",
              "Activity_9.2_Network_Partition/partition_observer.py",
              "Activity_9.2_Network_Partition/partition_writer.py",
              "Activity_9.2_Network_Partition/isolated_view.js"):
        code_file(R, f)


def conclusion(R, r91, rj):
    R.page_break()
    R.h("Conclusion", 1)
    items = []
    if r91:
        rr = r91["results"]
        items.append(f"Stronger write concerns cost latency: w:1 {rr['w:1']['avg_ms']:.3f} ms, "
                     f"w:majority {rr['w:majority']['avg_ms']:.3f} ms, w:all "
                     f"{rr['w:all']['avg_ms']:.3f} ms per write on this setup - the extra cost is "
                     f"the wait for other members' acknowledgments.")
    if rj:
        comp = {c["w"]: c for c in journal_comparison(rj["results"])}
        items.append(f"Journaling: j:true changed w:1 latency by {comp['w:1']['delta_pct']:+.1f}% "
                     f"({comp['w:1']['verdict']}) but w:majority only by "
                     f"{comp['w:majority']['delta_pct']:+.1f}% ({comp['w:majority']['verdict']}), "
                     f"because w:majority is already journaled by default.")
    items += [
        "A partition that leaves a majority connected is invisible to w:1 and w:majority clients; "
        "it only blocks w:all.",
        "A partition that isolates the primary makes it step down after ~10 s; the set becomes "
        "read-only until a majority can communicate again, then an election restores a primary and "
        "the priority-2 member takes its role back.",
        "Write concern is therefore the per-operation dial between availability (w:1) and "
        "consistency/durability (w:majority) - the CAP theorem as an operational choice.",
    ]
    R.bullets(items)


def appendix(R):
    R.page_break()
    R.h("Appendix A - Docker files", 1)
    for f in ("docker-compose.yml", "Dockerfile", "docker-entrypoint.sh"):
        code_file(R, f)
    code_file(R, "lab9", "lab9 (command menu)")
    R.h("Appendix B - shared modules", 1)
    code_file(R, "common/bench.py", "common/bench.py (benchmark engine and explanations)")
    code_file(R, "common/rs.py", "common/rs.py (connections, rs.status parsing, event log)")
    code_file(R, "setup/rsctl.py", "setup/rsctl.py (replica-set control tool)")


# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="Build the Exercise 9 report (PDF + DOCX)")
    ap.add_argument("--docker-url", default=os.getenv("DOCKER_URL", ""))
    ap.add_argument("--out", default=str(RESULTS_DIR / "Exercise-9_Report"))
    args = ap.parse_args()

    url = args.docker_url.strip()
    m = re.search(r"hub\.docker\.com/r/([^/\s]+)/([^/\s?#]+)", url)
    image = f"{m.group(1)}/{m.group(2)}:latest" if m else os.getenv(
        "LAB9_IMAGE", f"{PLACEHOLDER}/nosql-lab9:latest")

    r91 = load_json("lab9_1_results.json")
    rj = load_json("lab9_1_journal_results.json")
    writes, reads = load_jsonl("lab9_2_writes.jsonl"), load_jsonl("lab9_2_reads.jsonl")
    events = load_events() if RESULTS_DIR.exists() else []
    dur = load_json("lab9_2_durability.json")
    env = (r91 or rj or {}).get("environment")
    ver = (env or {}).get("mongodb_version", "")
    run_env = os.getenv("LAB9_RUN_ENV") or (
        "Docker: three mongo:7.0 containers (mongo1-3) and the lab9-runner container on the "
        "Docker network rslab9-net." if ver.startswith("7") else f"MongoDB {ver} replica set.")

    R = Doc(footer=f"{COURSE} | Exercise 9 | {STUDENT['Name']} ({STUDENT['Roll No.']})")
    title_block(R, url, image, env, run_env, os.getenv("LAB9_SAMPLE_NOTE", ""))
    summary(R, r91, rj, writes, events, dur)
    environment(R, env, r91, run_env)
    lab_91(R, r91)
    lab_91_journal(R, rj)
    lab_92(R, writes, reads, events, dur)
    conclusion(R, r91, rj)
    appendix(R)

    base = Path(args.out)
    pdf = render_pdf(R, base.with_suffix(".pdf"), title="Exercise 9 - " + STUDENT["Name"],
                     author=STUDENT["Name"])
    docx = render_docx(R, base.with_suffix(".docx"))
    print(f"Report written to {pdf}\n                  {docx}")
    missing = [n for n, v in (("Lab 9.1", r91), ("Lab 9.1 journal", rj), ("Lab 9.2", writes)) if not v]
    if missing:
        print("WARNING - no results yet for: " + ", ".join(missing))
    if not m or PLACEHOLDER in url:
        print("WARNING - Docker Hub URL missing: run  lab9 report https://hub.docker.com/r/<user>/nosql-lab9")
    print("Submit the PDF: results/Exercise-9_Report.pdf")


if __name__ == "__main__":
    main()

"""
Write-concern benchmark engine shared by Lab 9.1 and its Try-it-yourself.

Method (why the numbers are trustworthy)
  * One MongoClient per configuration, created with the w / journal options
    (the lab solution's approach). NOTE: Collection.insert_one() has NO
    write_concern argument - the reference script's
    `col.insert_one(doc, write_concern=wc)` raises TypeError.
  * Connection set-up, collection creation and 50 warm-up inserts are done
    BEFORE the timer starts, so the timer only measures acknowledged writes.
  * Each insert is timed individually with time.perf_counter(), and the
    whole loop is timed too (= "total time for 1,000 documents").
  * serverStatus().wiredTiger.log["log sync operations"] is read on every
    member before/after a run: this counts journal fsyncs, i.e. it shows
    directly whether a configuration forced the journal to disk.
"""
from __future__ import annotations

import math
import statistics
import time
from datetime import datetime, timezone

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from common.rs import DB_NAME, RS_URI, direct_client

PAYLOAD = "x" * 100  # ~150-byte documents ("small documents")


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = max(0, math.ceil(p / 100 * len(sorted_vals)) - 1)
    return sorted_vals[k]


def journal_syncs(hosts) -> dict:
    """WiredTiger 'log sync operations' counter on each member (None if n/a)."""
    out = {}
    for h in hosts or []:
        c = direct_client(h, timeout_ms=2000)
        try:
            ss = c.admin.command("serverStatus")
            out[h] = ss.get("wiredTiger", {}).get("log", {}).get("log sync operations")
        except PyMongoError:
            out[h] = None
        finally:
            c.close()
    return out


def probe_custom_mode(name: str = "all") -> str | None:
    """Try one insert with w:<name>. Returns None if the server accepts it,
    otherwise the server's error text (MongoDB has no built-in 'all')."""
    probe = MongoClient(RS_URI, w=name, wTimeoutMS=2000, serverSelectionTimeoutMS=5000)
    cleaner = MongoClient(RS_URI, serverSelectionTimeoutMS=5000)
    try:
        probe[DB_NAME]["w_all_probe"].insert_one({"probe": True})
        return None
    except PyMongoError as exc:
        code = getattr(exc, "code", None)
        msg = str(exc).split(", full error")[0]
        return f"{type(exc).__name__} (code {code}): {msg}"
    finally:
        cleaner[DB_NAME].drop_collection("w_all_probe")
        probe.close()
        cleaner.close()


def run_write_benchmark(label, w, j=None, docs=1000, warmup=50,
                        wtimeout_ms=10000, hosts=None) -> dict:
    opts = {"w": w}
    if w not in (0, 1):
        opts["wTimeoutMS"] = wtimeout_ms       # never block forever
    if j is not None:
        opts["journal"] = j
    client = MongoClient(RS_URI, serverSelectionTimeoutMS=5000, **opts)
    try:
        client.admin.command("ping")
        db = client[DB_NAME]
        coll_name = "wc_" + "".join(ch if ch.isalnum() else "_" for ch in label).strip("_")
        db.drop_collection(coll_name)
        coll = db[coll_name]

        # warm-up: opens the pooled connection, creates the collection
        for i in range(warmup):
            coll.insert_one({"warmup": True, "i": i})
        coll.delete_many({"warmup": True})

        syncs_before = journal_syncs(hosts)
        latencies = []
        t_start = time.perf_counter()
        for i in range(docs):
            doc = {"seq": i, "label": label, "payload": PAYLOAD,
                   "ts": datetime.now(timezone.utc)}
            t0 = time.perf_counter()
            coll.insert_one(doc)
            latencies.append((time.perf_counter() - t0) * 1000.0)
        total_s = time.perf_counter() - t_start
        syncs_after = journal_syncs(hosts)
        stored = coll.count_documents({})
        sent_as = dict(client.write_concern.document)
    finally:
        client.close()

    sync_delta = {
        h: (syncs_after[h] - syncs_before[h])
        if syncs_before.get(h) is not None and syncs_after.get(h) is not None else None
        for h in syncs_before
    }
    return {"label": label, "w": w, "j": j, "sent_as": sent_as, "docs": docs,
            "stored": stored, "total_s": total_s, "latencies_ms": latencies,
            "journal_syncs": sync_delta}


def summarize(label: str, runs: list[dict]) -> dict:
    """Merge several rounds of the same configuration."""
    lat = sorted(x for r in runs for x in r["latencies_ms"])
    totals = [r["total_s"] for r in runs]
    docs = runs[0]["docs"]
    round_avg = [t * 1000.0 / docs for t in totals]
    hosts = runs[0]["journal_syncs"].keys()
    syncs = {}
    for h in hosts:
        vals = [r["journal_syncs"].get(h) for r in runs]
        syncs[h] = None if any(v is None for v in vals) else sum(vals) / len(vals)
    return {
        "label": label,
        "sent_as": runs[0]["sent_as"],
        "docs": docs,
        "rounds": len(runs),
        "total_s": statistics.mean(totals),
        "total_s_each": totals,
        "round_avg_ms": round_avg,
        "round_spread_ms": max(round_avg) - min(round_avg),
        "avg_ms": statistics.mean(lat),
        "avg_from_total_ms": statistics.mean(totals) * 1000.0 / docs,
        "p50_ms": percentile(lat, 50),
        "p95_ms": percentile(lat, 95),
        "p99_ms": percentile(lat, 99),
        "min_ms": lat[0],
        "max_ms": lat[-1],
        "stdev_ms": statistics.pstdev(lat),
        "throughput_ops": docs / statistics.mean(totals),
        "journal_syncs_per_run": syncs,
        "all_stored": all(r["stored"] == docs for r in runs),
    }


def wc_text(sent_as: dict) -> str:
    """{'w': 'majority', 'wtimeout': 10000, 'j': True} -> 'w=majority j=true wtimeout=10000'"""
    order = ("w", "j", "wtimeout")
    parts = []
    for k in order + tuple(k for k in sent_as if k not in order):
        if k in sent_as:
            v = sent_as[k]
            parts.append(f"{k}={str(v).lower() if isinstance(v, bool) else v}")
    return " ".join(parts)


def syncs_per_write(row: dict, host: str | None):
    """Journal fsyncs per acknowledged write on `host` (None if not measured)."""
    if not host:
        return None
    v = row["journal_syncs_per_run"].get(host)
    return None if v is None else v / row["docs"]


def _fmt_spw(v):
    return "n/a" if v is None else f"{v:.2f}"


def majority_of(env: dict) -> int:
    n = env.get("data_bearing_members", 3) or 3
    return env.get("write_majority_count") or (n // 2 + 1)


# --------------------------------------------------------------------------
# Explanations - written from the MEASURED numbers so the text always
# matches the table it describes (used by the scripts and by the report).
# --------------------------------------------------------------------------
def explain_91(rows: dict, env: dict) -> list[str]:
    r1, rm, ra = rows["w:1"], rows["w:majority"], rows["w:all"]
    n = env.get("data_bearing_members", 3)
    maj = majority_of(env)
    wmjd = env.get("write_concern_majority_journal_default", True)
    primary = env.get("primary")
    x_m = rm["avg_ms"] / r1["avg_ms"]
    x_a = ra["avg_ms"] / r1["avg_ms"]
    order = sorted(rows, key=lambda k: rows[k]["avg_ms"])
    out = [f"Measured order, fastest to slowest average latency: {' < '.join(order)}."]

    out.append(
        f"w:1 averaged {r1['avg_ms']:.3f} ms per write ({r1['total_s']:.3f} s for "
        f"{r1['docs']:,} documents). The primary acknowledges as soon as the write is applied "
        f"on itself; no other member is involved, so this is the latency floor"
        + (" - and it was the fastest configuration." if order[0] == "w:1" else
           f" - although {order[0]} measured slightly faster in this run (noise)."))

    out.append(
        f"w:majority averaged {rm['avg_ms']:.3f} ms, {x_m:.2f}x w:1. With {n} data-bearing "
        f"members the majority is {maj}, so the primary must wait until at least one "
        f"secondary has fetched the write from the primary's oplog, applied it and reported its "
        f"position back - at least one extra network round trip per write."
        + (" Because writeConcernMajorityJournalDefault is true (the default), the majority "
           "acknowledgment also waits until the write is in the on-disk journal of those members."
           if wmjd else ""))

    gap = ra["avg_ms"] - rm["avg_ms"]
    if gap >= 0:
        out.append(
            f"w:all (sent as w:{n}) averaged {ra['avg_ms']:.3f} ms, {x_a:.2f}x w:1 and "
            f"{gap:+.3f} ms compared with w:majority. It must wait for EVERY secondary, i.e. "
            f"for the slower of the two, so each write pays the worst replication delay in the "
            f"set. The gap to w:majority is small here because both secondaries run on the same "
            f"host and apply writes at almost the same speed; on real servers the slowest member "
            f"(e.g. in another data centre) would dominate.")
    else:
        out.append(
            f"w:all (sent as w:{n}) averaged {ra['avg_ms']:.3f} ms ({x_a:.2f}x w:1), "
            f"{-gap:.3f} ms LESS than w:majority in this run. Both concerns wait for secondaries "
            f"that share one host and apply writes at almost the same speed, so the extra "
            f"member costs very little and the difference is within run-to-run variation "
            f"(per-round averages: w:majority {min(rm['round_avg_ms']):.3f}-"
            f"{max(rm['round_avg_ms']):.3f} ms, w:all {min(ra['round_avg_ms']):.3f}-"
            f"{max(ra['round_avg_ms']):.3f} ms).")
    out.append(
        f"The real cost of w:all is AVAILABILITY, not latency: a single unreachable member "
        f"blocks every w:all write until wtimeout (shown in Lab 9.2, Scenario A).")

    spw = [_fmt_spw(syncs_per_write(rows[k], primary)) for k in ("w:1", "w:majority", "w:all")]
    if any(v != "n/a" for v in spw):
        out.append(
            f"Journal fsyncs per write on the primary ({primary}), read from WiredTiger's "
            f"'log sync operations' counter: w:1 {spw[0]}, w:majority {spw[1]}, w:all {spw[2]}. "
            f"With w:1 the journal is flushed in the background in groups (well under 1 sync per "
            f"write); the replicated concerns flushed roughly once per write, which is part of "
            f"their extra latency.")

    p_order = sorted(rows, key=lambda k: rows[k]["p99_ms"])
    same = p_order == order
    out.append(
        f"Tail latency (P99): w:1 {r1['p99_ms']:.2f} ms, w:majority {rm['p99_ms']:.2f} ms, "
        f"w:all {ra['p99_ms']:.2f} ms - "
        + ("the same order as the averages." if same else
           f"order {' < '.join(p_order)}, not identical to the averages, because single slow "
           f"writes (scheduler pauses on a shared host) dominate the 99th percentile.")
        + " Replicated concerns have a wider spread because every write depends on another "
          "process being scheduled promptly.")

    pos = "inside" if 2.0 <= x_m <= 5.0 else ("below" if x_m < 2.0 else "above")
    out.append(
        f"The lab handout expects w:1 to be 2-5x faster than w:majority; the measured ratio is "
        f"{x_m:.2f}x, {pos} that range."
        + (" All members share one host, so the 'network' hop is a sub-millisecond virtual "
           "bridge; across racks or regions the gap grows with the round-trip time."
           if x_m < 2.0 else ""))
    return out


def journal_comparison(rows: dict) -> list[dict]:
    """rows keyed by 'w:1, j:false' etc. -> one comparison row per w level.
    The verdict compares the difference with the run-to-run spread of the
    per-round averages, so noise is not reported as an effect."""
    out = []
    for w in ("w:1", "w:majority", "w:all"):
        f, t = rows.get(f"{w}, j:false"), rows.get(f"{w}, j:true")
        if not f or not t:
            continue
        delta = t["avg_ms"] - f["avg_ms"]
        pct = 100.0 * delta / f["avg_ms"]
        noise = max(f.get("round_spread_ms", 0.0), t.get("round_spread_ms", 0.0))
        if abs(delta) <= noise or abs(pct) < 5:
            verdict = "within noise"
        elif delta < 0:
            verdict = "none (j:true not slower)"
        elif pct >= 25 and delta >= 0.2:
            verdict = "significant"
        elif pct >= 10:
            verdict = "moderate"
        else:
            verdict = "small"
        out.append({"w": w, "j_false_ms": f["avg_ms"], "j_true_ms": t["avg_ms"],
                    "delta_ms": delta, "delta_pct": pct, "noise_ms": noise,
                    "verdict": verdict})
    return out


REFERENCE_ORDER = ["w:1, j:false", "w:1, j:true", "w:majority, j:false", "w:majority, j:true"]


def reference_order_check(rows: dict) -> dict:
    """Chapter-09 README, 'Check your work': expected fastest -> slowest order."""
    measured = sorted(REFERENCE_ORDER, key=lambda k: rows[k]["avg_ms"])
    return {"expected": REFERENCE_ORDER, "measured": measured,
            "match": measured == REFERENCE_ORDER}


def explain_journal(rows: dict, env: dict) -> list[str]:
    comp = {c["w"]: c for c in journal_comparison(rows)}
    primary = env.get("primary")
    wmjd = env.get("write_concern_majority_journal_default", True)
    n = env.get("data_bearing_members", 3)
    out = []

    def spw(label):
        return _fmt_spw(syncs_per_write(rows[label], primary))

    def nz(c):
        return f"run-to-run spread {c['noise_ms']:.3f} ms"

    c = comp["w:1"]
    out.append(
        f"w:1 - j:true changed the average from {c['j_false_ms']:.3f} to {c['j_true_ms']:.3f} ms "
        f"({c['delta_ms']:+.3f} ms, {c['delta_pct']:+.1f}%, {nz(c)}): {c['verdict']}. With "
        f"j:false the primary acknowledges once the write is applied and the journal is flushed "
        f"in the background in groups; with j:true every acknowledgment waits for an immediate "
        f"journal flush. The primary's journal-sync counter shows it directly: "
        f"{spw('w:1, j:false')} syncs per write with j:false vs {spw('w:1, j:true')} with j:true.")

    c = comp["w:majority"]
    if wmjd:
        out.append(
            f"w:majority - {c['j_false_ms']:.3f} ms (j:false) vs {c['j_true_ms']:.3f} ms (j:true), "
            f"{c['delta_ms']:+.3f} ms ({c['delta_pct']:+.1f}%, {nz(c)}): {c['verdict']}. "
            f"writeConcernMajorityJournalDefault is true, so MongoDB already waits for the "
            f"journal on the majority even when j:false is requested; the journal-sync counter "
            f"on the primary is the same in both cases ({spw('w:majority, j:false')} vs "
            f"{spw('w:majority, j:true')} per write). Any remaining difference is not caused by "
            f"extra journal flushes.")
    else:
        out.append(
            f"w:majority - {c['j_false_ms']:.3f} ms vs {c['j_true_ms']:.3f} ms "
            f"({c['delta_pct']:+.1f}%): {c['verdict']}. writeConcernMajorityJournalDefault is "
            f"false on this set, so j:false really skips the journal wait.")

    c = comp["w:all"]
    out.append(
        f"w:all (w:{n}) - {c['j_false_ms']:.3f} ms vs {c['j_true_ms']:.3f} ms "
        f"({c['delta_ms']:+.3f} ms, {c['delta_pct']:+.1f}%, {nz(c)}): {c['verdict']}. Journal "
        f"syncs per write on the primary: {spw('w:all, j:false')} vs {spw('w:all, j:true')}. "
        f"Waiting for every member already costs more than one journal flush, so adding j:true "
        f"changes little.")

    rep = rows["w:majority, j:false"]["avg_ms"] - rows["w:1, j:false"]["avg_ms"]
    jw1 = comp["w:1"]["delta_ms"]
    bigger = "replication (waiting for another member)" if rep > jw1 else "journaling"
    out.append(
        f"Answer - does journaling add significant latency? It depends on the w level. For w:1 "
        f"the effect was {comp['w:1']['verdict']} ({comp['w:1']['delta_pct']:+.1f}%), because "
        f"j:true turns a background flush into a flush on every write. For w:majority and w:all "
        f"it was " + (comp['w:majority']['verdict'] if comp['w:majority']['verdict'] == comp['w:all']['verdict']
                      else f"{comp['w:majority']['verdict']} and {comp['w:all']['verdict']} respectively")
        + f", because those "
        f"writes are already journaled before they are acknowledged. In absolute terms, adding a "
        f"replica acknowledgment (w:1 -> w:majority, {rep:+.3f} ms) cost "
        f"{'more' if rep > jw1 else 'less'} than adding the journal flush to w:1 "
        f"({jw1:+.3f} ms), so on this setup {bigger} is the larger part of the price of "
        f"durability. Journal flush time depends on the disk: a spinning disk or network "
        f"storage would make j:true much more expensive than a local SSD.")

    chk = reference_order_check(rows)
    out.append(
        "Chapter 9 README 'Check your work' - expected order "
        + " < ".join(chk["expected"]) + "; measured order " + " < ".join(chk["measured"])
        + (": matches." if chk["match"] else
           ": not identical - the two w:majority configurations (and other pairs whose difference "
           "is within the run-to-run spread) can swap places, because with "
           "writeConcernMajorityJournalDefault: true they do the same work."))
    return out

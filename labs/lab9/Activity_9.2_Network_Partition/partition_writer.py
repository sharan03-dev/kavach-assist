"""
Lab 9.2 - Insert documents with different write concerns while a partition
is active, and record which operations succeed and which time out.

Each round inserts one document per write concern and (with --reads) also
tries one read with readPreference=primary and one with secondaryPreferred.
Every attempt is appended to results/lab9_2_writes.jsonl:
  {time, phase, wc, seq, _id, outcome, latency_ms, error}
and every read to results/lab9_2_reads.jsonl.

Outcomes
  OK                     acknowledged with the requested write concern
  WTIMEOUT               applied on the primary, but not acknowledged by
                         enough members within wtimeout (the write is NOT
                         undone - it replicates later or is rolled back)
  PRIMARY_STEPPED_DOWN / INTERRUPTED_REPL_CHANGE
                         the primary stepped down while the write waited
  NO_PRIMARY             no primary could be selected - write never sent
  NOT_PRIMARY            sent to a node that is no longer primary

Client settings: retryWrites=False so every attempt shows the raw outcome,
serverSelectionTimeoutMS=2000 so "no primary" is reported quickly.

  python partition_writer.py --phase A --duration 15 --concerns 1,majority,all
  python partition_writer.py --verify      # after recovery: which writes survived?
  python partition_writer.py --reset       # start a fresh experiment
"""
import argparse
import json
import sys
import time
import uuid
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from common.rs import (DB_NAME, RESULTS_DIR, RS_URI, classify, clock, data_bearing_members,  # noqa: E402
                       direct_client, format_table, log_event, results_file, rs_client,
                       rs_config, short)

COLL = "partition_writes"
WRITES_LOG = "lab9_2_writes.jsonl"
READS_LOG = "lab9_2_reads.jsonl"


def data_bearing_count() -> int:
    try:
        c = rs_client(serverSelectionTimeoutMS=3000)
        n = len(data_bearing_members(rs_config(c)))
        c.close()
        return n
    except PyMongoError:
        return 3


def make_clients(concerns, wtimeout):
    n = data_bearing_count() if "all" in concerns else 3
    clients = OrderedDict()
    for c in concerns:
        w = {"1": 1, "majority": "majority", "all": n}[c]
        label = "w:1" if c == "1" else ("w:majority" if c == "majority" else f"w:all(w:{n})")
        kw = dict(w=w, retryWrites=False, retryReads=False,
                  serverSelectionTimeoutMS=2000, connectTimeoutMS=2000,
                  socketTimeoutMS=wtimeout + 5000)
        if w != 1:
            kw["wTimeoutMS"] = wtimeout
        clients[label] = MongoClient(RS_URI, **kw)
    return clients


def try_read(client):
    t0 = time.perf_counter()
    try:
        client[DB_NAME][COLL].find_one({}, max_time_ms=2000)
        return "OK", (time.perf_counter() - t0) * 1000
    except PyMongoError as exc:
        name = classify(exc)
        return ("NO_SERVER" if name == "NO_PRIMARY" else name), (time.perf_counter() - t0) * 1000


def run(args):
    concerns = [c.strip() for c in args.concerns.split(",") if c.strip()]
    clients = make_clients(concerns, args.wtimeout)
    readers = {}
    if args.reads:
        for rp in ("primary", "secondaryPreferred"):
            readers[rp] = MongoClient(RS_URI, readPreference=rp, retryReads=False,
                                      serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)
    log = results_file(WRITES_LOG).open("a")
    rlog = results_file(READS_LOG).open("a") if readers else None
    last = {}
    stats = {label: Counter() for label in clients}
    ok_lat = {label: [] for label in clients}
    run_tag = uuid.uuid4().hex[:6]
    t_end = time.time() + args.duration if args.duration else None
    seq = 0
    print(f"[{clock()}] writer phase={args.phase}  concerns={list(clients)}  "
          f"wtimeout={args.wtimeout}ms", flush=True)
    while (t_end is None or time.time() < t_end) and (not args.rounds or seq < args.rounds):
        seq += 1
        parts = []
        for label, client in clients.items():
            _id = f"{args.phase}:{label}:{seq}:{run_tag}"
            ts = datetime.now()
            t0 = time.perf_counter()
            err = ""
            try:
                client[DB_NAME][COLL].insert_one({
                    "_id": _id, "phase": args.phase, "wc": label, "seq": seq,
                    "client_time": datetime.now(timezone.utc)})
                outcome = "OK"
            except PyMongoError as exc:
                outcome = classify(exc)
                err = str(exc).split(", full error")[0][:200]
            lat = (time.perf_counter() - t0) * 1000
            stats[label][outcome] += 1
            if outcome == "OK":
                ok_lat[label].append(lat)
            log.write(json.dumps({"time": ts.isoformat(timespec="milliseconds"),
                                  "epoch": ts.timestamp(), "phase": args.phase, "wc": label,
                                  "seq": seq, "_id": _id, "outcome": outcome,
                                  "latency_ms": round(lat, 3), "error": err}) + "\n")
            log.flush()
            parts.append(f"{label}={outcome}({lat:.0f}ms)")
            if last.get(label) != outcome:
                if label in last:
                    log_event("writer", "WRITE_OUTCOME_CHANGE",
                              f"[{args.phase}] {label}: {last[label]} -> {outcome}", echo=False)
                else:
                    log_event("writer", "WRITE_OUTCOME_FIRST",
                              f"[{args.phase}] {label}: {outcome}", echo=False)
                last[label] = outcome
        for rp, rc in readers.items():
            ts = datetime.now()
            res, lat = try_read(rc)
            rlog.write(json.dumps({"time": ts.isoformat(timespec="milliseconds"),
                                   "epoch": ts.timestamp(), "phase": args.phase,
                                   "read_preference": rp, "seq": seq, "outcome": res,
                                   "latency_ms": round(lat, 3)}) + "\n")
            rlog.flush()
            parts.append(f"read[{rp}]={res}")
            if last.get(rp) != res:
                log_event("writer", "READ_OUTCOME", f"[{args.phase}] read {rp}: {res}", echo=False)
                last[rp] = res
        print(f"{clock()}  #{seq:<3} " + "  ".join(parts), flush=True)
        time.sleep(args.interval)

    log.close()
    if rlog:
        rlog.close()
    print()
    rows = []
    for label, cnt in stats.items():
        lat = ok_lat[label]
        rows.append([label, sum(cnt.values()), cnt.get("OK", 0),
                     ", ".join(f"{k}:{v}" for k, v in cnt.items() if k != "OK") or "-",
                     f"{sum(lat) / len(lat):.1f}" if lat else "-"])
    print(format_table(["Write concern", "Attempts", "OK", "Failures", "Avg OK latency (ms)"],
                       rows, right=(1, 2, 4)))
    for c in list(clients.values()) + list(readers.values()):
        c.close()


def verdict(outcome, attempts, present):
    if outcome == "OK":
        return ("durable" if present == attempts
                else f"{attempts - present} ACKNOWLEDGED WRITE(S) ROLLED BACK")
    if outcome in ("WTIMEOUT", "PRIMARY_STEPPED_DOWN", "INTERRUPTED_REPL_CHANGE"):
        if present == attempts:
            return "client got an error, but the write WAS applied and later replicated"
        if present == 0:
            return "applied on the isolated primary, then rolled back"
        return f"{present} kept, {attempts - present} rolled back"
    return "never applied (rejected)" if present == 0 else f"{present} applied despite the error"


def verify(args):
    path = RESULTS_DIR / WRITES_LOG
    if not path.exists():
        print("No writes recorded yet.")
        return
    attempts = [json.loads(line) for line in path.open() if line.strip()]
    client = MongoClient(RS_URI, readConcernLevel="majority", serverSelectionTimeoutMS=15000)
    coll = client[DB_NAME][COLL]
    ids = [a["_id"] for a in attempts]
    present = set()
    for i in range(0, len(ids), 500):
        present |= {d["_id"] for d in coll.find({"_id": {"$in": ids[i:i + 500]}}, {"_id": 1})}

    groups = OrderedDict()
    for a in attempts:
        k = (a["phase"], a["wc"], a["outcome"])
        g = groups.setdefault(k, [0, 0])
        g[0] += 1
        g[1] += a["_id"] in present
    out = [{"phase": p, "wc": w, "client_outcome": o, "attempts": n, "present_now": k,
            "verdict": verdict(o, n, k)} for (p, w, o), (n, k) in groups.items()]
    print(f"[{clock()}] Durability check (readConcern majority on the current primary)")
    print(format_table(["Phase", "Write concern", "Client saw", "Attempts", "In DB now", "Meaning"],
                       [[g["phase"], g["wc"], g["client_outcome"], g["attempts"],
                         g["present_now"], g["verdict"]] for g in out], right=(3, 4)))

    counts = {}
    hosts = data_bearing_members(rs_config(client))
    for h in hosts:
        dc = direct_client(h, timeout_ms=3000)
        try:
            counts[h] = dc[DB_NAME][COLL].count_documents({})
        except PyMongoError as exc:
            counts[h] = f"unreachable ({type(exc).__name__})"
        finally:
            dc.close()
    print("\nDocuments in lab9.partition_writes on each member: " +
          ", ".join(f"{short(h)}={v}" for h, v in counts.items()))
    results_file("lab9_2_durability.json").write_text(
        json.dumps({"groups": out, "member_counts": counts}, indent=2))
    client.close()


def reset():
    c = rs_client()
    c[DB_NAME].drop_collection(COLL)
    c.close()
    for p in RESULTS_DIR.glob("lab9_2_*"):
        p.unlink()
    print(f"[{clock()}] reset: dropped {DB_NAME}.{COLL} and removed results/lab9_2_*")


def main():
    ap = argparse.ArgumentParser(description="Lab 9.2 partition writer")
    ap.add_argument("--phase", default="manual")
    ap.add_argument("--duration", type=float, default=20, help="seconds (0 = use --rounds)")
    ap.add_argument("--rounds", type=int, default=0)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--concerns", default="1,majority", help="comma list of 1,majority,all")
    ap.add_argument("--wtimeout", type=int, default=3000, help="ms")
    ap.add_argument("--reads", action="store_true", help="also test primary / secondaryPreferred reads")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    if args.reset:
        reset()
    elif args.verify:
        verify(args)
    else:
        if not args.duration and not args.rounds:
            ap.error("give --duration or --rounds")
        run(args)


if __name__ == "__main__":
    main()

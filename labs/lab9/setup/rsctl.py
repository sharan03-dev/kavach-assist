"""
rsctl - small replica-set control tool used by the Lab 9 scripts and by the
container entrypoint.

  python setup/rsctl.py init                 initialise rs0 (idempotent)
  python setup/rsctl.py status               rs.status() summary
  python setup/rsctl.py primary|secondaries  print member host:port
  python setup/rsctl.py wait-healthy         1 PRIMARY + all SECONDARY, lag 0
  python setup/rsctl.py wait-primary         block until a primary exists
  python setup/rsctl.py wait-caught-up HOST  block until HOST is a caught-up SECONDARY
  python setup/rsctl.py preferred            member with the highest priority
  python setup/rsctl.py wait-primary-is HOST block until HOST is PRIMARY (priority takeover)
  python setup/rsctl.py mongod-log --since T state transitions / elections from the mongod logs
  python setup/rsctl.py event NAME [DETAIL]  append to the Lab 9.2 event log
  python setup/rsctl.py now | sleep-until T  timing helpers for the shell script
  python setup/rsctl.py timeline             merged Lab 9.2 timeline (all sources)
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo.errors import OperationFailure, PyMongoError  # noqa: E402

from common.rs import (clock, direct_client, format_table, load_events, log_event,  # noqa: E402
                       member_rows, replica_set_name, results_file, rs_client, rs_config,
                       rs_status, seed_hosts, short)


def _status():
    c = rs_client(serverSelectionTimeoutMS=3000)
    try:
        return rs_status(c)
    finally:
        c.close()


# ---------------------------------------------------------------- commands
def cmd_init(_):
    hosts = seed_hosts()
    c = direct_client(hosts[0], timeout_ms=3000)
    try:
        c.admin.command("ping")
        try:
            c.admin.command("replSetGetStatus")
            print(f"[{clock()}] {replica_set_name()} already initialised")
        except OperationFailure as exc:
            if exc.code != 94:                     # 94 = NotYetInitialized
                raise
            cfg = {"_id": replica_set_name(),
                   "members": [{"_id": i, "host": h, "priority": 2 if i == 0 else 1}
                               for i, h in enumerate(hosts)]}
            try:
                c.admin.command("replSetInitiate", cfg)
                print(f"[{clock()}] replSetInitiate sent: "
                      + ", ".join(f"{m['host']}(p{m['priority']})" for m in cfg["members"]))
            except OperationFailure as e2:
                if e2.code != 23:                  # 23 = AlreadyInitialized
                    raise
    except PyMongoError as exc:
        print(f"[{clock()}] init: {hosts[0]} not ready yet ({type(exc).__name__})")
        return 1
    finally:
        c.close()
    return cmd_wait_primary(argparse.Namespace(timeout=60, log=False))


def cmd_status(_):
    st = _status()
    rows, primary = member_rows(st)
    view = next((r["name"] for r in rows if r["self"]), "?")
    print(f"[{clock()}] rs.status() as seen by {view}   term {st.get('term')}   "
          f"PRIMARY: {primary or 'NONE'}")
    print(format_table(
        ["MEMBER", "STATE", "HEALTH", "LAG (s)", "SYNC SOURCE (replicates from)"],
        [[r["name"], r["state"], "UP" if r["health"] else "DOWN",
          "-" if r["lag_s"] is None else f"{r['lag_s']:.0f}", r["sync"] or "-"] for r in rows]))
    return 0


def cmd_primary(_):
    _, p = member_rows(_status())
    if not p:
        return 1
    print(p)
    return 0


def cmd_secondaries(_):
    rows, _ = member_rows(_status())
    for r in sorted(rows, key=lambda r: r["name"]):
        if r["state"] == "SECONDARY":
            print(r["name"])
    return 0


def cmd_members(_):
    rows, _ = member_rows(_status())
    for r in sorted(rows, key=lambda r: r["name"]):
        print(r["name"])
    return 0


def cmd_wait_healthy(a):
    t0 = time.time()
    last = ""
    while time.time() - t0 < a.timeout:
        try:
            rows, primary = member_rows(_status())
            ok = (primary is not None and all(
                r["health"] == 1 and r["state"] in ("PRIMARY", "SECONDARY")
                and r["lag_s"] is not None and r["lag_s"] <= a.max_lag for r in rows))
            if ok and a.preferred_primary:
                ok = primary == _preferred()
            last = ", ".join(f"{short(r['name'])}={r['state']}" for r in rows)
            if ok:
                print(f"[{clock()}] replica set healthy: {last}")
                return 0
        except PyMongoError as exc:
            last = type(exc).__name__
        time.sleep(1)
    print(f"[{clock()}] NOT healthy after {a.timeout}s ({last})")
    return 1


def cmd_wait_primary(a):
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        try:
            st = _status()
            _, primary = member_rows(st)
            if primary:
                el = time.time() - t0
                if a.log:
                    log_event("script", "PRIMARY_AVAILABLE",
                              f"{primary} is PRIMARY (term {st.get('term')}), "
                              f"{el:.1f}s after reconnecting", f"{el:.1f}")
                else:
                    print(f"[{clock()}] PRIMARY: {primary} (term {st.get('term')})")
                return 0
        except PyMongoError:
            pass
        time.sleep(0.25)
    print(f"[{clock()}] no primary after {a.timeout}s")
    return 1


def cmd_wait_caught_up(a):
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        try:
            rows, primary = member_rows(_status())
            m = next((r for r in rows if r["name"] == a.member), None)
            p = next((r for r in rows if r["name"] == primary), None)
            if (m and p and m["health"] == 1 and m["state"] == "SECONDARY"
                    and m["optime"] is not None and p["optime"] is not None
                    and m["optime"] >= p["optime"]):
                el = time.time() - t0
                log_event("script", "CAUGHT_UP",
                          f"{a.member} is SECONDARY with the primary's latest optime, "
                          f"{el:.1f}s after reconnecting", f"{el:.1f}")
                return 0
        except PyMongoError:
            pass
        time.sleep(0.25)
    log_event("script", "CAUGHT_UP_TIMEOUT", f"{a.member} not caught up after {a.timeout}s")
    return 1


def _preferred():
    c = rs_client(serverSelectionTimeoutMS=3000)
    try:
        members = rs_config(c)["members"]
    finally:
        c.close()
    return max(members, key=lambda m: (m.get("priority", 1), -m["_id"]))["host"]


def cmd_preferred(_):
    print(_preferred())
    return 0


def cmd_wait_primary_is(a):
    """Block until a.member is PRIMARY again (priority takeover after a
    partition). Logs PRIORITY_TAKEOVER with the time since a.since."""
    t0 = time.time()
    ref = a.since or t0
    while time.time() - t0 < a.timeout:
        try:
            st = _status()
            _, primary = member_rows(st)
            if primary == a.member:
                log_event("script", a.event,
                          f"{a.member} is PRIMARY again (term {st.get('term')}), "
                          f"{time.time() - ref:.1f}s after the network was restored",
                          f"{time.time() - ref:.1f}")
                return 0
        except PyMongoError:
            pass
        time.sleep(0.25)
    log_event("script", a.event + "_NOT_SEEN",
              f"{a.member} did not become PRIMARY within {a.timeout:.0f}s")
    return 1


# mongod log lines that describe role changes. MongoDB <= 4.2 writes plain text,
# 4.4+ (incl. 7.0) writes one JSON object per line - both are handled.
_KEYS = ("state transition", "transition to", "stepping down", "starting an election",
         "election succeeded", "priority takeover", "stepdown", "step down",
         "sync source", "syncing from")


def _parse_mongod_line(line):
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        try:
            d = json.loads(line)
        except ValueError:
            return None
        msg = d.get("msg", "")
        if not any(k in msg.lower() for k in _KEYS):
            return None
        ts = (d.get("t") or {}).get("$date")
        attr = d.get("attr") or {}
        extra = []
        if "oldState" in attr or "newState" in attr:
            extra.append(f"{attr.get('oldState', '?')} -> {attr.get('newState', '?')}")
        for k in ("term", "reason", "electionTerm"):
            if k in attr:
                extra.append(f"{k}={attr[k]}")
        text = msg + (f" ({', '.join(str(x) for x in extra)})" if extra else "")
    else:
        m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+[+-]\d{2}:?\d{2})\s+\S+\s+\S+\s+\[[^\]]*\]\s+(.*)", line)
        if not m or not any(k in m.group(2).lower() for k in _KEYS):
            return None
        ts, text = m.group(1), m.group(2)
    try:
        ts = ts.replace("Z", "+00:00")
        if re.search(r"[+-]\d{4}$", ts):
            ts = ts[:-2] + ":" + ts[-2:]
        epoch = datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None
    return epoch, text


def cmd_mongod_log(a):
    """Read each member's mongod log with `docker logs` and record role changes
    (exact server-side timestamps) as source 'mongod' events."""
    hosts = seed_hosts()
    found = []
    for h in hosts:
        container = h.split(":", 1)[0]
        try:
            out = subprocess.run(["docker", "logs", container], capture_output=True,
                                 text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"[{clock()}] cannot read docker logs of {container}: {exc}")
            continue
        for line in (out.stdout + "\n" + out.stderr).splitlines():
            p = _parse_mongod_line(line)
            if p and p[0] >= a.since:
                found.append((p[0], container, p[1]))
    found.sort()
    for epoch, container, text in found:
        log_event("mongod", "MONGOD_LOG", f"{container}: {text[:150]}", echo=a.echo, at=epoch)
    print(f"[{clock()}] {len(found)} role-change line(s) read from the mongod logs")
    return 0


def cmd_event(a):
    log_event(a.source, a.name, a.detail, a.value)
    return 0


def cmd_now(_):
    print(f"{time.time():.3f}")
    return 0


def cmd_sleep_until(a):
    left = a.epoch - time.time()
    if left > 0:
        print(f"[{clock()}] waiting {left:.1f}s (partition window) ...", flush=True)
        time.sleep(left)
    return 0


def cmd_timeline(_):
    events = load_events()
    if not events:
        print("No Lab 9.2 events recorded yet.")
        return 1
    anchor, rows = None, []
    for e in events:
        if e["event"] == "SCENARIO_START":
            anchor = float(e["epoch"])
        off = "" if anchor is None else f"+{float(e['epoch']) - anchor:5.1f}s"
        rows.append([e["time"][11:23], off, e["source"], e["event"], e["detail"]])
    print(format_table(["TIME", "T+", "SOURCE", "EVENT", "DETAIL"], rows))
    md = ["| Time | T+ (since scenario start) | Source | Event | Detail |",
          "|---|---|---|---|---|"] + [f"| {' | '.join(r)} |" for r in rows]
    results_file("lab9_2_timeline.md").write_text("\n".join(md) + "\n")
    print("\nSaved results/lab9_2_timeline.md")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Lab 9 replica-set helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("primary")
    sub.add_parser("secondaries")
    sub.add_parser("members")
    p = sub.add_parser("wait-healthy")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--max-lag", type=float, default=2)
    p.add_argument("--preferred-primary", action="store_true",
                   help="also wait until the highest-priority member is PRIMARY")
    sub.add_parser("preferred")
    p = sub.add_parser("wait-primary-is")
    p.add_argument("member")
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--since", type=float, default=0)
    p.add_argument("--event", default="PRIORITY_TAKEOVER")
    p = sub.add_parser("mongod-log")
    p.add_argument("--since", type=float, default=0)
    p.add_argument("--echo", action="store_true")
    p = sub.add_parser("wait-primary")
    p.add_argument("--timeout", type=float, default=60)
    p.add_argument("--log", action="store_true")
    p = sub.add_parser("wait-caught-up")
    p.add_argument("member")
    p.add_argument("--timeout", type=float, default=120)
    p = sub.add_parser("event")
    p.add_argument("--source", default="script")
    p.add_argument("--value", default="")
    p.add_argument("name")
    p.add_argument("detail", nargs="?", default="")
    sub.add_parser("now")
    p = sub.add_parser("sleep-until")
    p.add_argument("epoch", type=float)
    sub.add_parser("timeline")
    a = ap.parse_args()
    fn = {"init": cmd_init, "status": cmd_status, "primary": cmd_primary,
          "secondaries": cmd_secondaries, "members": cmd_members,
          "wait-healthy": cmd_wait_healthy, "wait-primary": cmd_wait_primary,
          "wait-caught-up": cmd_wait_caught_up, "event": cmd_event, "now": cmd_now,
          "sleep-until": cmd_sleep_until, "timeline": cmd_timeline,
          "preferred": cmd_preferred, "wait-primary-is": cmd_wait_primary_is,
          "mongod-log": cmd_mongod_log}[a.cmd]
    sys.exit(fn(a))


if __name__ == "__main__":
    main()

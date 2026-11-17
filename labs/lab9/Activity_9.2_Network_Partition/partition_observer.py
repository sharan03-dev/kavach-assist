"""
Lab 9.2 - Replica-set observer (the Lab 8.2 failover monitor, extended for
network partitions).

Every --interval seconds it asks EACH member directly for rs.status() - all
members in parallel, so an unreachable member does not delay the others -
and prints one line describing the replica set as seen from the reachable side:

  14:02:03.120  view@mongo1 term 3   PRIMARY=mongo1 | mongo1=PRI  mongo2=SEC lag0  mongo3=DOWN

  PRI/SEC  = PRIMARY / SECONDARY          lagN = seconds behind the primary
  DOWN     = "(not reachable/healthy)"    - the member is NOT removed from
             rs.status(); it stays listed with health 0 until it returns.

Events (written to results/lab9_2_events_observer.csv):
  MEMBER_UNREACHABLE, MEMBER_REACHABLE, STATE_CHANGE, MEMBER_CAUGHT_UP,
  PRIMARY_STEPPED_DOWN, PRIMARY_ELECTED, FAILOVER.

A member cut off from the network cannot be queried from here; its OWN view
is captured by simulate_partition.sh with `docker exec <container> mongosh`.

Run:  lab9 observer                (Ctrl+C to stop)
      python Activity_9.2_Network_Partition/partition_observer.py --interval 0.5
"""
import argparse
import csv
import signal
from concurrent.futures import ThreadPoolExecutor
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo.errors import PyMongoError  # noqa: E402

from common.rs import (clock, direct_client, log_event, member_rows, results_file,  # noqa: E402
                       rs_client, rs_config, seed_hosts, short)

ABBR = {"PRIMARY": "PRI", "SECONDARY": "SEC", "(not reachable/healthy)": "DOWN",
        "RECOVERING": "REC", "ROLLBACK": "ROLLBACK", "STARTUP2": "STARTUP2",
        "ARBITER": "ARB", "UNKNOWN": "UNKNOWN"}


def discover_hosts():
    try:
        c = rs_client(serverSelectionTimeoutMS=3000)
        hosts = [m["host"] for m in rs_config(c)["members"]]
        c.close()
        return hosts
    except PyMongoError:
        return seed_hosts()


def main():
    ap = argparse.ArgumentParser(description="Lab 9.2 replica-set observer")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--duration", type=float, default=0, help="0 = until Ctrl+C / SIGTERM")
    args = ap.parse_args()

    running = {"on": True}

    def stop(*_):
        running["on"] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    hosts = discover_hosts()
    clients = {h: direct_client(h, timeout_ms=700) for h in hosts}
    pool = ThreadPoolExecutor(max_workers=len(hosts))

    def ask(c):
        try:
            return c.admin.command("replSetGetStatus")
        except PyMongoError:
            return None
    snap_path = results_file("lab9_2_observer.csv")
    new = not snap_path.exists()
    fh = snap_path.open("a", newline="")
    snap = csv.writer(fh)
    if new:
        snap.writerow(["time", "view_from", "term", "primary"] +
                      [f"{short(h)}_{k}" for h in hosts for k in ("state", "health", "lag_s")])

    print(f"Observing {', '.join(hosts)} every {args.interval}s  (Ctrl+C to stop)\n", flush=True)
    prev_state, prev_primary, rejoined_at = {}, "?", {}
    t_end = time.time() + args.duration if args.duration else None
    no_view_logged = False

    while running["on"] and (t_end is None or time.time() < t_end):
        t_loop = time.time()
        views = {}
        for h, st in zip(clients, pool.map(ask, clients.values())):
            if st is not None:
                views[h] = st

        if not views:
            print(f"{clock()}  no member reachable from the observer", flush=True)
            if not no_view_logged:
                log_event("observer", "NO_MEMBER_REACHABLE", "observer cannot reach any member", echo=False)
                no_view_logged = True
            time.sleep(args.interval)
            continue
        no_view_logged = False

        # prefer the primary's view; otherwise the first reachable member's
        view_host = next((h for h, s in views.items() if s.get("myState") == 1), sorted(views)[0])
        st = views[view_host]
        rows, primary = member_rows(st)
        term = st.get("term")

        # ---------------- events -------------------------------------------
        if prev_primary == "?":
            log_event("observer", "OBSERVER_START", f"primary={primary} term={term}", echo=False)
        elif primary != prev_primary:
            if prev_primary and not primary:
                old = next((r for r in rows if r["name"] == prev_primary), None)
                now_as = old["state"] if old else "unknown"
                log_event("observer", "PRIMARY_STEPPED_DOWN",
                          f"{prev_primary} PRIMARY -> {now_as}; no primary: replica set is read-only "
                          f"(term {term})")
            elif primary and not prev_primary:
                log_event("observer", "PRIMARY_ELECTED", f"{primary} became PRIMARY (term {term})")
                print(f"  [{clock()}] *** PRIMARY detected: {primary} ***", flush=True)
            else:
                log_event("observer", "FAILOVER", f"{prev_primary} -> {primary} (term {term})")
                print(f"  [{clock()}] *** FAILOVER: {prev_primary} -> {primary} ***", flush=True)

        for r in rows:
            name, key = r["name"], (r["health"], r["state"])
            old = prev_state.get(name)
            if old and old != key:
                if old[0] == 1 and r["health"] == 0:
                    log_event("observer", "MEMBER_UNREACHABLE",
                              f"{name}: {old[1]} -> {r['state']} (heartbeats failing)")
                elif old[0] == 0 and r["health"] == 1:
                    log_event("observer", "MEMBER_REACHABLE", f"{name}: back as {r['state']}")
                    rejoined_at[name] = time.time()
                elif "PRIMARY" not in (old[1], r["state"]):   # primary changes logged above
                    log_event("observer", "STATE_CHANGE", f"{name}: {old[1]} -> {r['state']}")
            prev_state[name] = key
            if (name in rejoined_at and primary and r["state"] == "SECONDARY"
                    and r["lag_s"] is not None and r["lag_s"] == 0):
                el = time.time() - rejoined_at.pop(name)
                log_event("observer", "MEMBER_CAUGHT_UP",
                          f"{name}: lag 0 s, {el:.1f}s after it became reachable", f"{el:.1f}")
        prev_primary = primary

        # ---------------- output line --------------------------------------
        cells = []
        for r in rows:
            s = ABBR.get(r["state"], r["state"])
            if r["state"] == "SECONDARY" and r["lag_s"] is not None:
                s += f" lag{r['lag_s']:.0f}"
            cells.append(f"{short(r['name'])}={s}")
        print(f"{clock()}  view@{short(view_host):<7}term {str(term):<4}"
              f"PRIMARY={short(primary):<7}| " + "  ".join(cells), flush=True)

        by_name = {r["name"]: r for r in rows}
        snap.writerow([clock(), view_host, term, primary or ""] + [
            v for h in hosts for v in (
                by_name.get(h, {}).get("state", ""), by_name.get(h, {}).get("health", ""),
                "" if by_name.get(h, {}).get("lag_s") is None else f"{by_name[h]['lag_s']:.0f}")])
        fh.flush()
        time.sleep(max(0.0, args.interval - (time.time() - t_loop)))

    fh.close()
    pool.shutdown(wait=False)
    for c in clients.values():
        c.close()
    print("\nObserver stopped. Snapshots: results/lab9_2_observer.csv", flush=True)


if __name__ == "__main__":
    main()

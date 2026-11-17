"""
Activity 8.2 - Continuously read from the replica set and log which member
served each read. Force a stepDown and watch the driver reconnect.

How "which member" is detected
  A pymongo CommandListener receives an event for every command the driver
  sends. The event's connection_id is the (host, port) of the server that
  actually answered, so the log shows the real member - not a guess.

Usage
  python failover_monitor.py                      # run until Ctrl+C, stepDown manually
  python failover_monitor.py --stepdown-after 10  # script forces the stepDown itself
  python failover_monitor.py --read-pref secondaryPreferred --duration 60

Every read is also written to failover_log.csv for the report.
"""
import argparse
import csv
import os
import threading
import time
from datetime import datetime

from pymongo import MongoClient, monitoring
from pymongo.errors import PyMongoError

RS_URI = os.getenv(
    "MONGO_URI",
    "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0",
)


class ServerTracker(monitoring.CommandListener):
    """Remembers which server answered the most recent 'find'."""

    def __init__(self):
        self.last_server = None

    def started(self, event):
        pass

    def succeeded(self, event):
        if event.command_name == "find":
            host, port = event.connection_id
            self.last_server = f"{host}:{port}"

    def failed(self, event):
        pass


def now():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def force_stepdown(delay):
    """Background thread: step the current primary down after `delay` s."""
    time.sleep(delay)
    admin = MongoClient(RS_URI, serverSelectionTimeoutMS=5000)
    try:
        print(f"{now()}  >>> Forcing replSetStepDown on {admin.primary} <<<")
        admin.admin.command("replSetStepDown", 60, secondaryCatchUpPeriodSecs=10)
    except PyMongoError as exc:
        # Some versions drop connections on stepDown - expected.
        print(f"{now()}  >>> stepDown issued ({type(exc).__name__}) <<<")
    finally:
        admin.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=0.5, help="seconds between reads")
    ap.add_argument("--duration", type=float, default=0, help="stop after N s (0 = Ctrl+C)")
    ap.add_argument("--stepdown-after", type=float, default=0,
                    help="force a stepDown after N seconds (0 = do it manually)")
    ap.add_argument("--read-pref", default="primary",
                    choices=["primary", "primaryPreferred", "secondary", "secondaryPreferred", "nearest"])
    args = ap.parse_args()

    tracker = ServerTracker()
    client = MongoClient(
        RS_URI,
        readPreference=args.read_pref,
        event_listeners=[tracker],
        serverSelectionTimeoutMS=3000,   # fail fast so outages are visible
        retryReads=True,                 # default; one automatic retry per read
    )
    coll = client["labdb"]["heartbeat"]
    coll.update_one({"_id": "hb"}, {"$set": {"started": datetime.now()}}, upsert=True)

    if args.stepdown_after:
        threading.Thread(target=force_stepdown, args=(args.stepdown_after,), daemon=True).start()

    print(f"Read preference: {args.read_pref}.  Press Ctrl+C to stop.\n")
    print(f"{'TIME':<14}{'STATUS':<9}{'SERVED BY':<15}{'ROLE':<11}{'LATENCY':<10}NOTE")

    reads = failures = failovers = 0
    current = None
    outage_start = None
    longest_outage = 0.0
    t_end = time.time() + args.duration if args.duration else None

    with open("failover_log.csv", "w", newline="") as fh:
        log = csv.writer(fh)
        log.writerow(["time", "status", "served_by", "role", "latency_ms", "note"])
        try:
            while t_end is None or time.time() < t_end:
                t0 = time.perf_counter()
                note = ""
                try:
                    coll.find_one({"_id": "hb"})
                    latency = (time.perf_counter() - t0) * 1000
                    server = tracker.last_server
                    primary = client.primary
                    role = "PRIMARY" if primary and f"{primary[0]}:{primary[1]}" == server else "SECONDARY"
                    reads += 1

                    if outage_start is not None:
                        gap = time.perf_counter() - outage_start
                        longest_outage = max(longest_outage, gap)
                        note = f"RECOVERED after {gap * 1000:.0f} ms"
                        outage_start = None
                    if current and server != current:
                        failovers += 1
                        note = (note + "; " if note else "") + f"SWITCHED {current} -> {server}"
                    current = server
                    row = [now(), "OK", server, role, f"{latency:.1f}", note]
                except PyMongoError as exc:
                    failures += 1
                    if outage_start is None:
                        outage_start = t0
                    row = [now(), "ERROR", "-", "-", "-", type(exc).__name__]

                print(f"{row[0]:<14}{row[1]:<9}{row[2]:<15}{row[3]:<11}"
                      f"{(row[4] + ' ms') if row[4] != '-' else '-':<10}{row[5]}")
                log.writerow(row)
                fh.flush()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass

    print("\n" + "=" * 60)
    print(f"Successful reads : {reads}")
    print(f"Failed reads     : {failures}")
    print(f"Member switches  : {failovers}")
    print(f"Longest outage   : {longest_outage * 1000:.0f} ms")
    print(f"Final member     : {current}")
    print("Full log written to failover_log.csv")
    client.close()


if __name__ == "__main__":
    main()

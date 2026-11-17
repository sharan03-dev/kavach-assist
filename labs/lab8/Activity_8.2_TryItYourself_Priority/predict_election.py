"""
Activity 8.2 (Try it yourself) - predict the election winner from member
priorities, force a stepDown, and verify the prediction.

Run AFTER set_priorities.js:
    python predict_election.py
    python predict_election.py --watch-takeover   # also watch mongo1 reclaim primary

Prediction rule (simplified MongoDB election logic)
  Eligible = healthy SECONDARY members with votes > 0 and priority > 0,
             excluding the primary being stepped down.
  Winner   = eligible member with the highest priority.
  A lower-priority node may win the first election if it happens to call it
  first, but the higher-priority node then performs a "priority takeover",
  so we judge the prediction on the primary the set SETTLES on.
"""
import argparse
import os
import time
from datetime import datetime

from pymongo import MongoClient
from pymongo.errors import PyMongoError

RS_URI = os.getenv(
    "MONGO_URI",
    "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0",
)
STEPDOWN_SECS = 60


def now():
    return datetime.now().strftime("%H:%M:%S")


def members(client):
    cfg = {m["host"]: m for m in client.admin.command("replSetGetConfig")["config"]["members"]}
    status = client.admin.command("replSetGetStatus")
    for m in status["members"]:
        m["priority"] = cfg[m["name"]]["priority"]
        m["votes"] = cfg[m["name"]]["votes"]
    return status


def current_primary(client):
    try:
        st = client.admin.command("replSetGetStatus")
        return next((m["name"] for m in st["members"] if m["stateStr"] == "PRIMARY"), None)
    except PyMongoError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--settle", type=int, default=20, help="seconds to observe after stepDown")
    ap.add_argument("--watch-takeover", action="store_true",
                    help=f"keep watching for {STEPDOWN_SECS}s+ to see the old primary reclaim")
    args = ap.parse_args()

    client = MongoClient(RS_URI, serverSelectionTimeoutMS=5000)
    st = members(client)

    # ---- 1. Show config ----------------------------------------------------
    print("=" * 62 + "\n1. Current members\n" + "=" * 62)
    print(f"{'MEMBER':<15}{'STATE':<11}{'PRIORITY':<10}{'VOTES':<7}HEALTH")
    for m in st["members"]:
        print(f"{m['name']:<15}{m['stateStr']:<11}{m['priority']:<10}{m['votes']:<7}{m['health']}")

    old_primary = next(m["name"] for m in st["members"] if m["stateStr"] == "PRIMARY")

    # ---- 2. Predict ----------------------------------------------------------
    print("\n" + "=" * 62 + "\n2. Prediction\n" + "=" * 62)
    eligible = [m for m in st["members"]
                if m["stateStr"] == "SECONDARY" and m["health"] == 1
                and m["priority"] > 0 and m["votes"] > 0]
    eligible.sort(key=lambda m: m["priority"], reverse=True)
    for m in eligible:
        print(f"  eligible: {m['name']} (priority {m['priority']})")
    if len(eligible) > 1 and eligible[0]["priority"] == eligible[1]["priority"]:
        print("  Tie on priority -> winner decided by optime freshness/timing; prediction uncertain.")
    predicted = eligible[0]["name"]
    print(f"\nPREDICTED new primary after {old_primary} steps down: {predicted}")

    # ---- 3. Force the election ------------------------------------------------
    print("\n" + "=" * 62 + "\n3. Forcing stepDown\n" + "=" * 62)
    try:
        client.admin.command("replSetStepDown", STEPDOWN_SECS, secondaryCatchUpPeriodSecs=10)
    except PyMongoError as exc:
        print(f"(connection reset during stepDown: {type(exc).__name__} - expected)")
    t0 = time.time()

    history = []
    while time.time() - t0 < args.settle:
        p = current_primary(client)
        if not history or history[-1][1] != p:
            history.append((round(time.time() - t0, 1), p))
            print(f"{now()}  t+{history[-1][0]:>5}s  PRIMARY = {p or '(election in progress)'}")
        time.sleep(0.5)

    settled = history[-1][1]
    first_winner = next((p for _, p in history if p and p != old_primary), None)

    # ---- 4. Verify -----------------------------------------------------------
    print("\n" + "=" * 62 + "\n4. Result\n" + "=" * 62)
    print(f"Predicted        : {predicted}")
    print(f"First elected    : {first_winner}")
    print(f"Settled primary  : {settled}")
    if settled == predicted:
        print("PREDICTION VERIFIED")
        if first_winner != predicted:
            print("(a lower-priority node won briefly, then a priority takeover occurred)")
    else:
        print("PREDICTION NOT MET - check health/optime of the expected member")

    # ---- 5. Optional: priority takeover by the old primary --------------------
    if args.watch_takeover:
        print(f"\nWatching for {old_primary} (highest priority) to reclaim primary "
              f"after its {STEPDOWN_SECS}s stepDown window...")
        deadline = t0 + STEPDOWN_SECS + 30
        while time.time() < deadline:
            p = current_primary(client)
            if p == old_primary:
                print(f"{now()}  t+{time.time() - t0:.0f}s  PRIORITY TAKEOVER: {p} is PRIMARY again")
                break
            time.sleep(1)
        else:
            print("No takeover observed in the window.")

    client.close()


if __name__ == "__main__":
    main()

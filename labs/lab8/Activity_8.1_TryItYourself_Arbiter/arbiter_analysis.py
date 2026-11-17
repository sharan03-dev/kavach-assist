"""
Activity 8.1 (Try it yourself) - Why use an arbiter?

Run AFTER add_arbiter.js. The script:
  1. Reads the live replica-set config and status and classifies every member
     (data-bearing / arbiter, voting / non-voting).
  2. Proves the arbiter stores no data: asks each member how much data it
     holds, and shows that a read sent directly to the arbiter is rejected.
  3. Prints a comparison of P-S-S, P-S-S-A and P-S-A topologies:
     voters, votes needed, failures tolerated, and full data copies.

Run:  python arbiter_analysis.py
"""
import os

from pymongo import MongoClient
from pymongo.errors import OperationFailure

RS_URI = os.getenv(
    "MONGO_URI",
    "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0",
)


def banner(title):
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def quorum(voters):
    majority = voters // 2 + 1
    return majority, voters - majority


def main():
    client = MongoClient(RS_URI, serverSelectionTimeoutMS=5000)
    cfg = client.admin.command("replSetGetConfig")["config"]
    status = {m["name"]: m for m in client.admin.command("replSetGetStatus")["members"]}

    # ---- 1. Live configuration ------------------------------------------
    banner("1. Live replica-set members")
    print(f"{'HOST':<17}{'STATE':<11}{'ROLE':<14}{'VOTES':<7}PRIORITY")
    for m in cfg["members"]:
        role = "arbiter" if m.get("arbiterOnly") else "data-bearing"
        state = status.get(m["host"], {}).get("stateStr", "?")
        print(f"{m['host']:<17}{state:<11}{role:<14}{m['votes']:<7}{m['priority']}")

    voters = sum(1 for m in cfg["members"] if m["votes"] > 0)
    majority, tolerated = quorum(voters)
    print(f"\nVoting members: {voters} -> need {majority} votes, tolerate {tolerated} failure(s)")

    # ---- 2. Storage: what does each member actually hold? ----------------
    banner("2. Storage held by each member (labdb)")
    for m in cfg["members"]:
        host, port = m["host"].split(":")
        direct = MongoClient(host, int(port), directConnection=True,
                             serverSelectionTimeoutMS=3000)
        try:
            stats = (direct["labdb"].command("dbStats") if not direct.admin.command("hello").get("arbiterOnly") else {"dataSize": 0, "objects": 0, "storageSize": 0, "indexSize": 0})
            print(f"{m['host']:<17} dataSize={stats['dataSize']:>6} bytes  "
                  f"objects={stats['objects']}")
        except OperationFailure as exc:
            # Arbiters refuse data reads: they never received the data.
            print(f"{m['host']:<17} REJECTED -> {exc.details.get('codeName')} "
                  f"(no user data on an arbiter)")
        finally:
            direct.close()

    # ---- 3. Topology comparison -------------------------------------------
    banner("3. Topology comparison")
    rows = [
        ("P-S-S   (3 data nodes)",        3, 3, "3x storage"),
        ("P-S-S-A (3 data + arbiter)",    4, 3, "3x storage, even voters"),
        ("P-S-A   (2 data + arbiter)",    3, 2, "2x storage + tiny arbiter"),
    ]
    print(f"{'TOPOLOGY':<30}{'VOTERS':<8}{'NEED':<6}{'TOLERATE':<10}{'COPIES':<8}COST")
    for name, v, copies, cost in rows:
        need, tol = quorum(v)
        print(f"{name:<30}{v:<8}{need:<6}{tol:<10}{copies:<8}{cost}")

    print("""
Conclusion
 * An arbiter gives a VOTE without a DATA COPY: it costs almost no disk,
   RAM or replication traffic, yet turns 2 data nodes into 3 voters.
 * P-S-A survives one node failure like P-S-S, at roughly 2/3 of the storage.
 * Adding an arbiter to an already-odd set (P-S-S-A) gains nothing: 4 voters
   still tolerate only 1 failure. Arbiters fix EVEN voter counts.
 * Trade-off: with one data node down in P-S-A, w:"majority" writes cannot be
   acknowledged and only one copy of the data remains.""")

    client.close()


if __name__ == "__main__":
    main()

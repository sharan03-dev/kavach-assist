"""
Activity 8.1 - Deploy a local replica set and verify replication.

Steps
  1. Connect to the replica set (the driver discovers the topology itself).
  2. Insert a document on the PRIMARY with write concern w=3 (all members).
  3. Connect DIRECTLY to each secondary and read the document back.
  4. Run replSetGetStatus (driver equivalent of rs.status()) and print each
     member's role, health and replication lag.

Run:  python verify_replication.py
"""
import os
from datetime import datetime, timezone

from pymongo import MongoClient, ReadPreference, WriteConcern

RS_URI = os.getenv(
    "MONGO_URI",
    "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0",
)


def banner(title):
    print("\n" + "=" * 64 + f"\n{title}\n" + "=" * 64)


def main():
    client = MongoClient(RS_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")  # force topology discovery

    banner("1. Topology discovered by the driver")
    print(f"Primary     : {client.primary}")
    print(f"Secondaries : {sorted(client.secondaries)}")

    # ---- 2. Insert on primary, acknowledged by all 3 members -------------
    banner("2. Insert on PRIMARY with writeConcern w=3")
    coll = client["labdb"].get_collection(
        "replication_test", write_concern=WriteConcern(w=3, wtimeout=5000)
    )
    doc = {"msg": "Hello from Lab 8.1 (Python)", "createdAt": datetime.now(timezone.utc)}
    inserted_id = coll.insert_one(doc).inserted_id
    print(f"Inserted _id={inserted_id} (acknowledged by 3/3 members)")

    # ---- 3. Read it back from each secondary directly ---------------------
    banner("3. Read the document directly from each SECONDARY")
    for host, port in sorted(client.secondaries):
        direct = MongoClient(
            host, port, directConnection=True,
            read_preference=ReadPreference.SECONDARY_PREFERRED,
            serverSelectionTimeoutMS=3000,
        )
        found = direct["labdb"]["replication_test"].find_one({"_id": inserted_id})
        print(f"{host}:{port:<6} -> {'FOUND   ' + found['msg'] if found else 'MISSING'}")
        direct.close()

    # ---- 4. rs.status() equivalent ----------------------------------------
    banner("4. rs.status() - members, roles and health")
    status = client.admin.command("replSetGetStatus")
    primary_optime = next(
        m["optimeDate"] for m in status["members"] if m["stateStr"] == "PRIMARY"
    )
    print(f"Replica set: {status['set']}   election term: {status['term']}")
    print(f"{'MEMBER':<15}{'STATE':<11}{'HEALTH':<8}{'LAG(s)':<8}UPTIME(s)")
    for m in status["members"]:
        lag = (primary_optime - m["optimeDate"]).total_seconds()
        health = "UP" if m["health"] == 1 else "DOWN"
        print(f"{m['name']:<15}{m['stateStr']:<11}{health:<8}{lag:<8.0f}{m.get('uptime', 0)}")

    client.close()


if __name__ == "__main__":
    main()

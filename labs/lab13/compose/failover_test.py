"""Lab 13.1 step 4 - connect PyMongo to the replica set and verify failover works (standalone version).

Run on the compose network (the member names prod-mongo-N only resolve there):   ./failover_test.sh
Reads continuously (every 100 ms) and writes with retryable writes while the PRIMARY runs rs.stepDown().
The full project runs a longer version (also a real crash) from backend/app/lab13.py.
"""
import argparse
import threading
import time

from pymongo import MongoClient
from pymongo.errors import PyMongoError

ap = argparse.ArgumentParser()
ap.add_argument("--password", required=True)
ap.add_argument("--seconds", type=int, default=25)
a = ap.parse_args()
uri = "mongodb://prod-mongo-1:27017,prod-mongo-2:27017,prod-mongo-3:27017/?replicaSet=rs0&authSource=admin"
c = MongoClient(uri, username="admin", password=a.password, serverSelectionTimeoutMS=2000, retryWrites=True, w="majority")
coll = c.lab13.failover_standalone
coll.drop()
t0, stop, log = time.time(), threading.Event(), []


def reader():
    n = 0
    while not stop.is_set():
        n += 1
        try:
            coll.insert_one({"_id": n, "n": n})            # retryable write: retried once by the driver
            last = coll.find_one(sort=[("n", -1)])
            log.append((round(time.time() - t0, 2), "ok", f"{c.primary[0]}", last["n"]))
        except PyMongoError as exc:
            log.append((round(time.time() - t0, 2), "ERR", type(exc).__name__, n))
        time.sleep(0.1)


th = threading.Thread(target=reader)
th.start()
time.sleep(5)
old = c.primary
print(f"t=5 s  primary {old[0]} -> rs.stepDown(30)")
try:
    c.admin.command("replSetStepDown", 30)
except PyMongoError as exc:
    print(f"   ({type(exc).__name__}: expected, the old primary drops its connections)")
time.sleep(a.seconds - 5)
stop.set()
th.join()
errors = [x for x in log if x[1] == "ERR"]
switch = next((x for x in log if x[1] == "ok" and x[2] != old[0]), None)
print(f"reads ok {len(log) - len(errors)}, errors {len(errors)}, new primary {c.primary[0]} first used at t={switch and switch[0]} s")
acked = sum(1 for x in log if x[1] == "ok")
print(f"acknowledged writes {acked}, stored {coll.count_documents({})} - nothing acknowledged was lost: "
      f"{coll.count_documents({}) >= acked}")

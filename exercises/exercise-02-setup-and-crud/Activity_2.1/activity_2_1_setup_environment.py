"""
23CSE528: NoSQL Databases using MongoDB
Exercise 2 - Lab 2.1: Environment Setup Verification
Student : M Nagasai Girisharan (24BTRCL114)

Checks, in order: Python version, PyMongo driver, local (Docker) MongoDB,
MongoDB Atlas (if MONGO_URI_ATLAS is set), and a live write/read/delete
round trip. The checklist is saved in MongoDB and exported to
environment_check.json.
"""

import os
import platform
import sys
from datetime import datetime, timezone

from bson import json_util

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
ATLAS_URI = os.getenv("MONGO_URI_ATLAS", "")
HERE = os.path.dirname(os.path.abspath(__file__))
results = []


def check(name, ok, detail):
    results.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name:<26} {detail}")
    return ok


def main():
    print("=" * 74)
    print("LAB 2.1 : ENVIRONMENT SETUP VERIFICATION")
    print("=" * 74)

    # 1. Python version
    check("Python >= 3.10", sys.version_info >= (3, 10),
          f"{platform.python_version()} ({platform.system()})")

    # 2. PyMongo driver
    try:
        import pymongo
        from pymongo import MongoClient
        check("PyMongo installed", True, f"pymongo {pymongo.version}")
    except ImportError:
        check("PyMongo installed", False, "run: pip install pymongo")
        return

    # 3. Local / Docker MongoDB
    local = None
    try:
        local = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        info = local.server_info()
        check("Local MongoDB reachable", True,
              f"{MONGO_URI}  version {info['version']}")
    except Exception as e:
        check("Local MongoDB reachable", False,
              "start it: docker run -d --name mongo -p 27017:27017 mongo:7")
        local = None

    # 4. Atlas (optional)
    if ATLAS_URI:
        try:
            atlas = MongoClient(ATLAS_URI, serverSelectionTimeoutMS=5000)
            check("MongoDB Atlas reachable", True,
                  f"version {atlas.server_info()['version']}")
        except Exception as e:
            check("MongoDB Atlas reachable", False, type(e).__name__)
    else:
        print("[SKIP] MongoDB Atlas reachable    MONGO_URI_ATLAS not set (optional)")

    # 5. CRUD round trip on the local server
    if local is not None:
        col = local["nosql_lab2"]["env_test"]
        col.delete_many({})
        ins = col.insert_one({"msg": "hello mongo", "at": datetime.now(timezone.utc)})
        found = col.find_one({"_id": ins.inserted_id})
        check("Write + read round trip", found is not None and found["msg"] == "hello mongo",
              f"inserted _id {ins.inserted_id}")
        print("\nDatabases on server:", ", ".join(local.list_database_names()))

        # save checklist
        report = local["nosql_lab2"]["environment_check"]
        report.drop()
        report.insert_many([dict(r) for r in results])
        with open(os.path.join(HERE, "environment_check.json"), "w") as f:
            f.write(json_util.dumps(list(report.find()), indent=2))

    passed = sum(r["status"] == "PASS" for r in results)
    print("-" * 74)
    print(f"Core checks passed: {passed}/{len(results)}")
    if passed == len(results):
        print("You are ready to start the labs!")
    print("[OK] Exported checklist -> environment_check.json")


if __name__ == "__main__":
    main()

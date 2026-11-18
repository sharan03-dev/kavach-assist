"""
23CSE528: NoSQL Databases using MongoDB
Exercise 4 - Activity for 4.2 (Try it yourself)
Student : M Nagasai Girisharan (24BTRCL114)

Idempotent bulk import: a function that can be run many times on the same
input without creating duplicates, using upsert logic for every document.

 * Each record is matched on its natural key (sku) -> UpdateOne(upsert=True).
 * $set writes the latest values, $setOnInsert sets created_at only once,
   so re-running with unchanged data modifies nothing (modified = 0).
 * A unique index on sku guarantees no duplicates even under concurrency.
Running the import 3 times gives the same final state.
Collection exported to catalog.json.
"""

import os
from datetime import datetime, timezone

from bson import json_util
from pymongo import MongoClient, UpdateOne

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab4"
HERE = os.path.dirname(os.path.abspath(__file__))

IMPORT_FILE = [   # e.g. rows from a supplier CSV
    {"sku": "KB-100", "name": "Mechanical Keyboard", "price": 89.50, "stock": 20},
    {"sku": "MS-200", "name": "Wireless Mouse", "price": 24.99, "stock": 75},
    {"sku": "HB-300", "name": "USB-C Hub", "price": 45.00, "stock": 30},
    {"sku": "WC-400", "name": "Webcam HD", "price": 49.99, "stock": 12},
    {"sku": "HS-500", "name": "Headset", "price": 59.00, "stock": 8},
    {"sku": "MS-200", "name": "Wireless Mouse", "price": 24.99, "stock": 75},  # duplicate row
]


def idempotent_import(col, records):
    """Upsert every record by sku. Safe to run any number of times."""
    now = datetime.now(timezone.utc)
    ops = [
        UpdateOne(
            {"sku": r["sku"]},
            {"$set": {k: v for k, v in r.items() if k != "sku"},
             "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        for r in records
    ]
    return col.bulk_write(ops, ordered=False)


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["catalog"]
    col.drop()
    col.create_index("sku", unique=True)

    print("=" * 76)
    print("LAB 4.2 TRY IT YOURSELF : IDEMPOTENT BULK IMPORT")
    print("=" * 76)
    print(f"Import file has {len(IMPORT_FILE)} rows "
          f"({len({r['sku'] for r in IMPORT_FILE})} unique SKUs, 1 duplicate row)\n")

    print(f"{'Run':<5}{'upserted':>9}{'matched':>9}{'modified':>10}{'docs in collection':>21}")
    print("-" * 76)
    counts = []
    for run in range(1, 4):
        r = idempotent_import(col, IMPORT_FILE)
        counts.append(col.count_documents({}))
        print(f"{run:<5}{r.upserted_count:>9}{r.matched_count:>9}{r.modified_count:>10}{counts[-1]:>21}")

    print("-" * 76)
    print(f"Document count after every run: {counts} -> "
          f"{'IDEMPOTENT (no duplicates)' if len(set(counts)) == 1 else 'NOT idempotent'}")

    # a changed price in a later file updates in place, still no duplicate
    idempotent_import(col, [{"sku": "HS-500", "name": "Headset", "price": 54.00, "stock": 10}])
    h = col.find_one({"sku": "HS-500"})
    print(f"Re-import with new price for HS-500 -> price {h['price']}, stock {h['stock']}, "
          f"total docs still {col.count_documents({})}")
    dup = list(col.aggregate([{"$group": {"_id": "$sku", "n": {"$sum": 1}}},
                              {"$match": {"n": {"$gt": 1}}}]))
    print(f"Duplicate SKUs found by $group check: {len(dup)}")

    with open(os.path.join(HERE, "catalog.json"), "w") as f:
        f.write(json_util.dumps(list(col.find()), indent=2))
    print("[OK] Exported collection -> catalog.json")
    client.close()


if __name__ == "__main__":
    main()

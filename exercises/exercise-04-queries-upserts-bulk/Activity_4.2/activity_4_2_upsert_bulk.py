"""
23CSE528: NoSQL Databases using MongoDB
Exercise 4 - Lab 4.2: Implement Upsert and Bulk Operations
Student : M Nagasai Girisharan (24BTRCL114)

1. upsert_price(sku, price): if the SKU exists update its price, otherwise
   insert a new document with default values (update_one with upsert=True).
2. One bulk_write with 5 UpdateOne operations: stock +10 for products A, B, C
   and is_active = False for products D and E. Verify the BulkWriteResult
   (matched / modified / upserted counts).
Collection exported to inventory.json.
"""

import os
from datetime import datetime, timezone

from bson import json_util
from pymongo import MongoClient, UpdateOne

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab4"
HERE = os.path.dirname(os.path.abspath(__file__))

SEED = [
    {"sku": "A", "name": "Wireless Mouse", "price": 24.99, "stock": 50, "is_active": True},
    {"sku": "B", "name": "Mechanical Keyboard", "price": 89.50, "stock": 20, "is_active": True},
    {"sku": "C", "name": "USB-C Hub", "price": 45.00, "stock": 5, "is_active": True},
    {"sku": "D", "name": "VGA Adapter", "price": 12.00, "stock": 3, "is_active": True},
    {"sku": "E", "name": "Floppy Drive", "price": 19.00, "stock": 1, "is_active": True},
]


def upsert_price(col, sku, price):
    """Update price if SKU exists, else insert a new product with defaults."""
    res = col.update_one(
        {"sku": sku},
        {"$set": {"price": price, "updated_at": datetime.now(timezone.utc)},
         "$setOnInsert": {"name": f"New product {sku}", "stock": 0,
                          "is_active": True, "tags": []}},
        upsert=True,
    )
    action = "INSERTED (upsert)" if res.upserted_id else "UPDATED price"
    print(f"  upsert_price('{sku}', {price:<6}) -> {action:<18} matched={res.matched_count} "
          f"modified={res.modified_count} upserted_id={res.upserted_id}")
    return res


def table(col):
    print(f"  {'SKU':<5}{'Name':<22}{'Price':>8}{'Stock':>7}  Active")
    for p in col.find().sort("sku", 1):
        print(f"  {p['sku']:<5}{p['name']:<22}{p['price']:>8.2f}{p['stock']:>7}  {p['is_active']}")


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["inventory"]
    col.drop()
    col.create_index("sku", unique=True)
    col.insert_many(SEED)

    print("=" * 76)
    print("LAB 4.2 : UPSERT AND BULK OPERATIONS")
    print("=" * 76)
    print("Initial inventory:")
    table(col)

    print("\n[1] upsert_price() with update_one(..., upsert=True)")
    print("-" * 76)
    upsert_price(col, "A", 22.49)        # existing -> update
    upsert_price(col, "F", 15.00)        # new -> insert with defaults
    upsert_price(col, "F", 14.50)        # now exists -> update

    print("\n[2] bulk_write with 5 UpdateOne operations")
    print("-" * 76)
    ops = [UpdateOne({"sku": s}, {"$inc": {"stock": 10}}) for s in ("A", "B", "C")] + \
          [UpdateOne({"sku": s}, {"$set": {"is_active": False}}) for s in ("D", "E")]
    before = {p["sku"]: p for p in col.find()}
    result = col.bulk_write(ops, ordered=True)
    print(f"  acknowledged   : {result.acknowledged}")
    print(f"  matched_count  : {result.matched_count}")
    print(f"  modified_count : {result.modified_count}")
    print(f"  upserted_count : {result.upserted_count}")
    print(f"  inserted_count : {result.inserted_count}")
    ok = result.matched_count == 5 and result.modified_count == 5 and result.upserted_count == 0
    print(f"  verification   : {'PASSED - 5 matched, 5 modified, 0 upserted' if ok else 'FAILED'}")

    print("\n  Per-product changes:")
    for p in col.find({"sku": {"$in": ["A", "B", "C", "D", "E"]}}).sort("sku", 1):
        b = before[p["sku"]]
        print(f"   {p['sku']}: stock {b['stock']:>3} -> {p['stock']:<3}  "
              f"is_active {b['is_active']} -> {p['is_active']}")

    print("\nFinal inventory:")
    table(col)

    with open(os.path.join(HERE, "inventory.json"), "w") as f:
        f.write(json_util.dumps(list(col.find()), indent=2))
    print("\n[OK] Exported collection -> inventory.json")
    client.close()


if __name__ == "__main__":
    main()

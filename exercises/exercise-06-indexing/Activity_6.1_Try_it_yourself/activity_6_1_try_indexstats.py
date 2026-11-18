"""
23CSE528: NoSQL Databases using MongoDB
Exercise 6 - Activity for 6.1 (Try it yourself)
Student : M Nagasai Girisharan (24BTRCL114)

Use the $indexStats aggregation stage to measure actual index usage after
running the five Lab 6.1 queries multiple times, and identify which indexes
are used most frequently. A realistic workload is simulated: each query runs
a different number of times (e.g. category browsing is the most common).
Requires the collection + indexes from Lab 6.1 (run that script first).
Results exported to index_stats.json.
"""

import os
import random

from bson import json_util
from pymongo import MongoClient, ASCENDING, DESCENDING

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab6"
HERE = os.path.dirname(os.path.abspath(__file__))
CATEGORIES = ["Electronics", "Books", "Clothing", "Home", "Sports", "Toys", "Beauty", "Grocery"]
random.seed(7)

# query -> how many times it runs in the simulated workload
WORKLOAD = [
    ("Q1 category + sort price", 120,
     lambda c: c.find({"category": random.choice(CATEGORIES)}).sort("price", ASCENDING).limit(20)),
    ("Q2 price range", 60,
     lambda c: (lambda lo: c.find({"price": {"$gte": lo, "$lte": lo + 50}}).limit(20))(random.randint(1, 900))),
    ("Q3 tag filter", 35,
     lambda c: c.find({"tags": random.choice(["sale", "new", "limited", "gift"])}).limit(20)),
    ("Q4 text search", 20,
     lambda c: c.find({"$text": {"$search": random.choice(["camera", "lamp", "watch"])}}).limit(20)),
    ("Q5 top rated in stock", 10,
     lambda c: c.find({"rating": {"$gte": 4.5}, "stock": {"$gt": 0}}).sort("rating", DESCENDING).limit(20)),
]


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["products"]
    if col.count_documents({}) == 0 or len(col.index_information()) < 6:
        print("Run Lab 6.1 (activity_6_1_index_design.py) first.")
        return

    print("=" * 76)
    print("LAB 6.1 TRY IT YOURSELF : MEASURE INDEX USAGE WITH $indexStats")
    print("=" * 76)
    base = {s["name"]: s["accesses"]["ops"] for s in col.aggregate([{"$indexStats": {}}])}

    print("Simulated workload:")
    for label, times, fn in WORKLOAD:
        for _ in range(times):
            list(fn(col))
        print(f"  ran {label:<26} x {times}")

    stats = list(col.aggregate([
        {"$indexStats": {}},
        {"$project": {"name": 1, "key": 1, "ops": "$accesses.ops", "since": "$accesses.since"}},
        {"$sort": {"ops": -1}},
    ]))

    print("\n$indexStats result (ops counted during this workload):")
    print("-" * 76)
    total = sum(s["ops"] - base.get(s["name"], 0) for s in stats) or 1
    print(f"  {'Index':<22}{'Key':<30}{'ops':>6}{'share':>8}")
    for s in stats:
        ops = s["ops"] - base.get(s["name"], 0)
        s["workload_ops"] = ops
        key = ", ".join(f"{k}:{v}" for k, v in s["key"].items())
        bar = "#" * round(ops / total * 30)
        print(f"  {s['name']:<22}{key:<30}{ops:>6}{ops / total * 100:>7.1f}%  {bar}")

    ranked = sorted(stats, key=lambda s: s["workload_ops"], reverse=True)
    print("\nMost frequently used index :", ranked[0]["name"])
    unused = [s["name"] for s in stats if s["workload_ops"] == 0]
    print("Unused during workload     :", ", ".join(unused) or "none")
    print("Conclusion: category_1_price_1 serves the most common query; an index")
    print("with 0 ops over a long period is a candidate to drop (it only slows writes).")

    with open(os.path.join(HERE, "index_stats.json"), "w") as f:
        f.write(json_util.dumps(stats, indent=2))
    print("[OK] Exported $indexStats -> index_stats.json")
    client.close()


if __name__ == "__main__":
    main()

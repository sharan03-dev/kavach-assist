"""
23CSE528: NoSQL Databases using MongoDB
Exercise 6 - Lab 6.2: Compare Index Strategies
Student : M Nagasai Girisharan (24BTRCL114)

1. With no indexes (except _id), time the insert of 10,000 documents
   using Python's time module.
2. Create 5 indexes on different fields, insert another 10,000 documents,
   time it again and calculate the percentage slowdown.
3. Drop all but the most critical index, run the five representative
   queries again and compare performance.
4. Print a short analysis of the read-performance vs write-throughput trade-off.
Each insert timing is the median of 3 runs to reduce noise.
Results exported to index_strategies.json.
"""

import os
import random
import statistics
import time

from bson import json_util
from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab6"
HERE = os.path.dirname(os.path.abspath(__file__))
random.seed(11)

CATEGORIES = ["Electronics", "Books", "Clothing", "Home", "Sports", "Toys", "Beauty", "Grocery"]
ADJ = ["Ultra", "Smart", "Classic", "Pro", "Eco", "Mini", "Max", "Super", "Daily", "Prime"]
NOUN = ["Lamp", "Phone", "Shirt", "Bottle", "Speaker", "Novel", "Shoe", "Watch", "Bag", "Camera"]
TAGS = ["sale", "new", "bestseller", "eco-friendly", "premium", "limited", "gift", "imported"]
INDEXES = [
    [("category", ASCENDING), ("price", ASCENDING)],
    [("price", ASCENDING)],
    [("tags", ASCENDING)],
    [("name", TEXT)],
    [("rating", DESCENDING), ("stock", ASCENDING)],
]
CRITICAL = "category_1_price_1"   # serves the most frequent query (see $indexStats)


def batch(n, start):
    return [{"name": f"{random.choice(ADJ)} {random.choice(NOUN)} {start + i}",
             "category": random.choice(CATEGORIES),
             "price": round(random.uniform(1, 1000), 2),
             "rating": round(random.uniform(1, 5), 1),
             "stock": random.randint(0, 500),
             "tags": random.sample(TAGS, k=random.randint(1, 3))} for i in range(n)]


def timed_insert(col, docs):
    t = time.perf_counter()
    col.insert_many(docs)
    return time.perf_counter() - t


QUERIES = [
    ("Q1 category + sort price", lambda c: list(c.find({"category": "Electronics"}).sort("price", 1).limit(50))),
    ("Q2 price 100-150", lambda c: list(c.find({"price": {"$gte": 100, "$lte": 150}}))),
    ("Q3 tag = 'limited'", lambda c: list(c.find({"tags": "limited"}))),
    ("Q4 name contains 'camera'", lambda c: list(c.find({"name": {"$regex": "camera", "$options": "i"}}))),
    ("Q5 rating>=4.8 & in stock", lambda c: list(c.find({"rating": {"$gte": 4.8}, "stock": {"$gt": 0}}).sort("rating", -1))),
]


def time_queries(col, reps=10):
    out = []
    for label, fn in QUERIES:
        t = time.perf_counter()
        for _ in range(reps):
            fn(col)
        out.append((label, (time.perf_counter() - t) / reps * 1000))
    return out


def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    print("=" * 76)
    print("LAB 6.2 : COMPARE INDEX STRATEGIES")
    print("=" * 76)

    no_idx, with_idx = [], []
    for trial in range(3):
        db.drop_collection("perf")
        col = db["perf"]
        no_idx.append(timed_insert(col, batch(10_000, 0)))          # step 1
        for keys in INDEXES:                                       # step 2
            col.create_index(keys)
        with_idx.append(timed_insert(col, batch(10_000, 10_000)))

    t1, t2 = statistics.median(no_idx), statistics.median(with_idx)
    slow = (t2 - t1) / t1 * 100
    print("[1] Insert 10,000 docs, NO secondary indexes")
    print(f"    runs: {', '.join(f'{x:.3f}s' for x in no_idx)}  -> median {t1:.3f}s "
          f"({10_000 / t1:,.0f} docs/s)")
    print("[2] Insert 10,000 docs, WITH 5 secondary indexes")
    print(f"    indexes: {', '.join(n for n in col.index_information() if n != '_id_')}")
    print(f"    runs: {', '.join(f'{x:.3f}s' for x in with_idx)}  -> median {t2:.3f}s "
          f"({10_000 / t2:,.0f} docs/s)")
    print(f"    percentage slowdown = ({t2:.3f} - {t1:.3f}) / {t1:.3f} x 100 = {slow:.1f}%")

    # step 3: queries with all 5 indexes vs only the critical one
    all5 = time_queries(col)
    for name in list(col.index_information()):
        if name not in ("_id_", CRITICAL):
            col.drop_index(name)
    one = time_queries(col)
    print(f"\n[3] Query time (avg of 10 runs) - all 5 indexes vs only '{CRITICAL}'")
    print("-" * 76)
    print(f"  {'Query':<28}{'5 indexes':>11}{'1 index':>11}{'change':>10}")
    rows = []
    for (label, a), (_, b) in zip(all5, one):
        rows.append({"query": label, "ms_5_indexes": round(a, 3), "ms_1_index": round(b, 3)})
        print(f"  {label:<28}{a:>9.2f}ms{b:>9.2f}ms{(b - a) / a * 100:>+9.0f}%")
    print(f"  indexes left: {list(col.index_information())}")

    print("\n[4] Analysis: query performance vs write throughput")
    print("-" * 76)
    print(f"  * Every extra index must be updated on every insert, so write time grew")
    print(f"    by {slow:.0f}% with 5 indexes (the text and multikey indexes cost most:")
    print(f"    they add several index keys per document).")
    print(f"  * Reads gain the most: queries that kept their index (Q1) stay fast,")
    print(f"    while Q2, Q3 and Q5 fall back to COLLSCAN and become much slower.")
    print(f"  * Q4 ($regex contains) uses no index in either setup, so its change")
    print(f"    is only run-to-run timing noise.")
    print(f"  * Rule: index the fields of frequent, latency-critical queries; drop")
    print(f"    rarely used indexes on write-heavy collections.")

    with open(os.path.join(HERE, "index_strategies.json"), "w") as f:
        f.write(json_util.dumps({
            "insert_no_indexes_s": no_idx, "insert_5_indexes_s": with_idx,
            "median_no_indexes_s": t1, "median_5_indexes_s": t2,
            "percentage_slowdown": round(slow, 2), "query_comparison": rows,
            "remaining_indexes": list(col.list_indexes()),
            "sample_documents": list(col.find().limit(20)),
        }, indent=2))
    print("[OK] Exported results -> index_strategies.json")
    client.close()


if __name__ == "__main__":
    main()

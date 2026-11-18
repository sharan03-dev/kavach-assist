"""
23CSE528: NoSQL Databases using MongoDB
Exercise 6 - Lab 6.1: Index Design for a Query Workload
Student : M Nagasai Girisharan (24BTRCL114)

1. products collection with 50,000 generated documents (loop + random data):
   name, category, price, rating, stock, tags (array).
2. Five representative queries.
3. explain() on each BEFORE any index: record stage + totalDocsExamined.
4. Design indexes that cover all five queries, create them, run explain()
   again and compare before vs after.
Results (explain summaries, indexes, 100-doc sample) -> index_design.json.
"""

import os
import random
import time

from bson import json_util
from pymongo import MongoClient, ASCENDING, DESCENDING, TEXT

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab6"
HERE = os.path.dirname(os.path.abspath(__file__))
N = 50_000
random.seed(42)                                   # reproducible data

CATEGORIES = ["Electronics", "Books", "Clothing", "Home", "Sports", "Toys", "Beauty", "Grocery"]
ADJ = ["Ultra", "Smart", "Classic", "Pro", "Eco", "Mini", "Max", "Super", "Daily", "Prime"]
NOUN = ["Lamp", "Phone", "Shirt", "Bottle", "Speaker", "Novel", "Shoe", "Watch", "Bag",
        "Chair", "Camera", "Kettle", "Ball", "Cream", "Puzzle"]
TAGS = ["sale", "new", "bestseller", "eco-friendly", "premium", "limited", "gift", "imported"]


def make_product(i):
    return {
        "name": f"{random.choice(ADJ)} {random.choice(NOUN)} {i}",
        "category": random.choice(CATEGORIES),
        "price": round(random.uniform(1, 1000), 2),
        "rating": round(random.uniform(1, 5), 1),
        "stock": random.randint(0, 500),
        "tags": random.sample(TAGS, k=random.randint(1, 3)),
    }


# ---- the five representative queries ----
def queries(text_index_ready):
    q4 = ({"$text": {"$search": "camera"}} if text_index_ready
          else {"name": {"$regex": "camera", "$options": "i"}})
    return [
        ("Q1 category + sort by price", {"category": "Electronics"}, [("price", ASCENDING)]),
        ("Q2 price range 100-150", {"price": {"$gte": 100, "$lte": 150}}, None),
        ("Q3 tag = 'limited'", {"tags": "limited"}, None),
        ("Q4 text search 'camera' on name", q4, None),
        ("Q5 rating>=4.8 & in stock, top rated", {"rating": {"$gte": 4.8}, "stock": {"$gt": 0}},
         [("rating", DESCENDING)]),
    ]


def plan_stages(plan):
    """Turn the winning plan tree into 'FETCH > IXSCAN' style text."""
    if "queryPlan" in plan:
        plan = plan["queryPlan"]
    parts = []
    while plan:
        name = plan["stage"]
        if plan.get("indexName"):
            name += f"({plan['indexName']})"
        parts.append(name)
        plan = plan.get("inputStage") or (plan.get("inputStages") or [None])[0]
    return " > ".join(parts)


def run_explain(col, label, flt, sort):
    cur = col.find(flt)
    if sort:
        cur = cur.sort(sort)
    ex = cur.explain()
    st = ex["executionStats"]
    return {"query": label, "plan": plan_stages(ex["queryPlanner"]["winningPlan"]),
            "nReturned": st["nReturned"], "totalDocsExamined": st["totalDocsExamined"],
            "totalKeysExamined": st["totalKeysExamined"],
            "ms": st["executionTimeMillis"]}


def print_rows(rows):
    print(f"  {'Query':<38}{'Returned':>9}{'DocsExam':>10}{'KeysExam':>10}{'ms':>6}")
    for r in rows:
        print(f"  {r['query']:<38}{r['nReturned']:>9}{r['totalDocsExamined']:>10}"
              f"{r['totalKeysExamined']:>10}{r['ms']:>6}")
        print(f"      plan: {r['plan']}")


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["products"]
    col.drop()

    print("=" * 78)
    print("LAB 6.1 : INDEX DESIGN FOR A QUERY WORKLOAD")
    print("=" * 78)
    t = time.perf_counter()
    col.insert_many([make_product(i) for i in range(N)])
    print(f"[1] Inserted {col.count_documents({}):,} random products "
          f"in {time.perf_counter() - t:.2f}s")

    print("\n[3] BEFORE indexes (only _id_ exists)")
    print("-" * 78)
    before = [run_explain(col, *q) for q in queries(False)]
    print_rows(before)
    print("  (Q4 uses a case-insensitive $regex because $text needs a text index)")

    print("\n[4] Index design")
    print("-" * 78)
    design = [
        ([("category", ASCENDING), ("price", ASCENDING)], "Q1: equality on category, then sort on price (ESR rule)"),
        ([("price", ASCENDING)], "Q2: range scan on price"),
        ([("tags", ASCENDING)], "Q3: multikey index on the tags array"),
        ([("name", TEXT)], "Q4: text index enables $text search on name"),
        ([("rating", DESCENDING), ("stock", ASCENDING)], "Q5: range + sort on rating, stock filter in index"),
    ]
    for keys, why in design:
        name = col.create_index(keys)
        print(f"  created {name:<22} -> {why}")

    print("\n[4] AFTER indexes")
    print("-" * 78)
    after = [run_explain(col, *q) for q in queries(True)]
    print_rows(after)

    print("\nBefore vs After (documents examined)")
    print("-" * 78)
    for b, a in zip(before, after):
        drop = (1 - a["totalDocsExamined"] / max(b["totalDocsExamined"], 1)) * 100
        print(f"  {b['query'][:3]}: {b['plan'].split(' > ')[-1]:<10} {b['totalDocsExamined']:>7,} docs  ->  "
              f"{a['plan'].split(' > ')[-1].split('(')[0]:<10} {a['totalDocsExamined']:>6,} docs "
              f"({drop:.1f}% fewer)")

    with open(os.path.join(HERE, "index_design.json"), "w") as f:
        f.write(json_util.dumps({
            "documents": N, "before": before, "after": after,
            "indexes": list(col.list_indexes()),
            "sample_documents_first_100": list(col.find().limit(100)),
        }, indent=2))
    print("\n[OK] Exported explain results + indexes + sample -> index_design.json")
    client.close()


if __name__ == "__main__":
    main()

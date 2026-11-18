"""
23CSE528: NoSQL Databases using MongoDB
Exercise 6 - Activity for 6.2 (Try it yourself)
Student : M Nagasai Girisharan (24BTRCL114)

Covered query = every field used in the filter AND every field returned
is inside one index, and _id is excluded. MongoDB then answers the query
from the index alone: no FETCH stage, totalDocsExamined = 0.

Index : { category: 1, price: 1 }
Query : find({category: "Electronics", price: {$lt: 50}},
             {_id: 0, category: 1, price: 1})
explain() is compared for a NON-covered version (also returns name) and
the covered version. Results exported to covered_query.json.
"""

import os
import random

from bson import json_util
from pymongo import MongoClient, ASCENDING

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab6"
HERE = os.path.dirname(os.path.abspath(__file__))
random.seed(3)
CATEGORIES = ["Electronics", "Books", "Clothing", "Home", "Sports", "Toys", "Beauty", "Grocery"]


def stages(plan):
    if "queryPlan" in plan:
        plan = plan["queryPlan"]
    out = []
    while plan:
        out.append(plan["stage"])
        plan = plan.get("inputStage")
    return out


def summarize(col, label, flt, proj):
    ex = col.find(flt, proj).explain()
    st = ex["executionStats"]
    s = stages(ex["queryPlanner"]["winningPlan"])
    r = {"query": label, "projection": proj, "stages": s,
         "nReturned": st["nReturned"], "totalKeysExamined": st["totalKeysExamined"],
         "totalDocsExamined": st["totalDocsExamined"], "ms": st["executionTimeMillis"],
         "has_FETCH": "FETCH" in s}
    print(f"\n{label}")
    print(f"  projection        : {proj}")
    print(f"  plan stages       : {' > '.join(s)}")
    print(f"  nReturned         : {r['nReturned']}")
    print(f"  totalKeysExamined : {r['totalKeysExamined']}")
    print(f"  totalDocsExamined : {r['totalDocsExamined']}")
    print(f"  FETCH stage       : {'present' if r['has_FETCH'] else 'ELIMINATED'}")
    return r


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["covered_demo"]
    col.drop()
    col.insert_many([{"name": f"Item {i}", "category": random.choice(CATEGORIES),
                      "price": round(random.uniform(1, 500), 2),
                      "stock": random.randint(0, 300)} for i in range(20_000)])
    col.create_index([("category", ASCENDING), ("price", ASCENDING)])

    print("=" * 76)
    print("LAB 6.2 TRY IT YOURSELF : COVERED QUERY")
    print("=" * 76)
    print(f"Collection covered_demo: {col.count_documents({}):,} docs, "
          f"index: category_1_price_1")

    flt = {"category": "Electronics", "price": {"$lt": 50}}
    a = summarize(col, "[A] NOT covered - returns 'name' (not in the index)", flt,
                  {"_id": 0, "category": 1, "price": 1, "name": 1})
    b = summarize(col, "[B] COVERED - returns only indexed fields, _id excluded", flt,
                  {"_id": 0, "category": 1, "price": 1})
    c = summarize(col, "[C] NOT covered - _id left in the projection", flt,
                  {"category": 1, "price": 1})

    print("\n" + "-" * 76)
    ok = not b["has_FETCH"] and b["totalDocsExamined"] == 0
    print(f"Verification: covered query has no FETCH and examined 0 documents -> {ok}")
    print(f"Documents read from disk/cache: A={a['totalDocsExamined']}, "
          f"B={b['totalDocsExamined']}, C={c['totalDocsExamined']}")
    print("Note: _id must be excluded because it is not part of the index.")

    with open(os.path.join(HERE, "covered_query.json"), "w") as f:
        f.write(json_util.dumps({"index": {"category": 1, "price": 1},
                                 "filter": flt, "results": [a, b, c],
                                 "sample_covered_output": list(col.find(flt, {"_id": 0, "category": 1, "price": 1}).limit(10))},
                                indent=2))
    print("[OK] Exported results -> covered_query.json")
    client.close()


if __name__ == "__main__":
    main()

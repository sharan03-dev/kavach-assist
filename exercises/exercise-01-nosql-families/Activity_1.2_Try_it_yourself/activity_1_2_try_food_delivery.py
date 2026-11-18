"""
23CSE528: NoSQL Databases using MongoDB
Exercise 1 - Lab 1.2 "Try it yourself"
Student : M Nagasai Girisharan (24BTRCL114)

Apply the same method to a NEW application of my own choice: a food-delivery
app (like Swiggy/Zomato). Break it into data domains, decide the family for
each from its read/write pattern, and check the choice with a simple scoring
rule. Results are exported to food_delivery_mapping.json.
"""

import os

from bson import json_util
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab1"
HERE = os.path.dirname(os.path.abspath(__file__))

# Each domain is described by its access-pattern traits
DOMAINS = [
    {"domain": "Restaurant menus", "traits": ["flexible_schema", "read_heavy", "query_fields"]},
    {"domain": "Customer sessions & carts", "traits": ["point_lookup", "low_latency", "temporary"]},
    {"domain": "Delivery partner live location", "traits": ["very_high_writes", "low_latency", "temporary"]},
    {"domain": "Order history", "traits": ["append_only", "time_range", "huge_volume"]},
    {"domain": "Dish recommendations", "traits": ["relationships", "multi_hop"]},
    {"domain": "Restaurant photos", "traits": ["large_blobs", "write_once_read_many"]},
]

# Which traits point to which family
FAMILY_TRAITS = {
    "Document (MongoDB)": {"flexible_schema", "query_fields", "read_heavy"},
    "Key-Value (Redis)": {"point_lookup", "low_latency", "temporary", "very_high_writes"},
    "Wide-Column (Cassandra)": {"append_only", "time_range", "huge_volume", "very_high_writes"},
    "Graph (Neo4j)": {"relationships", "multi_hop"},
    "Object/Key-Value (Amazon S3)": {"large_blobs", "write_once_read_many"},
}


def choose_family(traits):
    scores = {f: len(set(traits) & t) for f, t in FAMILY_TRAITS.items()}
    best = max(scores, key=scores.get)
    return best, scores[best]


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["food_delivery_mapping"]
    col.drop()

    docs = []
    for d in DOMAINS:
        family, score = choose_family(d["traits"])
        docs.append({**d, "chosen_family": family, "matched_traits": score})
    col.insert_many(docs)

    print("=" * 80)
    print("LAB 1.2 TRY IT YOURSELF : FOOD-DELIVERY APP -> NoSQL FAMILIES")
    print("=" * 80)
    print(f"{'Data domain':<33}{'Chosen family':<30}{'Score':>6}")
    print("-" * 80)
    for d in col.find():
        print(f"{d['domain']:<33}{d['chosen_family']:<30}{d['matched_traits']:>4}/{len(d['traits'])}")
        print(f"   traits: {', '.join(d['traits'])}")

    print("\nDistinct families needed:", len(col.distinct("chosen_family")))
    print("=> One database is not enough; the app uses polyglot persistence,")
    print("   with MongoDB as the main store for menus and restaurant data.")

    with open(os.path.join(HERE, "food_delivery_mapping.json"), "w") as f:
        f.write(json_util.dumps(list(col.find()), indent=2))
    print("[OK] Exported collection -> food_delivery_mapping.json")
    client.close()


if __name__ == "__main__":
    main()

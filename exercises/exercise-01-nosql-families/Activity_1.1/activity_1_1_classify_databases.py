"""
23CSE528: NoSQL Databases using MongoDB
Exercise 1 - Lab 1.1: Classify Database Products
Student : M Nagasai Girisharan (24BTRCL114)

1. Identify the NoSQL family of Amazon S3, Couchbase, Apache HBase,
   Amazon Neptune and Memcached, with a one-sentence reason.
2. One real-world use case where each is the best choice, and why.
3. Comparison table: Product | Family | Best For | Limitation.
Results are stored in MongoDB and exported to classification.json.
"""

import os

from bson import json_util
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab1"
HERE = os.path.dirname(os.path.abspath(__file__))

PRODUCTS = [
    {
        "product": "Amazon S3",
        "family": "Key-Value (Object store)",
        "reasoning": "Every object is stored and fetched by a unique object key "
                     "and the store cannot query inside the object's contents.",
        "use_case": "Storing user-uploaded photos/videos and data-lake files for an app",
        "why": "Cheap, virtually unlimited, highly durable storage where files are "
               "always fetched whole by key.",
        "best_for": "Large files, backups, static assets",
        "limitation": "No querying inside objects; high latency vs. a database",
    },
    {
        "product": "Couchbase",
        "family": "Document",
        "reasoning": "It stores JSON documents and can build secondary indexes and "
                     "run SQL-like (N1QL/SQL++) queries on fields inside them.",
        "use_case": "User profiles and shopping carts for an e-commerce site",
        "why": "Each profile/cart is one flexible JSON document that is queried by "
               "fields and served fast from its built-in memory cache.",
        "best_for": "Flexible JSON data with fast reads",
        "limitation": "Weak at multi-document joins and complex relationships",
    },
    {
        "product": "Apache HBase",
        "family": "Column-Family (Wide-Column)",
        "reasoning": "Data is organised by row key into column families and is built "
                     "for very wide, sparse rows with millions of columns on HDFS.",
        "use_case": "Storing billions of IoT sensor readings / message history",
        "why": "Handles huge write volumes and fast range scans over a row key "
               "across a Hadoop cluster.",
        "best_for": "Huge, sparse, write-heavy datasets",
        "limitation": "No secondary indexes or joins; needs Hadoop/ZooKeeper to run",
    },
    {
        "product": "Amazon Neptune",
        "family": "Graph",
        "reasoning": "It is purpose-built to store nodes and edges and query them "
                     "with graph languages like Gremlin, openCypher and SPARQL.",
        "use_case": "Fraud detection or friend-of-a-friend recommendations",
        "why": "Multi-hop relationship traversals are fast, which would need many "
               "costly joins in other families.",
        "best_for": "Highly connected data, relationship queries",
        "limitation": "Poor for bulk aggregation/analytics; AWS-only managed service",
    },
    {
        "product": "Memcached",
        "family": "Key-Value (in-memory cache)",
        "reasoning": "It keeps arbitrary byte values in RAM indexed only by string "
                     "keys, with no query language at all.",
        "use_case": "Caching database query results and web sessions",
        "why": "Sub-millisecond get/set from memory reduces load on the main database.",
        "best_for": "Fast temporary caching",
        "limitation": "No persistence or replication; data lost on restart",
    },
]


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["classification"]
    col.drop()
    col.insert_many(PRODUCTS)

    print("=" * 78)
    print("LAB 1.1 : CLASSIFY DATABASE PRODUCTS")
    print("=" * 78)
    print(f"[OK] Stored {col.count_documents({})} products in {DB_NAME}.classification\n")

    print("Q1) NoSQL family + reasoning")
    print("-" * 78)
    for d in col.find({}, {"_id": 0}):
        print(f"* {d['product']}  ->  {d['family']}")
        print(f"    {d['reasoning']}")

    print("\nQ2) Best real-world use case")
    print("-" * 78)
    for d in col.find({}, {"_id": 0, "product": 1, "use_case": 1, "why": 1}):
        print(f"* {d['product']}: {d['use_case']}")
        print(f"    Why: {d['why']}")

    print("\nQ3) Comparison table")
    print("-" * 78)
    print(f"{'Product':<16}{'Family':<30}{'Best For':<34}")
    for d in col.find():
        print(f"{d['product']:<16}{d['family']:<30}{d['best_for']:<34}")
    print("\nLimitations:")
    for d in col.find():
        print(f"  {d['product']:<16}{d['limitation']}")

    print("\nProducts per family (aggregation):")
    for r in col.aggregate([
        {"$group": {"_id": {"$arrayElemAt": [{"$split": ["$family", " ("]}, 0]},
                    "products": {"$push": "$product"}}},
        {"$sort": {"_id": 1}}]):
        print(f"  {r['_id']:<14}: {', '.join(r['products'])}")

    with open(os.path.join(HERE, "classification.json"), "w") as f:
        f.write(json_util.dumps(list(col.find()), indent=2))
    print("\n[OK] Exported collection -> classification.json")
    client.close()


if __name__ == "__main__":
    main()

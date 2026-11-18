"""
23CSE528: NoSQL Databases using MongoDB
Exercise 1 - Lab 1.2: Map a Real-World Application to NoSQL Families
Student : M Nagasai Girisharan (24BTRCL114)

Two applications (Ride-Sharing and Social Media Analytics) are split into
four data domains each. For every domain we record the read/write pattern
and choose the NoSQL family + database that fits it (polyglot persistence).
Results are exported to application_mapping.json.
"""

import os

from bson import json_util
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab1"
HERE = os.path.dirname(os.path.abspath(__file__))

MAPPING = [
    # ---------------- Ride-Sharing ----------------
    {"app": "Ride-Sharing", "domain": "User profiles",
     "pattern": "Read-heavy, point lookups by user id, flexible fields",
     "family": "Document", "database": "MongoDB",
     "rationale": "Riders and drivers have different, changing attributes; one "
                  "JSON document per user is read in a single lookup."},
    {"app": "Ride-Sharing", "domain": "Ride history",
     "pattern": "Write-heavy, append-only, range scans by user + time",
     "family": "Wide-Column", "database": "Apache Cassandra",
     "rationale": "Partition by user, cluster by ride time: fast appends and "
                  "'last N rides' range scans at huge scale."},
    {"app": "Ride-Sharing", "domain": "Real-time GPS tracking",
     "pattern": "Very high write rate, latest value only, short-lived",
     "family": "Key-Value", "database": "Redis",
     "rationale": "driver_id -> current location in memory, with geo commands "
                  "and expiry; sub-millisecond reads for matching."},
    {"app": "Ride-Sharing", "domain": "Payment transactions",
     "pattern": "Must be consistent, multi-step updates",
     "family": "Document", "database": "MongoDB (ACID transactions)",
     "rationale": "Multi-document transactions keep fare, wallet and ride status "
                  "correct together."},
    # ---------------- Social Media Analytics ----------------
    {"app": "Social Media Analytics", "domain": "Posts and content",
     "pattern": "Mixed read/write, nested data (media, hashtags)",
     "family": "Document", "database": "MongoDB",
     "rationale": "A post with its media and tags is naturally one document and "
                  "can be indexed by hashtag or author."},
    {"app": "Social Media Analytics", "domain": "Follower network",
     "pattern": "Multi-hop relationship traversal",
     "family": "Graph", "database": "Neo4j",
     "rationale": "'Friends of friends' and influencer paths are cheap edge "
                  "traversals instead of repeated joins."},
    {"app": "Social Media Analytics", "domain": "Engagement events",
     "pattern": "Massive write-heavy time-series of likes/views",
     "family": "Wide-Column", "database": "Apache Cassandra",
     "rationale": "Billions of events per day, partitioned by post and time for "
                  "fast writes and time-range reads."},
    {"app": "Social Media Analytics", "domain": "Media files",
     "pattern": "Large binary blobs, write once read many",
     "family": "Key-Value (Object)", "database": "Amazon S3",
     "rationale": "Images and videos fetched whole by key; cheap and durable."},
]


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["app_mapping"]
    col.drop()
    col.insert_many(MAPPING)

    print("=" * 80)
    print("LAB 1.2 : MAP A REAL-WORLD APPLICATION TO NoSQL FAMILIES")
    print("=" * 80)
    for app in col.distinct("app"):
        print(f"\n### {app}")
        print("-" * 80)
        for d in col.find({"app": app}):
            print(f"[{d['domain']}]  -> {d['family']} / {d['database']}")
            print(f"    pattern  : {d['pattern']}")
            print(f"    rationale: {d['rationale']}")

    print("\nFamily distribution across both applications (aggregation):")
    print("-" * 80)
    for r in col.aggregate([{"$group": {"_id": "$family", "count": {"$sum": 1}}},
                            {"$sort": {"count": -1, "_id": 1}}]):
        print(f"  {r['_id']:<22} {'#' * r['count']} ({r['count']})")
    print("\nConclusion: each app needs more than one family -> polyglot persistence.")

    with open(os.path.join(HERE, "application_mapping.json"), "w") as f:
        f.write(json_util.dumps(list(col.find()), indent=2))
    print("[OK] Exported collection -> application_mapping.json")
    client.close()


if __name__ == "__main__":
    main()

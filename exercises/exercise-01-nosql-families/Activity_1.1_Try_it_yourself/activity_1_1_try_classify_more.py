"""
23CSE528: NoSQL Databases using MongoDB
Exercise 1 - Lab 1.1 "Try it yourself"
Student : M Nagasai Girisharan (24BTRCL114)

Research ScyllaDB, InfluxDB and ArangoDB and classify them.
Does each fit neatly into one family, or does it span multiple families?
Results are exported to research_classification.json.
"""

import os

from bson import json_util
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab1"
HERE = os.path.dirname(os.path.abspath(__file__))

RESEARCH = [
    {
        "product": "ScyllaDB",
        "families": ["Column-Family (Wide-Column)"],
        "fits_one_family": True,
        "evidence": "A drop-in, C++ rewrite of Apache Cassandra: same CQL language, "
                    "partition key + clustering columns, tables of wide rows.",
        "best_for": "Very high-throughput, low-latency writes (chat, IoT, ad-tech)",
        "verdict": "Fits neatly into ONE family - it is a wide-column store.",
    },
    {
        "product": "InfluxDB",
        "families": ["Time-Series", "Column-Family (columnar storage)"],
        "fits_one_family": False,
        "evidence": "Data is organised as measurement + tags + fields + timestamp; "
                    "v3 stores it in columnar Apache Parquet files. Not one of the "
                    "four classic families - it is a specialised time-series DB.",
        "best_for": "Metrics, monitoring and sensor readings over time",
        "verdict": "Does NOT fit the four classic families neatly - it is a "
                   "time-series database built on columnar storage.",
    },
    {
        "product": "ArangoDB",
        "families": ["Document", "Graph", "Key-Value"],
        "fits_one_family": False,
        "evidence": "A multi-model database: JSON document collections, edge "
                    "collections for graph traversals, and key lookups, all queried "
                    "with one language (AQL).",
        "best_for": "Apps that need documents AND relationships in one engine",
        "verdict": "Spans MULTIPLE families (document + graph + key-value).",
    },
]


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["research_classification"]
    col.drop()
    col.insert_many(RESEARCH)

    print("=" * 78)
    print("LAB 1.1 TRY IT YOURSELF : CLASSIFY SCYLLADB, INFLUXDB, ARANGODB")
    print("=" * 78)
    for d in col.find({}, {"_id": 0}):
        print(f"\n* {d['product']}")
        print(f"    Family/families : {', '.join(d['families'])}")
        print(f"    Evidence        : {d['evidence']}")
        print(f"    Best for        : {d['best_for']}")
        print(f"    Verdict         : {d['verdict']}")

    print("\n" + "-" * 78)
    single = [d["product"] for d in col.find({"fits_one_family": True})]
    multi = [d["product"] for d in col.find({"fits_one_family": False})]
    print(f"Fits ONE family     : {', '.join(single)}")
    print(f"Spans / outside one : {', '.join(multi)}")
    print("Multi-family products found with $expr + $size (families array > 1):")
    for d in col.find({"$expr": {"$gt": [{"$size": "$families"}, 1]}}, {"product": 1}):
        print(f"   -> {d['product']}")

    with open(os.path.join(HERE, "research_classification.json"), "w") as f:
        f.write(json_util.dumps(list(col.find()), indent=2))
    print("\n[OK] Exported collection -> research_classification.json")
    client.close()


if __name__ == "__main__":
    main()

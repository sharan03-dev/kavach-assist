"""
23CSE528: NoSQL Databases using MongoDB
Exercise 4 - Activity for 4.1 (Try it yourself)
Student : M Nagasai Girisharan (24BTRCL114)

Add a text search capability using the $regex operator to find products
whose name contains a search string (case-insensitive).
User input is escaped with re.escape() so characters like '.' or '+' are
matched literally. Search results are exported to regex_search_results.json.
"""

import os
import re

from bson import json_util
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab4"
HERE = os.path.dirname(os.path.abspath(__file__))

PRODUCTS = [
    {"name": "Wireless Mouse", "price": 24.99, "stock": 120, "tags": ["electronics", "wireless"]},
    {"name": "Bluetooth Headphones", "price": 149.0, "stock": 35, "tags": ["electronics", "wireless", "premium"]},
    {"name": "USB-C Cable", "price": 9.99, "stock": 0, "tags": ["electronics", "accessories"]},
    {"name": "Mechanical Keyboard", "price": 89.5, "stock": 18, "tags": ["electronics", "premium"]},
    {"name": "Laptop Stand", "price": 35.0, "stock": 0, "tags": ["office"]},
    {"name": "Noise-Cancel Earbuds", "price": 199.0, "stock": 0, "tags": ["electronics", "wireless", "premium"]},
    {"name": "Desk Lamp", "price": 42.0, "stock": 60, "tags": ["office", "lighting"]},
    {"name": "Leather Notebook", "price": 22.0, "stock": 200, "tags": ["stationery", "premium"]},
    {"name": "Smart Watch", "price": 249.0, "stock": 12, "tags": ["electronics", "wireless", "premium", "wearable"]},
    {"name": "Webcam HD", "price": 49.99, "stock": 44, "tags": ["electronics", "video"]},
    {"name": "Ergonomic Chair", "price": 320.0, "stock": 7, "tags": ["office", "furniture"]},
    {"name": "Wireless Charger", "price": 29.0, "stock": 0, "tags": ["electronics", "wireless"]},
]


def search_products(col, text):
    """Case-insensitive 'contains' search on name using $regex."""
    query = {"name": {"$regex": re.escape(text), "$options": "i"}}
    return query, list(col.find(query, {"_id": 0, "name": 1, "price": 1}).sort("name", 1))


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["products"]
    if col.count_documents({}) == 0:            # seed if Lab 4.1 not run
        col.insert_many(PRODUCTS)

    print("=" * 74)
    print("LAB 4.1 TRY IT YOURSELF : $regex CASE-INSENSITIVE NAME SEARCH")
    print("=" * 74)
    results = {}
    for term in ["wireless", "WIRELESS", "lamp", "o", "usb-c", "phone", "xyz"]:
        query, hits = search_products(col, term)
        results[term] = hits
        print(f"\nsearch('{term}')   query={query}")
        if hits:
            for h in hits:
                print(f"   - {h['name']:<24} ${h['price']:.2f}")
        else:
            print("   (no products found)")

    same = [h["name"] for h in results["wireless"]] == [h["name"] for h in results["WIRELESS"]]
    print("\n" + "-" * 74)
    print(f"Case-insensitive check: 'wireless' and 'WIRELESS' return same results -> {same}")

    with open(os.path.join(HERE, "regex_search_results.json"), "w") as f:
        f.write(json_util.dumps(results, indent=2))
    print("[OK] Exported search results -> regex_search_results.json")
    client.close()


if __name__ == "__main__":
    main()

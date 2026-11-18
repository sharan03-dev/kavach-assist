"""
23CSE528: NoSQL Databases using MongoDB
Exercise 2 - Activity for 2.2 (Try it yourself)
Student : M Nagasai Girisharan (24BTRCL114)

Add an update operation that increases the price of all books published
before 2010 by 10 percent, then verify the update.

Verify in MongoDB Compass:
  connect to mongodb://localhost:27017 -> database nosql_lab2 -> collection
  books_2010 -> Filter: { year: { $lt: 2010 } }
The collection after the update is exported to books_2010.json.
"""

import os

from bson import json_util
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab2"
HERE = os.path.dirname(os.path.abspath(__file__))

BOOKS = [
    {"title": "The C Programming Language", "author": "Kernighan & Ritchie", "year": 1988, "price": 45.00},
    {"title": "Design Patterns", "author": "Gamma, Helm, Johnson, Vlissides", "year": 1994, "price": 55.00},
    {"title": "Code Complete", "author": "Steve McConnell", "year": 2004, "price": 42.50},
    {"title": "Clean Code", "author": "Robert C. Martin", "year": 2008, "price": 39.99},
    {"title": "Introduction to Algorithms", "author": "Cormen et al.", "year": 2009, "price": 89.00},
    {"title": "MongoDB: The Definitive Guide", "author": "Bradshaw et al.", "year": 2010, "price": 44.50},
    {"title": "Deep Learning", "author": "Goodfellow et al.", "year": 2016, "price": 72.00},
    {"title": "Designing Data-Intensive Applications", "author": "Martin Kleppmann", "year": 2017, "price": 54.99},
    {"title": "Hands-On Machine Learning", "author": "Aurelien Geron", "year": 2022, "price": 59.99},
]


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["books_2010"]
    col.drop()
    col.insert_many(BOOKS)

    print("=" * 76)
    print("LAB 2.2 TRY IT YOURSELF : +10% PRICE FOR BOOKS PUBLISHED BEFORE 2010")
    print("=" * 76)
    print(f"[OK] Inserted {col.count_documents({})} books\n")

    before = {b["title"]: b["price"] for b in col.find()}

    # ---- the update ----
    query = {"year": {"$lt": 2010}}
    result = col.update_many(query, {"$mul": {"price": 1.10}})
    print(f"update_many({query}, {{'$mul': {{'price': 1.10}}}})")
    print(f"  matched = {result.matched_count}, modified = {result.modified_count}\n")

    # ---- verification ----
    print(f"{'Title':<40}{'Year':>5}{'Before':>10}{'After':>10}  Check")
    print("-" * 76)
    all_ok = True
    for b in col.find().sort("year", 1):
        old = before[b["title"]]
        expected = round(old * 1.10, 2) if b["year"] < 2010 else old
        ok = abs(b["price"] - expected) < 0.01
        all_ok &= ok
        flag = ("+10% OK" if b["year"] < 2010 else "unchanged OK") if ok else "WRONG"
        print(f"{b['title'][:39]:<40}{b['year']:>5}{old:>10.2f}{b['price']:>10.2f}  {flag}")

    print("-" * 76)
    print("Verification:", "PASSED - only pre-2010 books changed" if all_ok else "FAILED")
    print("Compass filter to view the same result: { year: { $lt: 2010 } }")

    with open(os.path.join(HERE, "books_2010.json"), "w") as f:
        f.write(json_util.dumps(list(col.find()), indent=2))
    print("[OK] Exported collection -> books_2010.json")
    client.close()


if __name__ == "__main__":
    main()

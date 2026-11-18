"""
23CSE528: NoSQL Databases using MongoDB
Exercise 2 - Lab 2.2: Bookstore CRUD Operations
Student : M Nagasai Girisharan (24BTRCL114)

Create : insert books with a nested publisher object and a tags array.
Read   : by year, by genre, by price range, by array element (tags).
Update : increase every price by 10% with $mul.
Delete : remove one book by title.
The final collection is exported to books.json.
"""

import os

from bson import json_util
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab2"
HERE = os.path.dirname(os.path.abspath(__file__))

BOOKS = [
    {"title": "The Pragmatic Programmer", "author": "David Thomas & Andrew Hunt",
     "year": 2019, "genre": "Software Engineering", "price": 49.99,
     "publisher": {"name": "Addison-Wesley", "location": "Boston"},
     "tags": ["programming", "career", "best-practices"]},
    {"title": "Designing Data-Intensive Applications", "author": "Martin Kleppmann",
     "year": 2017, "genre": "Databases", "price": 54.99,
     "publisher": {"name": "O'Reilly", "location": "Sebastopol"},
     "tags": ["databases", "distributed-systems", "NoSQL"]},
    {"title": "Deep Learning", "author": "Goodfellow, Bengio, Courville",
     "year": 2016, "genre": "Machine Learning", "price": 72.00,
     "publisher": {"name": "MIT Press", "location": "Cambridge"},
     "tags": ["AI", "neural-networks", "math"]},
    {"title": "Clean Code", "author": "Robert C. Martin",
     "year": 2008, "genre": "Software Engineering", "price": 39.99,
     "publisher": {"name": "Prentice Hall", "location": "New Jersey"},
     "tags": ["programming", "best-practices"]},
    {"title": "Hands-On Machine Learning", "author": "Aurelien Geron",
     "year": 2022, "genre": "Machine Learning", "price": 59.99,
     "publisher": {"name": "O'Reilly", "location": "Sebastopol"},
     "tags": ["AI", "python", "scikit-learn"]},
    {"title": "MongoDB: The Definitive Guide", "author": "Bradshaw, Brazil, Chodorow",
     "year": 2021, "genre": "Databases", "price": 44.50,
     "publisher": {"name": "O'Reilly", "location": "Sebastopol"},
     "tags": ["databases", "NoSQL", "MongoDB"]},
]


def show(cursor, fmt):
    for b in cursor:
        print("  " + fmt(b))


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["books"]
    col.drop()

    print("=" * 74)
    print("LAB 2.2 : BOOKSTORE CRUD OPERATIONS")
    print("=" * 74)

    # CREATE
    res = col.insert_many(BOOKS)
    print(f"[CREATE] Inserted {len(res.inserted_ids)} books\n")

    # READ
    print("[READ] Books published after 2020   filter {'year': {'$gt': 2020}}")
    show(col.find({"year": {"$gt": 2020}}, {"_id": 0}),
         lambda b: f"{b['title']:<40} ({b['year']})  ${b['price']:.2f}")

    print("\n[READ] Machine Learning books        filter {'genre': 'Machine Learning'}")
    show(col.find({"genre": "Machine Learning"}, {"_id": 0}),
         lambda b: f"{b['title']:<40} by {b['author']}")

    print("\n[READ] Price between $40 and $60     filter {'price': {'$gte': 40, '$lte': 60}}")
    show(col.find({"price": {"$gte": 40, "$lte": 60}}, {"_id": 0}).sort("price", 1),
         lambda b: f"{b['title']:<40} ${b['price']:.2f}")

    print("\n[READ] Books tagged 'NoSQL'          filter {'tags': 'NoSQL'}")
    show(col.find({"tags": "NoSQL"}, {"_id": 0}),
         lambda b: f"{b['title']:<40} tags={b['tags']}")

    print("\n[READ] Nested field: O'Reilly books  filter {'publisher.name': \"O'Reilly\"}")
    print(f"  count = {col.count_documents({'publisher.name': "O'Reilly"})}")

    # UPDATE
    before = {b["title"]: b["price"] for b in col.find()}
    upd = col.update_many({}, {"$mul": {"price": 1.10}})
    print(f"\n[UPDATE] $mul price x 1.10 -> modified {upd.modified_count} books")
    for b in col.find():
        print(f"  {b['title']:<40} ${before[b['title']]:.2f} -> ${b['price']:.2f}")

    # DELETE
    d = col.delete_one({"title": "Clean Code"})
    print(f"\n[DELETE] delete_one(title='Clean Code') -> deleted {d.deleted_count}")
    print(f"Remaining books ({col.count_documents({})}):")
    show(col.find({}, {"_id": 0, "title": 1}), lambda b: b["title"])

    with open(os.path.join(HERE, "books.json"), "w") as f:
        f.write(json_util.dumps(list(col.find()), indent=2))
    print("\n[OK] Exported collection -> books.json")
    client.close()


if __name__ == "__main__":
    main()

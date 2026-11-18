"""
23CSE528: NoSQL Databases using MongoDB
Exercise 4 - Lab 4.1: Build a Product Query API
Student : M Nagasai Girisharan (24BTRCL114)

1. products collection with schema validation: name (string), price (positive
   number), stock (non-negative integer), tags (array of strings); 10+ products.
2. Comparison operators : price between 20 and 50; stock equal to 0.
3. Logical operators    : out of stock OR price > 100; in stock AND tagged 'premium'.
4. Array operators      : tags contain BOTH 'electronics' AND 'wireless'; exactly 2 tags.
5. Pagination           : get_page(page, per_page) sorted by name.
Collection exported to products.json.
"""

import os

from bson import json_util
from pymongo import MongoClient, ASCENDING
from pymongo.errors import WriteError

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab4"
HERE = os.path.dirname(os.path.abspath(__file__))

VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["name", "price", "stock", "tags"],
    "properties": {
        "name": {"bsonType": "string", "description": "must be a string"},
        "price": {"bsonType": ["double", "int", "long", "decimal"],
                  "minimum": 0, "exclusiveMinimum": True, "description": "positive number"},
        "stock": {"bsonType": ["int", "long"], "minimum": 0,
                  "description": "non-negative integer"},
        "tags": {"bsonType": "array", "items": {"bsonType": "string"}},
    },
}}

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


def show(title, flt, col):
    print(f"\n{title}")
    print(f"  filter: {flt}")
    for p in col.find(flt).sort("name", ASCENDING):
        print(f"   - {p['name']:<22} ${p['price']:>7.2f}  stock={p['stock']:<4} tags={p['tags']}")


def get_page(col, page, per_page):
    """Return the products for a 1-based page number, sorted by name."""
    skip = (page - 1) * per_page
    return list(col.find({}, {"_id": 0, "name": 1, "price": 1})
                .sort("name", ASCENDING).skip(skip).limit(per_page))


def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    db.drop_collection("products")
    db.create_collection("products", validator=VALIDATOR)
    col = db["products"]
    col.insert_many(PRODUCTS)

    print("=" * 78)
    print("LAB 4.1 : PRODUCT QUERY API")
    print("=" * 78)
    print(f"[1] Created 'products' with $jsonSchema validator, inserted {col.count_documents({})} products")
    for bad in ({"name": "Bad price", "price": -5, "stock": 1, "tags": []},
                {"name": "Bad stock", "price": 5.0, "stock": 2.5, "tags": []},
                {"name": "Bad tags", "price": 5.0, "stock": 1, "tags": [1, 2]}):
        try:
            col.insert_one(bad)
            print(f"    [ACCEPTED?] {bad['name']}")
        except WriteError:
            print(f"    [REJECTED] '{bad['name']}' violates the schema")

    print("\n[2] COMPARISON OPERATORS" + "-" * 54)
    show("Price between 20 and 50", {"price": {"$gte": 20, "$lte": 50}}, col)
    show("Out of stock (stock == 0)", {"stock": {"$eq": 0}}, col)

    print("\n[3] LOGICAL OPERATORS" + "-" * 57)
    show("Out of stock OR price above 100",
         {"$or": [{"stock": 0}, {"price": {"$gt": 100}}]}, col)
    show("In stock AND tagged 'premium'",
         {"$and": [{"stock": {"$gt": 0}}, {"tags": "premium"}]}, col)

    print("\n[4] ARRAY OPERATORS" + "-" * 59)
    show("Tags contain BOTH 'electronics' AND 'wireless'",
         {"tags": {"$all": ["electronics", "wireless"]}}, col)
    show("Exactly 2 tags", {"tags": {"$size": 2}}, col)

    print("\n[5] PAGINATION  get_page(page, per_page=5) sorted by name" + "-" * 20)
    total = col.count_documents({})
    for page in range(1, -(-total // 5) + 1):
        names = ", ".join(p["name"] for p in get_page(col, page, 5))
        print(f"  page {page}: {names}")

    with open(os.path.join(HERE, "products.json"), "w") as f:
        f.write(json_util.dumps(list(col.find()), indent=2))
    print("\n[OK] Exported collection -> products.json")
    client.close()


if __name__ == "__main__":
    main()

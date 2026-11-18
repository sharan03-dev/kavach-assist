"""
23CSE528: NoSQL Databases using MongoDB
Exercise 5 - Lab 5.2 "Try it yourself": $lookup with a pipeline sub-expression
Student : M Nagasai Girisharan (24BTRCL114)
 
$lookup with `let` + `pipeline` (MongoDB 3.6+) lets us FILTER the joined
(foreign) documents before they are attached.
 
Demo 1: for every product, join only its orders with quantity >= 2.
Demo 2: for every order, join the product only if it is in the
        'Accessories' category (non-matching orders get an empty array).
Results are exported to lookup_pipeline_results.json.
"""
 
import os
from datetime import datetime
 
from bson import json_util
from pymongo import MongoClient
 
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab5"
HERE = os.path.dirname(os.path.abspath(__file__))
 
PRODUCTS = [
    {"_id": "P101", "name": "Wireless Mouse",       "category": "Accessories", "base_price": 25.0},
    {"_id": "P102", "name": "Mechanical Keyboard",  "category": "Accessories", "base_price": 120.0},
    {"_id": "P103", "name": "27-inch Monitor",      "category": "Displays",    "base_price": 280.0},
    {"_id": "P104", "name": "USB-C Hub",            "category": "Accessories", "base_price": 45.0},
    {"_id": "P105", "name": "Webcam HD",            "category": "Video",       "base_price": 60.0},
    {"_id": "P106", "name": "Noise-Cancel Headset", "category": "Audio",       "base_price": 199.0},
    {"_id": "P107", "name": "Graphics Tablet",      "category": "Creative",    "base_price": 150.0},
    {"_id": "P108", "name": "Portable SSD 1TB",     "category": "Storage",     "base_price": 110.0},
]
ORDERS = [
    ("O201", "Arjun", "P101", 2, "2026-06-02"),
    ("O202", "Sneha", "P103", 1, "2026-06-05"),
    ("O203", "Rahul", "P102", 1, "2026-06-09"),
    ("O204", "Kavya", "P104", 3, "2026-06-12"),
    ("O205", "Arjun", "P106", 1, "2026-06-15"),
    ("O206", "Divya", "P101", 4, "2026-06-18"),
    ("O207", "Kiran", "P105", 2, "2026-06-21"),
    ("O208", "Sneha", "P102", 1, "2026-06-24"),
    ("O209", "Rahul", "P103", 2, "2026-06-27"),
    ("O210", "Kavya", "P106", 1, "2026-06-30"),
]
MIN_QTY = 2
 
 
def seed(db):
    """Re-create Lab 5.2 data if it is not present (script runs standalone)."""
    if db["products"].count_documents({}) == 0:
        db["products"].insert_many(PRODUCTS)
    if db["orders_ref"].count_documents({}) == 0:
        db["orders_ref"].insert_many([
            {"_id": o, "customer": c, "product_id": p, "quantity": q,
             "order_date": datetime.strptime(d, "%Y-%m-%d")}
            for o, c, p, q, d in ORDERS])
 
 
def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    seed(db)
    products, orders = db["products"], db["orders_ref"]
 
    print("=" * 74)
    print("LAB 5.2 TRY IT YOURSELF : $lookup WITH PIPELINE SUB-EXPRESSION")
    print("=" * 74)
    print(f"Server version: {client.server_info()['version']}\n")
 
    # ---------- Demo 1: products <- orders filtered by quantity ----------
    print(f"Demo 1) Each product with only its orders where quantity >= {MIN_QTY}")
    print("-" * 74)
    demo1 = [
        {"$lookup": {
            "from": "orders_ref",
            "let": {"pid": "$_id"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$product_id", "$$pid"]},
                    {"$gte": ["$quantity", MIN_QTY]},
                ]}}},
                {"$project": {"_id": 1, "customer": 1, "quantity": 1}},
                {"$sort": {"quantity": -1}},
            ],
            "as": "bulk_orders",
        }},
        {"$project": {"name": 1, "bulk_orders": 1,
                      "bulk_count": {"$size": "$bulk_orders"}}},
        {"$sort": {"bulk_count": -1, "_id": 1}},
    ]
    demo1_res = list(products.aggregate(demo1))
    for r in demo1_res:
        detail = ", ".join(f"{o['_id']}:{o['customer']}x{o['quantity']}"
                           for o in r["bulk_orders"]) or "-- none --"
        print(f"{r['_id']} {r['name']:<21} [{r['bulk_count']}]  {detail}")
 
    # ---------- Demo 2: orders <- products filtered by category ----------
    print("\nDemo 2) Each order joined ONLY to products in category 'Accessories'")
    print("-" * 74)
    demo2 = [
        {"$lookup": {
            "from": "products",
            "let": {"pid": "$product_id"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$_id", "$$pid"]},
                            "category": "Accessories"}},
                {"$project": {"_id": 0, "name": 1, "base_price": 1}},
            ],
            "as": "accessory",
        }},
        {"$project": {"customer": 1, "quantity": 1, "accessory": 1}},
        {"$sort": {"_id": 1}},
    ]
    demo2_res = list(orders.aggregate(demo2))
    for r in demo2_res:
        if r["accessory"]:
            a = r["accessory"][0]
            print(f"{r['_id']} {r['customer']:<6} -> {a['name']:<20} "
                  f"total ${r['quantity'] * a['base_price']:.2f}")
        else:
            print(f"{r['_id']} {r['customer']:<6} -> []  (filtered out on foreign side)")
 
    matched = sum(1 for r in demo2_res if r["accessory"])
    print(f"\n{matched} of {len(demo2_res)} orders kept a joined product; "
          f"the rest were filtered inside the $lookup pipeline.")
 
    out = os.path.join(HERE, "lookup_pipeline_results.json")
    with open(out, "w") as f:
        f.write(json_util.dumps({"demo1_products_bulk_orders": demo1_res,
                                 "demo2_orders_accessories": demo2_res}, indent=2))
    print(f"[OK] Exported results -> {os.path.basename(out)}")
    client.close()
 
 
if __name__ == "__main__":
    main()

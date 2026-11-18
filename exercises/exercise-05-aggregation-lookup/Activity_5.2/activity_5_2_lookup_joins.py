"""
23CSE528: NoSQL Databases using MongoDB
Exercise 5 - Lab 5.2: Join Data with $lookup
Student : M Nagasai Girisharan (24BTRCL114)
 
Tasks:
 1. `products` (name, category, base_price) and `orders` that REFERENCE
    products by product_id (no embedding). Insert related data.
 2. $lookup orders -> products: order date, product name, quantity and
    computed total (quantity * base_price).
 3. Products that have never been ordered (left outer join + $match on an
    empty joined array).
Both collections are exported to lab5_2_data.json.
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
 
# orders reference products by product_id only
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
 
 
def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    products, orders = db["products"], db["orders_ref"]
 
    # ---------------- Step 1: create + insert ----------------
    products.drop()
    orders.drop()
    products.insert_many(PRODUCTS)
    orders.insert_many([
        {"_id": oid, "customer": c, "product_id": pid, "quantity": q,
         "order_date": datetime.strptime(d, "%Y-%m-%d")}
        for oid, c, pid, q, d in ORDERS
    ])
    orders.create_index("product_id")
 
    print("=" * 74)
    print("LAB 5.2 : JOIN DATA WITH $lookup")
    print("=" * 74)
    print(f"[OK] Inserted {products.count_documents({})} products and "
          f"{orders.count_documents({})} orders (orders reference product_id)\n")
 
    # ---------------- Step 2: orders JOIN products ----------------
    print("Q2) Orders joined with products  (total = quantity * base_price)")
    print("-" * 74)
    join = [
        {"$lookup": {"from": "products", "localField": "product_id",
                     "foreignField": "_id", "as": "product"}},
        {"$unwind": "$product"},
        {"$project": {
            "_id": 0,
            "order_id": "$_id",
            "order_date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$order_date"}},
            "product_name": "$product.name",
            "quantity": 1,
            "total": {"$multiply": ["$quantity", "$product.base_price"]},
        }},
        {"$sort": {"order_date": 1}},
    ]
    print(f"{'Order':<7}{'Date':<12}{'Product':<22}{'Qty':>4}{'Total ($)':>12}")
    grand = 0
    for r in orders.aggregate(join):
        grand += r["total"]
        print(f"{r['order_id']:<7}{r['order_date']:<12}{r['product_name']:<22}"
              f"{r['quantity']:>4}{r['total']:>12.2f}")
    print(f"{'':<45}{'Grand total':>12}: {grand:.2f}\n")
 
    # ---------------- Step 3: products never ordered ----------------
    print("Q3) Products that have NEVER been ordered (left outer join)")
    print("-" * 74)
    never = [
        {"$lookup": {"from": "orders_ref", "localField": "_id",
                     "foreignField": "product_id", "as": "orders"}},
        {"$match": {"orders": {"$size": 0}}},
        {"$project": {"_id": 1, "name": 1, "category": 1, "base_price": 1}},
    ]
    for r in products.aggregate(never):
        print(f"{r['_id']}  {r['name']:<20} {r['category']:<10} ${r['base_price']:.2f}")
 
    # ---------------- Export ----------------
    out = os.path.join(HERE, "lab5_2_data.json")
    with open(out, "w") as f:
        f.write(json_util.dumps({"products": list(products.find()),
                                 "orders": list(orders.find())}, indent=2))
    print(f"\n[OK] Exported products + orders collections -> {os.path.basename(out)}")
    client.close()
 
 
if __name__ == "__main__":
    main()

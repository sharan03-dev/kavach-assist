"""
23CSE528: NoSQL Databases using MongoDB
Exercise 5 - Lab 5.1 "Try it yourself": $bucket stage
Student : M Nagasai Girisharan (24BTRCL114)
 
Categorise every order by total order value (quantity * unit_price):
    small  : under $50
    medium : $50 - $200
    large  : over $200
Uses the `orders` collection from Lab 5.1 (re-seeded here so the script
runs on its own). Bucketed results are exported to orders_bucket.json.
"""
 
import os
from datetime import datetime
 
from bson import json_util
from pymongo import MongoClient
 
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab5"
HERE = os.path.dirname(os.path.abspath(__file__))
 
ORDERS = [
    ("Arjun",  "Wireless Mouse",      2,  25.0, "2026-01-05", "completed"),
    ("Sneha",  "Mechanical Keyboard", 1, 120.0, "2026-01-12", "completed"),
    ("Rahul",  "USB-C Hub",           3,  45.0, "2026-01-18", "pending"),
    ("Kavya",  "Laptop Stand",        1,  35.0, "2026-01-25", "cancelled"),
    ("Arjun",  "27-inch Monitor",     1, 280.0, "2026-02-02", "completed"),
    ("Divya",  "Webcam HD",           2,  60.0, "2026-02-08", "completed"),
    ("Sneha",  "Noise-Cancel Headset",1, 199.0, "2026-02-14", "completed"),
    ("Rahul",  "Wireless Mouse",      4,  25.0, "2026-02-20", "shipped"),
    ("Kiran",  "Laptop Stand",        2,  35.0, "2026-02-27", "completed"),
    ("Arjun",  "USB-C Hub",           1,  45.0, "2026-03-03", "completed"),
    ("Kavya",  "27-inch Monitor",     2, 280.0, "2026-03-09", "completed"),
    ("Divya",  "Mechanical Keyboard", 1, 120.0, "2026-03-15", "cancelled"),
    ("Kiran",  "Webcam HD",           1,  60.0, "2026-03-21", "shipped"),
    ("Sneha",  "Wireless Mouse",      1,  25.0, "2026-03-28", "completed"),
    ("Rahul",  "Noise-Cancel Headset",2, 199.0, "2026-04-04", "completed"),
    ("Arjun",  "Mechanical Keyboard", 2, 120.0, "2026-04-10", "completed"),
    ("Kavya",  "USB-C Hub",           1,  45.0, "2026-04-16", "pending"),
    ("Divya",  "27-inch Monitor",     1, 280.0, "2026-04-22", "completed"),
    ("Kiran",  "Mechanical Keyboard", 1, 120.0, "2026-04-29", "completed"),
    ("Sneha",  "Laptop Stand",        3,  35.0, "2026-05-06", "shipped"),
    ("Rahul",  "Webcam HD",           1,  60.0, "2026-05-13", "cancelled"),
    ("Arjun",  "Noise-Cancel Headset",1, 199.0, "2026-05-20", "completed"),
    ("Kavya",  "Wireless Mouse",      2,  25.0, "2026-05-27", "completed"),
    ("Divya",  "USB-C Hub",           2,  45.0, "2026-05-30", "pending"),
]
 
 
def main():
    client = MongoClient(MONGO_URI)
    orders = client[DB_NAME]["orders"]
 
    if orders.count_documents({}) == 0:          # seed only if Lab 5.1 not run
        orders.insert_many([
            {"order_id": f"ORD-{1001 + i}", "customer": c, "product": p,
             "quantity": q, "unit_price": up,
             "order_date": datetime.strptime(d, "%Y-%m-%d"), "status": s}
            for i, (c, p, q, up, d, s) in enumerate(ORDERS)
        ])
 
    print("=" * 70)
    print("LAB 5.1 TRY IT YOURSELF : $bucket BY ORDER VALUE")
    print("=" * 70)
    print(f"Orders in collection: {orders.count_documents({})}\n")
 
    pipeline = [
        {"$addFields": {"order_value": {"$multiply": ["$quantity", "$unit_price"]}}},
        {"$bucket": {
            "groupBy": "$order_value",
            # [0,50) small | [50,200] medium | (200, inf) large
            # $bucket boundaries are lower-inclusive, so 200.01 makes $200 'medium'
            "boundaries": [0, 50, 200.01, float("inf")],
            "default": "unknown",
            "output": {
                "count": {"$sum": 1},
                "total_value": {"$sum": "$order_value"},
                "avg_value": {"$avg": "$order_value"},
                "orders": {"$push": {"order_id": "$order_id",
                                     "customer": "$customer",
                                     "value": "$order_value"}},
            },
        }},
        {"$addFields": {"category": {"$switch": {
            "branches": [
                {"case": {"$eq": ["$_id", 0]}, "then": "small (< $50)"},
                {"case": {"$eq": ["$_id", 50]}, "then": "medium ($50 - $200)"},
                {"case": {"$eq": ["$_id", 200.01]}, "then": "large (> $200)"},
            ],
            "default": "unknown"}}}},
        {"$project": {"_id": 0, "category": 1, "count": 1,
                      "total_value": 1, "avg_value": {"$round": ["$avg_value", 2]},
                      "orders": 1}},
    ]
    results = list(orders.aggregate(pipeline))
 
    print(f"{'Category':<22}{'Count':>6}{'Total ($)':>12}{'Avg ($)':>10}")
    print("-" * 70)
    for b in results:
        print(f"{b['category']:<22}{b['count']:>6}{b['total_value']:>12.2f}"
              f"{b['avg_value']:>10.2f}")
    print()
    for b in results:
        ids = ", ".join(f"{o['order_id']}(${o['value']:.0f})" for o in b["orders"])
        print(f"{b['category']}:\n   {ids}")
 
    out = os.path.join(HERE, "orders_bucket.json")
    with open(out, "w") as f:
        f.write(json_util.dumps(results, indent=2))
    print(f"\n[OK] Exported bucket results -> {os.path.basename(out)}")
    client.close()
 
 
if __name__ == "__main__":
    main()

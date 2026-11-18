"""
23CSE528: NoSQL Databases using MongoDB
Exercise 5 - Lab 5.1: Build a Sales Analytics Pipeline
Student : M Nagasai Girisharan (24BTRCL114)
 
Tasks:
 1. Create an `orders` collection (customer, product, quantity, unit_price,
    order_date, status) and insert at least 20 orders.
 2. Monthly revenue  -> group by month, sum(quantity * unit_price), sort by month.
 3. Top 3 customers by total spending, with the list of products they bought.
 4. Average order value per status category.
The collection is exported to orders.json (MongoDB Extended JSON).
"""
 
import os
from datetime import datetime
 
from bson import json_util
from pymongo import MongoClient
 
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab5"
HERE = os.path.dirname(os.path.abspath(__file__))
 
ORDERS = [
    # customer, product, quantity, unit_price, order_date, status
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
 
ORDER_VALUE = {"$multiply": ["$quantity", "$unit_price"]}
 
 
def line(char="-", n=70):
    print(char * n)
 
 
def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    orders = db["orders"]
 
    # ---------------- Step 1: create + insert ----------------
    orders.drop()
    docs = [
        {
            "order_id": f"ORD-{1001 + i}",
            "customer": c,
            "product": p,
            "quantity": q,
            "unit_price": up,
            "order_date": datetime.strptime(d, "%Y-%m-%d"),
            "status": s,
        }
        for i, (c, p, q, up, d, s) in enumerate(ORDERS)
    ]
    result = orders.insert_many(docs)
    line("=")
    print("LAB 5.1 : SALES ANALYTICS PIPELINE")
    line("=")
    print(f"[OK] Inserted {len(result.inserted_ids)} orders into {DB_NAME}.orders")
    print(f"     Customers : {sorted(orders.distinct('customer'))}")
    print(f"     Products  : {len(orders.distinct('product'))} distinct\n")
 
    # ---------------- Step 2: monthly revenue ----------------
    print("Q2) Monthly revenue  (sum of quantity * unit_price, sorted by month)")
    line()
    monthly = [
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m", "date": "$order_date"}},
            "revenue": {"$sum": ORDER_VALUE},
            "orders": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
        {"$project": {"_id": 0, "month": "$_id", "revenue": 1, "orders": 1}},
    ]
    print(f"{'Month':<10}{'Orders':>8}{'Revenue ($)':>15}")
    for r in orders.aggregate(monthly):
        print(f"{r['month']:<10}{r['orders']:>8}{r['revenue']:>15.2f}")
    print()
 
    # ---------------- Step 3: top 3 customers ----------------
    print("Q3) Top 3 customers by total spending (with products bought)")
    line()
    top3 = [
        {"$group": {
            "_id": "$customer",
            "total_spent": {"$sum": ORDER_VALUE},
            "order_count": {"$sum": 1},
            "products": {"$addToSet": "$product"},
        }},
        {"$sort": {"total_spent": -1}},
        {"$limit": 3},
        {"$project": {"_id": 0, "customer": "$_id", "total_spent": 1,
                      "order_count": 1,
                      "products": {"$sortArray": {"input": "$products", "sortBy": 1}}}},
    ]
    for rank, r in enumerate(orders.aggregate(top3), 1):
        print(f"#{rank} {r['customer']:<7} spent ${r['total_spent']:.2f} "
              f"in {r['order_count']} orders")
        print(f"    products: {', '.join(r['products'])}")
    print()
 
    # ---------------- Step 4: average order value per status ----------------
    print("Q4) Average order value per status category")
    line()
    by_status = [
        {"$group": {
            "_id": "$status",
            "avg_order_value": {"$avg": ORDER_VALUE},
            "orders": {"$sum": 1},
        }},
        {"$sort": {"avg_order_value": -1}},
        {"$project": {"_id": 0, "status": "$_id", "orders": 1,
                      "avg_order_value": {"$round": ["$avg_order_value", 2]}}},
    ]
    print(f"{'Status':<12}{'Orders':>8}{'Avg Value ($)':>16}")
    for r in orders.aggregate(by_status):
        print(f"{r['status']:<12}{r['orders']:>8}{r['avg_order_value']:>16.2f}")
 
    # ---------------- Export collection to JSON ----------------
    out = os.path.join(HERE, "orders.json")
    with open(out, "w") as f:
        f.write(json_util.dumps(list(orders.find()), indent=2))
    print(f"\n[OK] Exported orders collection -> {os.path.basename(out)}")
    client.close()
 
 
if __name__ == "__main__":
    main()

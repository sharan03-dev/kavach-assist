"""
23CSE528: NoSQL Databases using MongoDB
Exercise 3 - Lab 3.1: Design an Embedded Document Schema
Student : M Nagasai Girisharan (24BTRCL114)

1. University course-enrollment system: each course document EMBEDS its
   roster (name, ID, grade) and its schedule (days, times, room). Insert
   3+ courses and query all courses that meet on Monday.
2. Schema validation ($jsonSchema) requiring title, code (e.g. CS401),
   instructor and schedule.
3. Query all students enrolled in courses taught by a specific instructor.
Collection exported to courses.json.
"""

import os

from bson import json_util
from pymongo import MongoClient
from pymongo.errors import WriteError

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab3"
HERE = os.path.dirname(os.path.abspath(__file__))

VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["title", "code", "instructor", "schedule"],
    "properties": {
        "title": {"bsonType": "string"},
        "code": {"bsonType": "string", "pattern": "^[A-Z]{2,4}[0-9]{3}$",
                 "description": "course code like CS401"},
        "instructor": {"bsonType": "string"},
        "schedule": {
            "bsonType": "object",
            "required": ["days", "time", "room"],
            "properties": {
                "days": {"bsonType": "array", "minItems": 1, "items": {"bsonType": "string"}},
                "time": {"bsonType": "string"},
                "room": {"bsonType": "string"},
            },
        },
        "roster": {
            "bsonType": "array",
            "items": {"bsonType": "object", "required": ["name", "student_id"],
                      "properties": {"name": {"bsonType": "string"},
                                     "student_id": {"bsonType": "string"},
                                     "grade": {"bsonType": ["string", "null"]}}},
        },
    },
}}

COURSES = [
    {"code": "CS401", "title": "NoSQL Databases using MongoDB", "instructor": "Dr. Arvind Kumar",
     "credits": 4,
     "schedule": {"days": ["Monday", "Wednesday"], "time": "09:00-10:30", "room": "B-204"},
     "roster": [{"name": "Aarav", "student_id": "24CS001", "grade": "A"},
                {"name": "Diya", "student_id": "24CS002", "grade": "A+"},
                {"name": "Ishaan", "student_id": "24CS003", "grade": "B+"}]},
    {"code": "CS402", "title": "Machine Learning", "instructor": "Dr. Anita Desai",
     "credits": 4,
     "schedule": {"days": ["Tuesday", "Thursday"], "time": "11:00-12:30", "room": "A-110"},
     "roster": [{"name": "Meera", "student_id": "24CS004", "grade": "A"},
                {"name": "Aarav", "student_id": "24CS001", "grade": "A"}]},
    {"code": "CS405", "title": "Computer Vision", "instructor": "Dr. Arvind Kumar",
     "credits": 3,
     "schedule": {"days": ["Monday", "Friday"], "time": "14:00-15:30", "room": "C-301"},
     "roster": [{"name": "Rohan", "student_id": "24CS005", "grade": "B"},
                {"name": "Ananya", "student_id": "24CS006", "grade": None}]},
    {"code": "MA210", "title": "Discrete Mathematics", "instructor": "Dr. Meena Rao",
     "credits": 3,
     "schedule": {"days": ["Wednesday"], "time": "10:45-12:15", "room": "A-002"},
     "roster": [{"name": "Vikram", "student_id": "24CS007", "grade": "A"}]},
]


def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    db.drop_collection("courses")

    # Step 2 first: create the collection WITH the validator
    db.create_collection("courses", validator=VALIDATOR, validationLevel="strict",
                         validationAction="error")
    courses = db["courses"]
    courses.create_index("code", unique=True)

    print("=" * 76)
    print("LAB 3.1 : EMBEDDED DOCUMENT SCHEMA - COURSE ENROLLMENT")
    print("=" * 76)
    res = courses.insert_many(COURSES)
    print(f"[OK] Created 'courses' with $jsonSchema validator; inserted {len(res.inserted_ids)} courses")

    # Q1: courses meeting on Monday (query inside embedded array)
    print("\nQ1) Courses that meet on Monday   filter {'schedule.days': 'Monday'}")
    print("-" * 76)
    for c in courses.find({"schedule.days": "Monday"}, {"_id": 0, "roster": 0}):
        s = c["schedule"]
        print(f"  {c['code']}  {c['title']:<32} {', '.join(s['days']):<18} {s['time']}  {s['room']}")

    # Q2: validation tests
    print("\nQ2) Schema validation tests")
    print("-" * 76)
    bad_docs = [
        ("missing instructor + schedule", {"code": "CS499", "title": "Test Course"}),
        ("bad code format 'cs-9'", {"code": "cs-9", "title": "X", "instructor": "Y",
                                    "schedule": {"days": ["Monday"], "time": "9", "room": "R"}}),
    ]
    for label, doc in bad_docs:
        try:
            courses.insert_one(doc)
            print(f"  [UNEXPECTED] {label} was accepted")
        except WriteError as e:
            print(f"  [REJECTED] {label}  -> code {e.code} DocumentValidationFailure")
    good = {"code": "CS410", "title": "Big Data", "instructor": "Dr. Anita Desai",
            "schedule": {"days": ["Thursday"], "time": "15:45-17:15", "room": "B-105"},
            "roster": []}
    courses.insert_one(good)
    print("  [ACCEPTED] valid course CS410 inserted")

    # Q3: students taught by a specific instructor
    instructor = "Dr. Arvind Kumar"
    print(f"\nQ3) Students enrolled in courses taught by {instructor}")
    print("-" * 76)
    pipeline = [
        {"$match": {"instructor": instructor}},
        {"$unwind": "$roster"},
        {"$group": {"_id": "$roster.student_id",
                    "name": {"$first": "$roster.name"},
                    "courses": {"$push": "$code"}}},
        {"$sort": {"name": 1}},
    ]
    for s in courses.aggregate(pipeline):
        print(f"  {s['_id']}  {s['name']:<12} courses: {', '.join(s['courses'])}")

    with open(os.path.join(HERE, "courses.json"), "w") as f:
        f.write(json_util.dumps({"validator": VALIDATOR,
                                 "courses": list(courses.find())}, indent=2))
    print("\n[OK] Exported validator + collection -> courses.json")
    client.close()


if __name__ == "__main__":
    main()

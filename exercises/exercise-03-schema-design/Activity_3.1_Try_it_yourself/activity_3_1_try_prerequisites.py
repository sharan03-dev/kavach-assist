"""
23CSE528: NoSQL Databases using MongoDB
Exercise 3 - Activity for 3.1 (Try it yourself)
Student : M Nagasai Girisharan (24BTRCL114)

Requirement: track prerequisites between courses.
Decision   : REFERENCE (store an array of prerequisite course codes), not embed.

Justification:
 * A prerequisite is itself a full course that already exists as its own
   document - embedding a copy would duplicate data and go stale when that
   course changes (title, instructor, schedule).
 * The relationship is many-to-many: one course can be a prerequisite of
   many courses, and a course can need many prerequisites.
 * Prerequisites form a chain (CS101 -> CS201 -> CS301 -> CS401). With
   references, $graphLookup can walk the whole chain recursively.
 * The array of codes is small and bounded, so it costs very little space.
Results are exported to prerequisites.json.
"""

import os

from bson import json_util
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab3"
HERE = os.path.dirname(os.path.abspath(__file__))

COURSES = [
    {"_id": "CS101", "title": "Programming in Python", "prerequisites": []},
    {"_id": "MA101", "title": "Linear Algebra", "prerequisites": []},
    {"_id": "CS201", "title": "Data Structures", "prerequisites": ["CS101"]},
    {"_id": "CS220", "title": "Database Management Systems", "prerequisites": ["CS201"]},
    {"_id": "CS301", "title": "Machine Learning", "prerequisites": ["CS201", "MA101"]},
    {"_id": "CS401", "title": "NoSQL Databases using MongoDB", "prerequisites": ["CS220"]},
    {"_id": "CS450", "title": "Deep Learning", "prerequisites": ["CS301"]},
]


def main():
    client = MongoClient(MONGO_URI)
    col = client[DB_NAME]["courses_prereq"]
    col.drop()
    col.insert_many(COURSES)
    col.create_index("prerequisites")      # multikey index for reverse lookups

    print("=" * 76)
    print("LAB 3.1 TRY IT YOURSELF : COURSE PREREQUISITES (REFERENCED DESIGN)")
    print("=" * 76)
    print("Decision: REFERENCE prerequisites by course code (array of _id values)")
    print("Reason  : many-to-many, avoids duplicated course data, supports chains\n")

    print("Direct prerequisites (resolved with $lookup):")
    print("-" * 76)
    for c in col.aggregate([
        {"$lookup": {"from": "courses_prereq", "localField": "prerequisites",
                     "foreignField": "_id", "as": "pre"}},
        {"$sort": {"_id": 1}}]):
        names = ", ".join(f"{p['_id']} {p['title']}" for p in c["pre"]) or "-- none --"
        print(f"  {c['_id']} {c['title']:<30} needs: {names}")

    target = "CS450"
    print(f"\nFull prerequisite chain for {target} ($graphLookup):")
    print("-" * 76)
    res = col.aggregate([
        {"$match": {"_id": target}},
        {"$graphLookup": {"from": "courses_prereq", "startWith": "$prerequisites",
                          "connectFromField": "prerequisites", "connectToField": "_id",
                          "as": "chain", "depthField": "level"}},
    ]).next()
    for p in sorted(res["chain"], key=lambda x: x["level"]):
        print(f"  level {p['level']}: {p['_id']} {p['title']}")

    unlock = "CS201"
    print(f"\nCourses that directly require {unlock} (reverse query on the array):")
    print("-" * 76)
    for c in col.find({"prerequisites": unlock}, {"title": 1}):
        print(f"  {c['_id']} {c['title']}")

    with open(os.path.join(HERE, "prerequisites.json"), "w") as f:
        f.write(json_util.dumps(list(col.find()), indent=2))
    print("\n[OK] Exported collection -> prerequisites.json")
    client.close()


if __name__ == "__main__":
    main()

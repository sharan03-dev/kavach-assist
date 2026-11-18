"""
23CSE528: NoSQL Databases using MongoDB
Exercise 3 - Lab 3.2: Migrate from Referenced to Embedded Design
Student : M Nagasai Girisharan (24BTRCL114)

1. Blog with separate `posts` and `comments` collections (referenced).
   Insert 5 posts and 10 comments.
2. $lookup query to get a post with its comments in a single query.
3. Redesign with embedding (comments array inside the post) and migrate the
   data with an aggregation pipeline ($lookup + $out).
4. Compare the number of database queries needed to show a post with its
   comments in both designs (counted with a PyMongo command listener).
Both designs are exported to blog.json.
"""

import os
from datetime import datetime, timedelta

from bson import json_util
from pymongo import MongoClient, monitoring

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab3"
HERE = os.path.dirname(os.path.abspath(__file__))


class QueryCounter(monitoring.CommandListener):
    """Counts read commands (find / aggregate) sent to the server."""
    def __init__(self):
        self.count = 0

    def started(self, event):
        if event.command_name in ("find", "aggregate", "getMore"):
            self.count += 1

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


counter = QueryCounter()
BASE = datetime(2026, 9, 1, 10, 0)

POSTS = [
    {"_id": 1, "title": "Why MongoDB?", "author": "Aarav"},
    {"_id": 2, "title": "Embedding vs Referencing", "author": "Diya"},
    {"_id": 3, "title": "Indexing Basics", "author": "Aarav"},
    {"_id": 4, "title": "Aggregation Pipelines 101", "author": "Meera"},
    {"_id": 5, "title": "Schema Validation Tips", "author": "Rohan"},
]
COMMENTS = [  # post_id references posts._id
    (1, "Ishaan", "Great intro!"), (1, "Meera", "Flexible schema is a big win."),
    (1, "Rohan", "What about joins?"), (2, "Aarav", "Clear explanation."),
    (2, "Ananya", "Embedding saved me a query."), (3, "Diya", "Compound indexes next please"),
    (3, "Vikram", "ESR rule is useful"), (4, "Ishaan", "Loved the $group example"),
    (4, "Aarav", "Can you cover $facet?"), (5, "Meera", "Validation caught my bug!"),
]


def main():
    client = MongoClient(MONGO_URI, event_listeners=[counter])
    db = client[DB_NAME]
    for c in ("posts", "comments", "posts_embedded"):
        db.drop_collection(c)

    # ---------- 1. referenced design ----------
    db.posts.insert_many([{**p, "created": BASE + timedelta(days=i)} for i, p in enumerate(POSTS)])
    db.comments.insert_many([
        {"_id": i + 1, "post_id": pid, "user": u, "text": t,
         "created": BASE + timedelta(days=pid, hours=i)}
        for i, (pid, u, t) in enumerate(COMMENTS)])
    db.comments.create_index("post_id")

    print("=" * 76)
    print("LAB 3.2 : MIGRATE FROM REFERENCED TO EMBEDDED DESIGN")
    print("=" * 76)
    print(f"[1] Referenced design: {db.posts.count_documents({})} posts, "
          f"{db.comments.count_documents({})} comments (comments.post_id -> posts._id)")

    # ---------- 2. $lookup in a single query ----------
    print("\n[2] Post 1 with its comments using $lookup (one aggregate query)")
    print("-" * 76)
    post = db.posts.aggregate([
        {"$match": {"_id": 1}},
        {"$lookup": {"from": "comments", "localField": "_id",
                     "foreignField": "post_id", "as": "comments"}},
    ]).next()
    print(f"  '{post['title']}' by {post['author']}")
    for c in post["comments"]:
        print(f"     - {c['user']}: {c['text']}")

    # ---------- 3. migrate to embedded ----------
    print("\n[3] Migrating to embedded design with $lookup + $project + $out")
    print("-" * 76)
    db.posts.aggregate([
        {"$lookup": {"from": "comments", "localField": "_id",
                     "foreignField": "post_id", "as": "comments"}},
        {"$project": {"title": 1, "author": 1, "created": 1,
                      "comments": {"$map": {"input": "$comments", "as": "c",
                                            "in": {"user": "$$c.user", "text": "$$c.text",
                                                   "created": "$$c.created"}}}}},
        {"$out": "posts_embedded"},
    ])
    total = sum(len(p["comments"]) for p in db.posts_embedded.find())
    print(f"  posts_embedded: {db.posts_embedded.count_documents({})} posts, "
          f"{total} embedded comments (no comments lost: {total == len(COMMENTS)})")
    sample = db.posts_embedded.find_one({"_id": 2})
    print(f"  sample -> {{_id: 2, title: '{sample['title']}', comments: "
          f"[{len(sample['comments'])} items]}}")

    # ---------- 4. compare query counts ----------
    print("\n[4] Queries needed to display ONE post with its comments")
    print("-" * 76)

    counter.count = 0                           # referenced, application-side join
    p = db.posts.find_one({"_id": 3})
    cs = list(db.comments.find({"post_id": 3}))
    ref_app = counter.count

    counter.count = 0                           # referenced, $lookup
    list(db.posts.aggregate([{"$match": {"_id": 3}},
                             {"$lookup": {"from": "comments", "localField": "_id",
                                          "foreignField": "post_id", "as": "c"}}]))
    ref_lookup = counter.count

    counter.count = 0                           # embedded
    e = db.posts_embedded.find_one({"_id": 3})
    emb = counter.count

    print(f"  {'Design':<42}{'Queries':>8}   Notes")
    print(f"  {'Referenced (find post + find comments)':<42}{ref_app:>8}   2 round trips")
    print(f"  {'Referenced ($lookup)':<42}{ref_lookup:>8}   1 query, server joins 2 collections")
    print(f"  {'Embedded (find_one)':<42}{emb:>8}   1 query, 1 document, no join")
    print(f"\n  Same data both ways: {len(cs)} comments vs {len(e['comments'])} embedded")

    with open(os.path.join(HERE, "blog.json"), "w") as f:
        f.write(json_util.dumps({"referenced": {"posts": list(db.posts.find()),
                                                "comments": list(db.comments.find())},
                                 "embedded": {"posts_embedded": list(db.posts_embedded.find())}},
                                indent=2))
    print("\n[OK] Exported both designs -> blog.json")
    client.close()


if __name__ == "__main__":
    main()

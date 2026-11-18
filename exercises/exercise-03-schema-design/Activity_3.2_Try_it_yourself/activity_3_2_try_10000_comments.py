"""
23CSE528: NoSQL Databases using MongoDB
Exercise 3 - Activity for 3.2 (Try it yourself)
Student : M Nagasai Girisharan (24BTRCL114)

Scenario: one post has 10,000 comments.

Why embedding is a problem
 1. 16 MB document limit - comments keep growing without bound
    (unbounded array anti-pattern); a viral post can hit the limit and
    every new comment insert will then fail.
 2. Every read of the post loads ALL comments, even if the page shows 10.
 3. Every new comment rewrites/grows a huge document -> slow writes and
    more memory and network use.
 4. Indexes on comment fields become very large multikey indexes.

Alternative design: SUBSET + REFERENCE (and optional BUCKET pattern)
 * posts keep only the latest 5 comments embedded + a comment_count
   (fast first page, one query).
 * ALL comments live in a separate `comments` collection referencing
   post_id, indexed on (post_id, created) for paging.
This script measures both designs. Results exported to comment_design.json.
"""

import os
import time
from datetime import datetime, timedelta

from bson import BSON, json_util
from pymongo import MongoClient, DESCENDING
from pymongo.errors import DocumentTooLarge, WriteError

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "nosql_lab3"
HERE = os.path.dirname(os.path.abspath(__file__))
N = 10_000
LIMIT = 16 * 1024 * 1024
BASE = datetime(2026, 9, 1)


def make_comment(i):
    return {"user": f"user{i % 500}", "text": f"Comment number {i} on this viral post. " * 3,
            "likes": i % 50, "created": BASE + timedelta(seconds=i)}


def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    for c in ("viral_embedded", "viral_posts", "viral_comments"):
        db.drop_collection(c)

    print("=" * 76)
    print("LAB 3.2 TRY IT YOURSELF : A POST WITH 10,000 COMMENTS")
    print("=" * 76)

    comments = [make_comment(i) for i in range(N)]

    # ----- Design A: everything embedded -----
    doc = {"_id": 1, "title": "Viral post", "comments": comments}
    size = len(BSON.encode(doc))
    db.viral_embedded.insert_one(doc)
    per = size / N
    print("[A] Fully EMBEDDED design")
    print(f"    document size with {N:,} comments : {size/1024/1024:.2f} MB "
          f"({size/LIMIT*100:.1f}% of the 16 MB limit)")
    print(f"    ~{per:.0f} bytes per comment -> limit reached at ~{int(LIMIT/per):,} comments")

    t = time.perf_counter()
    for _ in range(20):
        db.viral_embedded.find_one({"_id": 1})
    a_read = (time.perf_counter() - t) / 20 * 1000
    print(f"    read post page (loads ALL comments) : {a_read:.2f} ms, {size/1024:.0f} KB transferred")

    # prove the hard limit: try to grow the doc past 16 MB
    try:
        big = [make_comment(i) for i in range(int(LIMIT / per) + 5000)]
        db.viral_embedded.replace_one({"_id": 1}, {"title": "Viral post", "comments": big})
        print("    grow past 16 MB                      : accepted (unexpected)")
    except (DocumentTooLarge, WriteError) as e:
        print(f"    grow past 16 MB                      : FAILED -> {type(e).__name__}")

    # ----- Design B: subset + reference -----
    db.viral_comments.insert_many([{**c, "post_id": 1} for c in comments])
    db.viral_comments.create_index([("post_id", 1), ("created", DESCENDING)])
    latest = list(db.viral_comments.find({"post_id": 1}, {"_id": 0, "post_id": 0})
                  .sort("created", -1).limit(5))
    db.viral_posts.insert_one({"_id": 1, "title": "Viral post", "comment_count": N,
                               "recent_comments": latest})
    bsize = len(BSON.encode(db.viral_posts.find_one({"_id": 1})))

    t = time.perf_counter()
    for _ in range(20):
        db.viral_posts.find_one({"_id": 1})
    b_read = (time.perf_counter() - t) / 20 * 1000

    def page(n, size=20):
        return list(db.viral_comments.find({"post_id": 1}).sort("created", -1)
                    .skip((n - 1) * size).limit(size))

    t = time.perf_counter()
    p3 = page(3)
    b_page = (time.perf_counter() - t) * 1000
    print("\n[B] SUBSET + REFERENCE design")
    print(f"    post document (5 recent + count)    : {bsize/1024:.2f} KB")
    print(f"    read post page                      : {b_read:.2f} ms")
    print(f"    load comments page 3 (20 per page)  : {b_page:.2f} ms, {len(p3)} comments")
    plan = db.viral_comments.find({"post_id": 1}).sort("created", -1).limit(20).explain()
    stage = plan["queryPlanner"]["winningPlan"]
    while "inputStage" in stage and stage["stage"] != "IXSCAN":
        stage = stage["inputStage"]
    print(f"    paging uses index                   : {stage['stage']} {stage.get('indexName', '')}")

    print("\nSummary")
    print("-" * 76)
    print(f"  Post read: embedded {a_read:.2f} ms vs subset {b_read:.2f} ms "
          f"(~{a_read / max(b_read, 0.001):.0f}x faster)")
    print("  Embedded design is unbounded and fails at 16 MB; the subset +")
    print("  reference design stays small no matter how many comments arrive.")

    with open(os.path.join(HERE, "comment_design.json"), "w") as f:
        f.write(json_util.dumps({
            "embedded_doc_size_bytes": size, "embedded_read_ms": round(a_read, 3),
            "subset_post": db.viral_posts.find_one({"_id": 1}),
            "subset_read_ms": round(b_read, 3),
            "sample_comment_documents": list(db.viral_comments.find().limit(5)),
        }, indent=2))
    print("[OK] Exported results -> comment_design.json")
    client.close()


if __name__ == "__main__":
    main()

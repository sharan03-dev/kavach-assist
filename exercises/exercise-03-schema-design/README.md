# Exercise 3 - Schema design: embedding vs referencing
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

Exercise 3 decides when to embed related data inside one document and when to reference it from another collection.

| Folder | What it does | Output |
|---|---|---|
| `Activity_3.1` | a university course-enrollment system: each course embeds its roster and schedule, `$jsonSchema` validation requires title, code, instructor and schedule, and queries find Monday courses and the students of one instructor | `courses.json` |
| `Activity_3.1_Try_it_yourself` | course prerequisites: the decision is to REFERENCE (an array of course codes) because a prerequisite is itself a course that would go stale if copied | `prerequisites.json` |
| `Activity_3.2` | a blog with separate `posts` and `comments`: a `$lookup` query, then a redesign with embedded comments migrated with `$lookup` + `$out`, and a comparison of how many database queries each design needs | `blog.json` |
| `Activity_3.2_Try_it_yourself` | one post with 10,000 comments: why embedding fails (16 MB limit, every read loads all comments) and the better design | `comment_design.json` |

Rule of thumb: embed what is read together and stays small; reference what grows without limit or is shared.

## Run
```bash
pip install pymongo
python exercises/exercise-03-schema-design/Activity_3.1/activity_3_1_embedded_schema.py
python exercises/exercise-03-schema-design/Activity_3.2/activity_3_2_blog_migration.py
```
The same ideas are used on the real Kavach platform in `labs/lab7_1/`.

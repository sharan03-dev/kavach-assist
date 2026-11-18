# 23CSE528 NoSQL Databases using MongoDB - all lab experiments

**M Nagasai Girisharan - 24BTRCL114 - 5 AIML-B**

This repository is the consolidated submission for the MongoDB lab: every lab experiment (Chapters 1-18),
the mini project (Kavach Assist, an LLM/SLM-backed chat application on MongoDB) and their documentation.

| Ch. | Experiment | Code |
|---|---|---|
| 1 | NoSQL database families | `exercises/exercise-01-nosql-families/` |
| 2 | Environment setup and CRUD with PyMongo | `exercises/exercise-02-setup-and-crud/` |
| 3 | Schema design: embedding vs referencing | `exercises/exercise-03-schema-design/` |
| 4 | Query operators, upserts and bulk writes | `exercises/exercise-04-queries-upserts-bulk/` |
| 5 | Aggregation framework and $lookup joins | `exercises/exercise-05-aggregation-lookup/` |
| 6 | Indexing and query performance | `exercises/exercise-06-indexing/` |
| 7 | Production schema and working-set analysis (Kavach CTI platform) | `backend/`, `frontend/`, `labs/lab7_1/`, `labs/lab7_2/` |
| 8 | Replica sets, failover and elections | `labs/lab8/` |
| 9 | Write concern latency and network partitions (CAP) | `labs/lab9/` |
| 10 | Transactions, atomicity, durability and journaling | `labs/lab10/` |
| 11 | Sharding and horizontal scalability | `labs/lab11/` |
| 12 | Database security and the CIA triad | `labs/lab12/` |
| 13 | Deploying MongoDB with containers and the cloud | `labs/lab13/` |
| 14 | Managing database services in production | `labs/lab14/` |
| 15 | MongoDB-backed product as a service (TaskTracker) | `labs/lab15/` |
| 16 | Wide-column and graph databases: Cassandra and Neo4j | `labs/lab16/` |
| 17 | AI-native MongoDB: vector search and RAG | `labs/lab17/` |
| 18 | Comparative capstone: false positives in threat intelligence | `labs/lab18/` |
| Project | Kavach Assist - LLM/SLM chat application on MongoDB | `backend/app/assist/`, `frontend/assets/assist.js` |

Exercises 1-6 run against a local MongoDB (`mongodb://localhost:27017`, override with `MONGO_URI`):

```bash
pip install pymongo
python exercises/exercise-01-nosql-families/Activity_1.1/activity_1_1_classify_databases.py
```

Labs 7-18 and the project run with Docker Compose - see the main `README.md`.

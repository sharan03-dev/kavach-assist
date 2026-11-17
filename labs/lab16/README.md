# Exercise 16 - Wide-Column and Graph Databases: Cassandra and Neo4j
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528

| Part | What runs | Where |
|---|---|---|
| **16.1 Model data in three databases** | a university enrollment system (300 students, 24 courses) in MongoDB (embedded `course_ids` + `roster`), Cassandra (`student_courses`, `course_students`) and Neo4j (`(:Student)-[:ENROLLED_IN]->(:Course)`); 3 queries x 3 databases, answers compared, latency (5 args x 10 runs) | website *Cassandra + Neo4j lab 16* -> *Three data models* |
| **Activity: hybrid architecture** | MongoDB = operational documents (enrollment in one transaction), Cassandra = audit log `audit_log ((entity_id, day), at DESC)`, Neo4j = prerequisite chains `[:REQUIRES*1..]` - an enrollment is refused until the chain is COMPLETED | tab *Hybrid architecture* |
| **16.2 Hybrid query service** | `fraud_service.py`: `FraudQueryService` with `get_transaction` (MongoDB), `get_user_profile` (Cassandra, partition user_id, clustering event_time DESC), `detect_fraud_rings` (Neo4j, up to 3 hops + cycles), connection pooling, circuit breaker (3 failures -> skip, retry after 30 s), 12 unit tests with `unittest.mock`, integration test on planted rings, latency per method | tab *FraudQueryService* |
| **16 on 10 (mentor's rule)** | the Lab 10 billing ledger -> transactions + profile events + User nodes -> the same fraud check | tab *Applied to Lab 10* |

```bash
docker compose up -d lab16                      # Lab 7 -> ... -> 15 -> 16 (+ cassandra, neo4j)
open http://localhost:8000/#/lab16
docker compose exec lab16 lab16 all             # everything + results/Exercise-16_Report.pdf
docker compose exec lab16 python -m unittest -v labs/lab16/test_fraud_service.py
docker exec -it cassandra cqlsh                 # DESCRIBE KEYSPACE lab16;
open http://localhost:7474                      # Neo4j Browser: neo4j / Lab16-neo4j!
```
Files: `schema.cql`, `schema.cypher`, `queries.md`, `fraud_service.py`, `test_fraud_service.py`, `run_lab16.py`, logic in `backend/app/lab16.py`.

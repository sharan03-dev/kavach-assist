# Labs 7 to 18 - one folder and one README per lab
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

Every lab has its own folder with its own README (what the lab proves, the files, and how to run it). All labs run on the Kavach
platform through Docker Compose, and each lab starts only after the lab before it is healthy (Lab 18 needs 17, 17 needs 16, and so on down to Lab 7).

| Lab | Topic | Folder and README | Website page |
|---|---|---|---|
| 7.1 | Production schema: embedding vs referencing | [lab7_1](lab7_1/README.md) | `#/lab71` |
| 7.2 | Working set and the WiredTiger cache | [lab7_2](lab7_2/README.md) | `#/lab72` |
| 8 | Replica set and high availability | [lab8](lab8/README.md) | `#/lab8` |
| 9 | Write concern and network partitions | [lab9](lab9/README.md) | `#/lab9` |
| 10 | Multi-document ACID transactions | [lab10](lab10/README.md) | `#/lab10` |
| 11 | Sharding | [lab11](lab11/README.md) | `#/lab11` |
| 12 | Security | [lab12](lab12/README.md) | `#/lab12` |
| 13 | Containers and cloud | [lab13](lab13/README.md) | `#/lab13` |
| 14 | Operations, monitoring and backup | [lab14](lab14/README.md) | `#/lab14` |
| 15 | Multi-tenant SaaS (TaskTracker) | [lab15](lab15/README.md) | `#/lab15` |
| 16 | Cassandra and Neo4j (polyglot persistence) | [lab16](lab16/README.md) | `#/lab16` |
| 17 | Vector search and RAG | [lab17](lab17/README.md) | `#/lab17` |
| 18 | Capstone: MongoDB vs Cassandra vs Neo4j | [lab18](lab18/README.md) | `#/lab18` |

`final/` holds the final assessment helper (the Kavach Assist chat application, see the main README). Reports for every lab are written
to the `results/` folder as PDF. Only one lab should run at a time, because Lab 13 deliberately crashes database members.

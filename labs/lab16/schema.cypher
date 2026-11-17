// Lab 16 - Neo4j schema: nodes + relationships. Constraints give unique ids AND an index for the start node lookup.
CREATE CONSTRAINT student_id IF NOT EXISTS FOR (s:Student) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT course_id IF NOT EXISTS FOR (c:Course) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE;
// (:Student)-[:ENROLLED_IN {term}]->(:Course)      16.1
// (:Student)-[:COMPLETED {grade}]->(:Course)       activity: what a student has passed
// (:Course)-[:REQUIRES]->(:Course)                 activity: prerequisite chains
// (:User)-[:TRANSFERRED_TO {txn_id, amount, ts}]->(:User)   16.2 money-transfer graph

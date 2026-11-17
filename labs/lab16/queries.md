# Lab 16.1 - the same three questions in three databases

| Question | MongoDB (document) | Cassandra (query-driven tables) | Neo4j (graph) |
|---|---|---|---|
| Courses of a student | `db.courses.find({"roster.student_id": sid}, {title: 1})` | `SELECT course_id, course_title FROM lab16.student_courses WHERE student_id = ?` | `MATCH (:Student {id: $sid})-[:ENROLLED_IN]->(c:Course) RETURN c.id, c.title` |
| Students in a course | `db.courses.findOne({_id: cid}, {roster: 1})` | `SELECT student_id, student_name FROM lab16.course_students WHERE course_id = ?` | `MATCH (s:Student)-[:ENROLLED_IN]->(:Course {id: $cid}) RETURN s.id, s.name` |
| Students who share a course with X | aggregate: `$match roster.student_id` -> `$unwind roster` -> `$group` | 1 + N queries (student_courses, then course_students per course) + union in Python | `MATCH (s1:Student {id:$sid})-[:ENROLLED_IN]->(c)<-[:ENROLLED_IN]-(s2) WHERE s2 <> s1 RETURN DISTINCT s2.id` |

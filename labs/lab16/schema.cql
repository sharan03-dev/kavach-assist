-- Lab 16 - Cassandra schema (keyspace lab16). Query-driven design: ONE table per query, data written to every table
-- that needs it (denormalisation). Partition key = what the query filters on; clustering columns = the sort order.

CREATE KEYSPACE IF NOT EXISTS lab16 WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};

-- 16.1 query "all courses of a student"  -> one partition per student
CREATE TABLE IF NOT EXISTS lab16.student_courses (
    student_id   text,
    course_id    text,
    course_title text,
    credits      int,
    dept         text,
    PRIMARY KEY ((student_id), course_id)
);

-- 16.1 query "all students in a course"  -> one partition per course
CREATE TABLE IF NOT EXISTS lab16.course_students (
    course_id    text,
    student_id   text,
    student_name text,
    year         int,
    PRIMARY KEY ((course_id), student_id)
);

-- Activity 16.1: audit log (append-only, time-ordered, partitioned by entity and day so a partition never grows forever)
CREATE TABLE IF NOT EXISTS lab16.audit_log (
    entity_id text,
    day       date,
    at        timeuuid,
    action    text,
    actor     text,
    outcome   text,
    details   text,
    PRIMARY KEY ((entity_id, day), at)
) WITH CLUSTERING ORDER BY (at DESC);

-- 16.2 user_profiles: partitioned by user_id, clustering column event_time (newest first)
CREATE TABLE IF NOT EXISTS lab16.user_profiles (
    user_id    text,
    event_time timestamp,
    txn_id     text,
    event_type text,
    amount     double,
    merchant   text,
    country    text,
    PRIMARY KEY ((user_id), event_time, txn_id)
) WITH CLUSTERING ORDER BY (event_time DESC, txn_id ASC);

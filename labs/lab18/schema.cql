-- Lab 18 - Cassandra schema (keyspace lab18): one table per access pattern; every threat is written to all of them.
CREATE KEYSPACE IF NOT EXISTS lab18 WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};

-- Q1 threat history of a source (newest first). month in the partition key: a partition never grows without limit.
CREATE TABLE IF NOT EXISTS lab18.threats_by_source (
    source text, month text, ts timestamp, threat_id uuid,
    indicator text, ind_type text, threat_type text, family text, confidence int,
    PRIMARY KEY ((source, month), ts, threat_id)
) WITH CLUSTERING ORDER BY (ts DESC, threat_id ASC);

-- type-based analytics (the mentor's second table): all threats of one type on one day
CREATE TABLE IF NOT EXISTS lab18.threats_by_type (
    threat_type text, day date, ts timestamp, threat_id uuid,
    source text, indicator text, family text, confidence int,
    PRIMARY KEY ((threat_type, day), ts, threat_id)
) WITH CLUSTERING ORDER BY (ts DESC, threat_id ASC);

-- Q2 target and spread, step 1: every sighting of one indicator
CREATE TABLE IF NOT EXISTS lab18.threats_by_indicator (
    indicator text, ts timestamp, threat_id uuid,
    source text, family text, threat_type text, confidence int,
    PRIMARY KEY ((indicator), ts, threat_id)
) WITH CLUSTERING ORDER BY (ts DESC, threat_id ASC);

-- Q2 target and spread, step 2: all threats of one malware family on one day
CREATE TABLE IF NOT EXISTS lab18.threats_by_family (
    family text, day date, ts timestamp, threat_id uuid,
    source text, indicator text, confidence int,
    PRIMARY KEY ((family, day), ts, threat_id)
) WITH CLUSTERING ORDER BY (ts DESC, threat_id ASC);

-- Q3 daily volume analytics: counters maintained on write (no aggregation at read time)
CREATE TABLE IF NOT EXISTS lab18.daily_volume (
    month text, day date, threat_type text, n counter,
    PRIMARY KEY ((month), day, threat_type)
);

-- false-positive check: which sources reported an indicator (a set, updated on write)
CREATE TABLE IF NOT EXISTS lab18.indicator_sources (
    indicator text PRIMARY KEY, sources set<text>, max_conf int, ind_type text, family text
);

CREATE TABLE IF NOT EXISTS lab18.allowlist (indicator text PRIMARY KEY, reason text);

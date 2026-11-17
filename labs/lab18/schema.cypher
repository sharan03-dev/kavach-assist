// Lab 18 - Neo4j schema
CREATE CONSTRAINT l18_source IF NOT EXISTS FOR (s:Source) REQUIRE s.name IS UNIQUE;
CREATE CONSTRAINT l18_indicator IF NOT EXISTS FOR (i:Indicator) REQUIRE i.value IS UNIQUE;
CREATE CONSTRAINT l18_family IF NOT EXISTS FOR (f:Family) REQUIRE f.name IS UNIQUE;
CREATE CONSTRAINT l18_asn IF NOT EXISTS FOR (a:ASN) REQUIRE a.asn IS UNIQUE;
CREATE INDEX l18_reported_day IF NOT EXISTS FOR ()-[r:REPORTED]-() ON (r.day);
CREATE INDEX l18_reported_ts IF NOT EXISTS FOR ()-[r:REPORTED]-() ON (r.ts);
// (:Source)-[:REPORTED {threat_id, ts, day, type, confidence}]->(:Indicator {value, type, family, asn, country, allowlisted})
// (:Indicator)-[:OF_FAMILY]->(:Family)   (:Indicator)-[:IN_ASN]->(:ASN)
// (:Source)-[:SUSPICIOUS {shared_benign}]-(:Source)  - feeds that share false positives

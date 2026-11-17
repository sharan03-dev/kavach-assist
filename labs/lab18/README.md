# Exercise 18 - Comparative Capstone Lab: false positives from the threat-intelligence service
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528

* **Data**: 100,000 recent threats from many feeds - every real sighting of the Kavach platform (Lab 7) first, the rest from 12 simulated feeds that
  also produce realistic false positives (known-good infrastructure, uncorroborated noise) and 8 coordinated campaigns, so the detector can be scored.
* **Schemas** (`schema.cql`, `schema.cypher`, `backend/app/lab18.py`): MongoDB `sources.recent_threats` (last 50 embedded) + `full_threats`, index (from_source, timestamp);
  Cassandra `threats_by_source` + `threats_by_type` (+ the tables the other queries need: by indicator, by family, daily_volume counters, indicator_sources);
  Neo4j `(:Source)-[:REPORTED]->(:Indicator)-[:OF_FAMILY]->(:Family)` and `SUSPICIOUS` between feeds that share false positives.
* **18.1**: 4 benchmark queries (threat history, target and spread, daily volume analytics, real-time insert throughput), 10 runs each, median; answers compared;
  the same false-positive detector on all three (precision / recall against the labels); **activity**: Louvain communities on the co-reporting graph read from Neo4j,
  flagged by density x link frequency, cross-referenced with the MongoDB documents.
* **18.2**: `results/Exercise-18_Report.pdf` - executive summary, methodology, schema design, query performance, scalability 1M/10M/100M (measured bytes per threat,
  storage / RAM / nodes), horizontal scaling, ergonomics + operations, recommendation per access pattern.
* **Applied to Lab 7**: verdicts in `cti_platform.fp_verdicts`.

```bash
docker compose up -d lab18          # EVERYTHING: Lab 7 -> 8 -> ... -> 17 -> 18
open http://localhost:8000/#/lab18
docker compose exec lab18 lab18 all # 100,000 threats + report (5-10 min);  lab18 quick = 10,000
```

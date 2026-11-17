# Lab 7.2 - Working Set Analysis (answers)

## Setup
100,000 documents of exactly 2,048 bytes (checked with `$bsonSize`) built from the 465 real reports in
awesome-annual-security-reports, inserted into `cti_lab72.ws_reports` with an index on `seq`.
Data about 200 MB + indexes about 3 MB = the working set for random reads.

## Cache metrics (db.serverStatus().wiredTiger.cache)
| Metric | Meaning |
|---|---|
| maximum bytes configured | cache size; default = larger of 50 % of (RAM - 1 GB) or 256 MB |
| bytes currently in the cache | how full the cache is |
| tracked dirty bytes in the cache | modified pages not yet written to disk |
| pages requested from the cache | every page access (hits + misses) |
| pages read into cache | misses: page had to be read from disk |
| unmodified / modified pages evicted | pages pushed out to make room |
| pages evicted by application threads | eviction threads cannot keep up - a sign of cache pressure |

**Hit ratio** over an interval = 1 - (delta pages read into cache / delta pages requested from the cache).
**% of collection in cache** = `$collStats.storageStats.wiredTiger.cache["bytes currently in the cache"]`
divided by the collection's data size.

## Observations (fill in with your own run)
- On an 8 GB Mac the cache is about 3.5 GB, the working set (~205 MB) is ~6 % of it, so it fits.
- Right after a restart (`brew services restart mongodb-community@8.0`, then `--reads-only`) the first
  intervals show a lower hit ratio because pages come from disk; within seconds it climbs above 99 %.
- With `--small-cache 128` the working set (205 MB) no longer fits (128 MB x 80 % eviction target), so the
  hit ratio stays lower, pages are read from disk continuously and evictions appear.
- Conclusion: size RAM so data + indexes that are actively used fit in ~80 % of the WiredTiger cache.

# Activity 7.2 - Monitoring script
`labs/lab7_2/activity_7_2_monitor.py` samples serverStatus every 10 s and prints interval hit ratio,
cumulative hit ratio, cache used %, dirty %, pages read from disk, evictions and application-thread
evictions. A background thread browses the security news feeds and the annual-reports list and inserts
only reports not already in the database (unique index on `url` / `_id`), so you can see ingestion's effect
on the cache. Samples go to the capped collection `cache_metrics` (live chart on the Cache lab page) and to
`labs/lab7_2/output/cache_monitor.csv`. Use `--workload` to add random reads.

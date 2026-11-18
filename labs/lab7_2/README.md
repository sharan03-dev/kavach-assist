# Lab 7.2 - Working Set Analysis (the WiredTiger cache)
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

Lab 7.2 measures how MongoDB's storage engine (WiredTiger) uses its cache. It builds a **working set of 100,000 documents of exactly
2,048 bytes** (checked with `$bsonSize`) from the 465 real reports of the *awesome-annual-security-reports* list, inserted into
`cti_lab72.ws_reports` with an index on `seq` (about 200 MB of data plus about 3 MB of indexes), then reads it randomly and watches the
cache hit ratio.

| Part | What it does | File |
|---|---|---|
| **Lab 7.2** | inserts the 100k documents, reads the cache metrics from `db.serverStatus().wiredTiger.cache`, computes the hit ratio and the percentage of the collection that is in the cache, and runs 60 seconds of random reads (optionally again with a small 128 MB cache). | `lab_02_working_set_analysis.py` |
| **Activity 7.2** | a monitoring script: every 10 seconds it reads the cache metrics and reports the hit ratio; in the background it looks for newer threat reports (security news feeds and the annual-reports list) and inserts only the ones not already stored. | `activity_7_2_monitor.py` |
| **Written answers** | the answers to the lab questions and the meaning of each cache metric. | `ANSWERS.md` |

## Key ideas
- **Hit ratio** over an interval = 1 - (pages read into the cache / pages requested from the cache). Near 100 % means the working set fits in memory.
- **Working set** = the data and indexes that are used often. If it is larger than the cache, pages are evicted and re-read from disk and latency rises.
- Signs of cache pressure: many pages evicted by application threads and a falling hit ratio.
- The default cache size is the larger of 50 % of (RAM - 1 GB) or 256 MB.

## Run
```bash
docker compose up -d --build lab7
docker compose exec lab7 python labs/lab7_2/lab_02_working_set_analysis.py                    # insert 100k docs + analyse + 60 s of reads
docker compose exec lab7 python labs/lab7_2/lab_02_working_set_analysis.py --reads-only       # skip the insert
docker compose exec lab7 python labs/lab7_2/activity_7_2_monitor.py --workload --duration 300 # monitor with generated reads
open http://localhost:8000/#/lab72                                                            # the website's Cache lab 7.2 page (live chart)
```
The monitor writes `labs/lab7_2/output/cache_monitor.csv` and the capped collection `cti_platform.cache_metrics`, which the website chart reads.

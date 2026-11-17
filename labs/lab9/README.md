# Exercise 9 – Write Concern Latency and Network Partitions (CAP Theorem in Practice)

M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

| Directory | Activity | Script(s) |
|---|---|---|
| `Activity_9.1_Write_Concern_Latency` | Lab 9.1 – w:1 vs w:majority vs w:all, 1,000 inserts each | `write_concern_latency.py` |
| `Activity_9.1_TryItYourself_Journaling` | Lab 9.1 activity – j:true vs j:false for every w | `journal_latency.py` |
| `Activity_9.2_Network_Partition` | Lab 9.2 – network partition (scenario A: one secondary, B: both) | `simulate_partition.sh`, `partition_observer.py`, `partition_writer.py`, `isolated_view.js` |
| `common/` | shared code | `rs.py` (connections, rs.status, event log), `bench.py` (benchmark + explanations) |
| `setup/` | replica-set tooling | `rsctl.py` (init / status / waits / mongod-log / timeline), `init_replica_set.js` |
| `report/` | report | `make_report.py` → `results/Exercise-9_Report.pdf` + `.docx` |
| root | Docker | `docker-compose.yml`, `docker-compose.hub.yml`, `Dockerfile`, `docker-entrypoint.sh`, `lab9` (menu), `publish.sh` |

Topology = Exercise 8: `mongo1:27017` (priority 2), `mongo2:27018`, `mongo3:27019`, replica set `rs0`, MongoDB 7.0,
plus `lab9-runner` (all scripts) on the same Docker network `rslab9-net` – no hosts-file edit needed.

---

## A. Everything in one command (recommended)

Docker Desktop must be running, Exercise 8 stopped (`docker compose down` in its folder).

```bash
cd M_NAGASAI_GIRISHARAN_24BTRCL114_LAB9
./publish.sh <your-dockerhub-username>
```

It logs in to Docker Hub, builds the image for Intel **and** Apple-Silicon, pushes it to
`https://hub.docker.com/r/<user>/nosql-lab9`, starts the replica set **from the pushed image**, runs all labs
(~6 min) and writes **`results/Exercise-9_Report.pdf`** – the file to submit. It already contains the Docker Hub URL,
every result table, charts, the captured terminal output of every lab, the timelines, explanations and all code.
No screenshots have to be pasted by hand.

## B. Step by step

```bash
# 1. put your Docker Hub user in .env (both lines)
docker compose up -d --build
docker compose ps                                  # wait until lab9-runner is "healthy"
docker exec -it lab9-runner lab9 status

docker exec -it lab9-runner lab9 9.1               # Lab 9.1            (~1 min)
docker exec -it lab9-runner lab9 9.1-journal       # Lab 9.1 activity   (~2 min)
docker exec -it lab9-runner lab9 9.2               # Lab 9.2            (~2 min)

docker login && docker compose push lab9           # (Apple Silicon: prefer ./publish.sh = multi-arch)
docker exec -it lab9-runner lab9 report https://hub.docker.com/r/<user>/nosql-lab9
```
Each lab's console output is saved to `results/console_<lab>.txt` and appears in the report automatically.

## C. Demonstration for grading (from Docker Hub only)

```bash
docker run --rm <user>/nosql-lab9 compose > docker-compose.yml
docker compose up -d
docker exec -it lab9-runner lab9 9.1
docker exec -it lab9-runner lab9 9.1-journal
docker exec -it lab9-runner lab9 9.2 --step        # pauses before every step so you can explain it
```
Second terminal during Lab 9.2 (Terminal 1 of the handout): `docker exec -it lab9-runner lab9 observer`

### Lab 9.2 by hand – the three-terminal version from the handout
```bash
# Terminal 1 – observer
docker exec -it lab9-runner lab9 observer
# Terminal 2 – partition one secondary
docker ps
docker network disconnect rslab9-net mongo3
# Terminal 3 – writes during the partition
docker exec -it lab9-runner lab9 writer --phase manual --duration 30 --concerns 1,majority --reads
# Terminal 2 – after 30 s heal it and watch Terminal 1 show mongo3 catching up
docker network connect rslab9-net mongo3
# isolate BOTH secondaries: the primary steps down after ~10 s, the set becomes read-only
docker network disconnect rslab9-net mongo3 && docker network disconnect rslab9-net mongo2
# reconnect both, watch the election and then mongo1 (priority 2) taking its role back
docker network connect rslab9-net mongo3 && docker network connect rslab9-net mongo2
docker exec -it lab9-runner lab9 verify
```
Isolated member's own view: `docker exec mongo3 mongosh --port 27019 --quiet --eval "$(cat Activity_9.2_Network_Partition/isolated_view.js)"`

## What the report contains
Docker Hub URL and run commands · results at a glance · environment · for every lab: objective, prediction,
method, **captured terminal output**, comparison tables, chart, data-driven explanation, prediction-vs-result ·
handout trade-off table with measured latency · chapter README "check your work" order · Lab 9.2 key-moment
timelines (exact step-down / election / priority-takeover times from the mongod logs), durability check, observer
output, full event timeline, CAP interpretation · conclusion · all source code.

## Notes on correctness (differences from the reference scripts)
- `insert_one()` has no `write_concern` argument; the reference `insert_one(doc, write_concern=wc)` raises `TypeError`. Each configuration uses its own `MongoClient(..., w=..., journal=...)`.
- MongoDB has no built-in `w:"all"`; the script records the server's rejection and uses `w:3` (= every data-bearing member).
- With `writeConcernMajorityJournalDefault: true` (default) `w:"majority"` already waits for the journal, so j:true/j:false hardly differ for w:majority; for w:1, `j:true` forces a journal flush per write – proven with WiredTiger's `log sync operations` counter.
- Differences are only called effects when they exceed the round-to-round spread (5 rotated rounds).
- A partitioned member is **not** removed from `rs.status()`; it stays listed with `health: 0`, `(not reachable/healthy)`.
- The partition script picks real SECONDARY members from `rs.status()` (the reference could pick the primary), keeps the partition 30 s, records the priority takeover after the election and always reconnects the network.
- With chained replication (default) a secondary may replicate from another secondary; if that one is cut off, w:majority can stall briefly until a new sync source is chosen. `lab9 status` shows each member's sync source and the report explains it if it happens.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `Conflict. The container name "/mongo1" is already in use` | Exercise 8 still running – `docker compose down` there. |
| `lab9-runner` stays "starting" | `docker logs lab9-runner` – mongo containers still starting; it retries. |
| `permission denied ... docker.sock` | Docker Desktop → Settings → Advanced → allow the default Docker socket. |
| Lab 9.2 interrupted | `docker network connect rslab9-net mongo2` / `mongo3` (the script also does this on Ctrl+C). |
| `buildx` error in publish.sh | Update Docker Desktop, or use step-by-step mode B. |
| Start completely fresh | `docker compose down -v && docker compose up -d --build` |

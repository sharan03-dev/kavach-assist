# Kavach - threat intelligence on MongoDB, with the Kavach Assist chat (23CSE528)

**M Nagasai Girisharan - 24BTRCL114 - 5 AIML-B - 23CSE528 NoSQL Database using MongoDB**

This repository holds two things that build on each other:

1. **Final assessment: Kavach Assist** - an LLM/SLM-backed chat application on MongoDB (this section).
2. **Exercises 7 to 18** - the Kavach cyber threat intelligence platform and its labs (the rest of this file).

---

## Final assessment: Kavach Assist

A signed-in user picks a platform (Linux, Windows, macOS, Android, iOS, network devices, web/server software, cloud) and asks about
**Anticipated, Active, Evolving and Resolved** threats. The answer lists the threats with the reason for their status and gives precise
mitigation procedures. MongoDB is the operational database, the vector store and the conversation memory.

### Run it

```bash
bash setup_assist.sh            # builds the image, creates secrets in .env, starts everything, prints the admin login
# or by hand:
cp .env.example .env            # set ADMIN_PASSWORD and JWT_SECRET
docker compose up -d lab7 atlas-local ollama scanner-bot
```

Open **http://localhost:8000** (or https://localhost:8443, self-signed). Sign in, then **Kavach Assist**.
On an 8 GB machine use `bash setup_assist.sh --lean`: it stops the lab containers the chat does not need.

The language model (llama3.2, about 2 GB) downloads on the first start. Until it is ready - or on a machine too small for it - the same
answers are written by a built-in composer from the same MongoDB data, so the application always works.

### How one question is answered

```
browser --POST /api/assist/chat/stream--> FastAPI (lab7)
   1. memory      last 5 exchanges of this conversation            MongoDB  kavach_assist.messages
   2. understand  platform, status, CVE ids, scanner commands; follow-ups reuse the conversation context
   3. facts       exact queries on the threat catalog              MongoDB  kavach_assist.threats ($facet, compound + text indexes)
   4. retrieve    $vectorSearch (+ $text) over the knowledge base  MongoDB Atlas Local  kavach_assist.knowledge
   5. generate    Ollama llama3.2 writes the answer from facts + passages + history (streamed token by token)
   6. remember    question, answer, sources, tokens, latency       MongoDB  messages + metrics
```

Facts (CVE ids, dates, counts, commands) come from MongoDB; the model only writes the explanation. Scanner commands never go through the model.

### The four statuses

| Status | Rule (stored with every threat as `status_reason`) |
|---|---|
| **Active** | listed in a known-exploited catalog (CISA KEV and others) or has exploitation sightings, and it is current (sightings in the 30-day window, a recent listing, or the patch deadline not reached) |
| **Evolving** | a public proof-of-concept exists or sightings are rising week over week, exploitation not confirmed; confirmed campaigns that show a new variant |
| **Anticipated** | no exploitation or proof-of-concept yet, but discussed, severe (CVSS) or likely to be exploited (EPSS) |
| **Resolved** | a fix exists, the deadline has passed and nothing was sighted in the window; rejected records; campaigns that were taken down |

Source data: `cti_platform.vulnerabilities`, `gcve_sightings` and `reports` of Exercise 7. Code: `backend/app/assist/threats.py`.

### Mapping to the assessment instructions

| Section | Implementation |
|---|---|
| A.1 Selecting and running an LLM | Option 2 (recommended): **Ollama** runs `llama3.2` locally - service `ollama` in `docker-compose.yml`, or Ollama on the host (`OLLAMA_BASE_URL=http://host.docker.internal:11434`, faster on a Mac because it uses the GPU). Option 1 also works: set `OPENAI_API_KEY`. `backend/app/assist/llm.py` |
| A.2 Embedding and ingestion | `backend/app/assist/knowledge.py`: threat catalog, mitigation playbooks, lab guide and results -> chunks -> embeddings (all-MiniLM-L6-v2, 384 dimensions, baked into the image) -> `knowledge` with a `vectorSearch` index (cosine; filter fields kind, platforms, status). Incremental: only changed chunks are embedded again; refreshed every 30 minutes |
| A.3 FastAPI backend with RAG chat endpoint | `POST /chat` `{message, session_id}` -> `{answer, sources, ...}` and `POST /api/assist/chat/stream` (Server-Sent Events). `backend/app/assist/engine.py`, `api.py` |
| A.4 MongoDB as conversation memory | `conversations` + `messages` on the replica set with `w: majority`; survives restarts; per user; follow-ups such as "what about Linux?" or "how do I fix the first one?" |
| A.5 Chat frontend | HTML + JavaScript page inside the Kavach website: streaming, platform chips, status board, threat cards, sources, conversation list. `frontend/assets/assist.js` |
| A.6 Containerizing the full stack | `Dockerfile`, `docker-compose.yml`: MongoDB replica set (3 members), Atlas Local, the web application, Ollama, the scanner bot |
| A.7 Production hardening | see the checklist below |

### Login and the administrator

- **One administrator** - created from `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `.env`.
- **Everyone else** signs in with **Google** or creates an account with **email + password**.
- Passwords: salted scrypt hashes. Session: signed JWT in an HttpOnly, SameSite cookie; the user is re-read on every request, so
  disabling an account or "sign out everywhere" works at once. Too many failed sign-ins are locked out for 10 minutes.
- Every `/api` route of the website needs a signed-in user (except sign-in itself, `/api/health` and the internal Lab 14 webhook).
- **Admin console** (`#/admin`): users (approve, disable, reset password, sign out, delete, add), usage and latency, knowledge base,
  scanner bot, audit log, sign-up settings.

**Google sign-in** needs a client id from Google (free, about 3 minutes):
1. https://console.cloud.google.com -> create a project -> *APIs & Services* -> *OAuth consent screen* (External; add your own address as a test user).
2. *Credentials* -> *Create credentials* -> *OAuth client ID* -> *Web application*. Authorized JavaScript origins: `http://localhost` and `http://localhost:8000`.
3. Put the client id into `.env`: `GOOGLE_CLIENT_ID=1234...apps.googleusercontent.com`, then `docker compose up -d lab7`.

The server verifies Google's ID token: signature against Google's public keys, audience, issuer and `email_verified`.

### The scanner bot (the "for optimality" part)

`scanner-bot` is a background service. Every 5 minutes it scans the folder `scan_target/` (a deliberately vulnerable demo project is
copied there on the first start; mount your own project instead if you like) and the Linux system it runs on:

- dependencies with known vulnerabilities in `requirements.txt` and `package.json` - OSV.dev when online, a bundled advisory list offline, raised in priority when Kavach lists the CVE as exploited
- hard-coded secrets, secrets files readable by everyone or missing from `.gitignore`, debug mode, Dockerfile problems
- operating-system packages with a pending security update

It reports in the chat. **It changes nothing by itself**: "fix all" (or "fix 1 and 3", "fix the critical ones") shows the exact changes,
and only a **yes** queues the job. Every changed file is backed up first; "undo last fix" restores it; a new scan verifies the fix.
The bot container has no Docker socket and only sees the folder it scans. Code: `backend/app/assist/scanner.py`, `scanner_bot.py`.

### A.7 production hardening checklist

| Item | Done as |
|---|---|
| Secrets | only in `.env` (git-ignored) -> environment variables; nothing in code or compose files (Exercise 12) |
| Rate limiting | 20 questions per minute per user on `/chat` - one atomic `find_one_and_update` counter per window with a TTL index (Exercise 15); HTTP 429 with `Retry-After` |
| Encryption | HTTPS on port 8443 (self-signed certificate created at start); database traffic stays on the private Docker network - the TLS-only MongoDB server is shown in Exercise 12 |
| Monitoring | latency, status, model and token usage of every chat request in `metrics` (TTL 14 days), admin dashboard, Prometheus text format at `/metrics` (Exercise 14) |
| Health check | `GET /health` (503 when MongoDB is unreachable) - used by the container health check (Exercise 13) |
| Lifecycle | SIGTERM is passed to uvicorn; the FastAPI lifespan stops background work and closes the HTTP and MongoDB connection pools (Exercise 14) |

### Try it

```
What are the active threats for Windows?          what about Linux?          how do I fix the first one?
Which Android threats are evolving and how do I mitigate them?               anticipated iOS threats
Give me a summary of all platforms                tell me about CVE-2024-3400               is 185.220.101.1 malicious?
What did the scanner bot find?    fix the critical ones    yes    undo last fix
What did Lab 13 prove?    explain write concern    are all labs working?
```

API from a terminal:

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login -H 'content-type: application/json' \
  -d '{"email":"admin@kavach.local","password":"<ADMIN_PASSWORD>"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
curl -s -X POST localhost:8000/chat -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"message":"active threats for linux","session_id":"demo"}'
```

Tests: `docker exec lab7 python -m unittest tests.test_assist -v` (status rules, question understanding, passwords, scanner fix and rollback).

### Files of the final assessment

`backend/app/assist/` - `settings.py` `store.py` `auth.py` `threats.py` `knowledge.py` `llm.py` `engine.py` `scanner.py` `scanner_bot.py`
`metrics.py` `ratelimit.py` `api.py`, `knowledge_base/` (playbooks, lab guide, FAQ), `demo_template/` (the demo project for the bot).
`frontend/assets/assist.js`, `assist.css`. `tests/test_assist.py`. `setup_assist.sh`, `push_to_github.sh`, `labs/final/make_submission.py`.

---

# Exercises 7 to 18

# Exercises 16, 17 and 18 - Cassandra + Neo4j, Vector search + RAG, Comparative capstone
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

One project, one Docker image, one website for **Labs 7 to 18**. The mentor's rule still holds - starting a lab starts every lab below it:
```
Lab 18 needs Lab 17 needs Lab 16 needs Lab 15 needs ... needs Lab 8 needs Lab 7
docker compose up -d lab18      ->  7 -> 8 -> 9 -> 10 -> 11 -> 12 -> 13 -> 14 -> 15 -> 16 -> 17 -> 18
```
| Lab | What it adds | Applied to (mentor's rule) | Website |
|---|---|---|---|
| **16** Cassandra + Neo4j | 16.1 the same university dataset in MongoDB / Cassandra / Neo4j (3 queries each, identical answers, latency); activity: hybrid architecture (operational / audit log / prerequisite chains); 16.2 `FraudQueryService` (pooling, circuit breaker, 12 mock unit tests, integration test, latency) | Lab 10 billing ledger -> fraud check | `#/lab16` |
| **17** Vector search + RAG | MongoDB Atlas Local (mongod + mongot) `$vectorSearch`; 17.1 RAG pipeline (100-word chunks, top 3, generator with citations); activity: conversation memory; 17.2 HNSW vs IVF (5,000 x 384-d, latency / recall / build time, curve, recommendation) | Lab 7 reports -> *Ask Kavach* | `#/lab17` |
| **18** Capstone | 100,000 threats in all three databases, the mentor's schemas, 4 benchmarks x 10 runs (median), false-positive detector on all three, Louvain communities x MongoDB, scalability 1M/10M/100M, comparative report | verdicts -> Lab 7 (`fp_verdicts`) | `#/lab18` |

```bash
cp .env.example .env                    # optional; LAB13_K8S=off is recommended on an 8-10 GB machine
docker compose up -d --build lab18      # everything (first start 30-60 min: every lab runs once, in order)
open http://localhost:8000/#/labs       # follow the chain live; then #/lab16, #/lab17, #/lab18
docker compose exec lab16 lab16 all     # rerun a lab + its PDF (lab17 all, lab18 all)
./publish.sh <dockerhub-user>           # push <user>/nosql-lab16, -lab17, -lab18 (amd64 + arm64), run from the image, write the 3 PDFs
```
New containers: `cassandra` (:9042), `neo4j` (:7474 browser, :7687 bolt; neo4j / Lab16-neo4j!), `atlas-local` (:27060). Memory: give Docker Desktop
at least 10 GB (Settings -> Resources) for the whole chain, or set `LAB13_K8S=off`. Details: `labs/lab16/README.md`, `labs/lab17/README.md`, `labs/lab18/README.md`.

---

# Exercise 15 - Designing and Architecting a MongoDB-Backed Product as a Service
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

**Lab 15** adds **TaskTracker**, a multi-tenant SaaS product, on top of Labs 7-14 (`Lab 15 needs Lab 14 needs ... Lab 7`):

| Part | What it does | Where |
|---|---|---|
| **15.1 Multi-tenant API** | FastAPI + JWT tenants, CRUD with tenant_id on every query (`scoped()`), `$jsonSchema`, compound indexes, rate limiting with a MongoDB counter (findAndModify + TTL) | website *SaaS lab 15* -> TaskTracker app; `/tasktracker/docs` |
| **Activity: RBAC** | admins manage all tasks of their tenant, members only their own; re-checked on every request | tab *RBAC* |
| **15.2 Migration** | one shared collection -> collection per tenant (`$match + $out`), leakage validation, list/create/analytics benchmark at 3 scales, one-tenant restore, schema evolution, recommendation | tab *Migration* |
| **15 on 14..7** | runs on the Lab 13 production set as least-privilege `tasktracker_app`, signup in a Lab 10 transaction, Lab 14 backups | tab *Design + applied* |

```bash
docker compose up -d --build lab15        # 7 -> 8 -> ... -> 14 -> 15
open http://localhost:8000/#/lab15        # sign in as a demo user (password Lab15-demo!)
docker compose exec lab15 lab15 all       # every Lab 15 task + results/Exercise-15_Report.pdf
./publish.sh sharan10860                  # push sharan10860/nosql-lab15, run from the image, write the PDF
```
Details and measured findings: **`labs/lab15/README.md`**. Exercise 14 is described below.

---

# Exercise 14 - Managing Database Services in Production
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

One project that contains **Labs 7 to 14** and a website for all of them (Kavach CTI). **Lab 14** runs the database the way an
operations team does - on the **Lab 13 production replica set**, and applied to the **Lab 10 billing ledger** (mentor's rule):

| Part | What it does | Where |
|---|---|---|
| **14.1 Monitoring dashboard** | `monitor_dashboard.py`: connections, opcounters, WiredTiger cache, replica set status of every member (as least-privilege `monitor_app`), table to `results/metrics.log` every 30 s, WARNING below 80 % cache hit ratio / above 10 s lag | website tab *Dashboard*: live table, KPI tiles, 60-min charts, log |
| **Activity: webhook** | `requests.post(webhook_url, json=alert_payload)` in try/except, Slack + Discord formats, FIRING/RESOLVED only; two **drills** make the thresholds fire for real (fsyncLock lag drill, cold-cache drill) | tab *Alerts + webhook*: built-in `#mongodb-alerts` channel |
| **14.2 Backup + verification** | `backup_verify.py --uri --out`: timestamped mongodump, hourly with `schedule`, 7-day retention, 1,000-doc restore verification, **point-in-time restore** from the oplog, 1k/10k/100k benchmark, RPO/RTO | tabs *Backups*, *Restore verification*, *Point-in-time*, *Benchmark* |
| **14 on 13 + 10** | the billing ledger is backed up, customers keep paying, an attacker zeroes every wallet and deletes the payments; recovered to the second before the attack (digest + invariants) | tab *Billing recovery* |

```bash
docker compose up -d --build lab14        # 7 -> 8 -> 9 -> 10 -> 11 -> 12 -> 13 -> 14 (Lab 14 in full + ops daemon)
open http://localhost:8000/#/lab14        # Windows: start http://localhost:8000/#/lab14
docker compose exec lab14 lab14 all       # every Lab 14 task + results/Exercise-14_Report.pdf
./publish.sh <dockerhub-user>             # push sharan10860/nosql-lab14 (amd64 + arm64), run from the image, write the PDF
```
Details, users, drills and the measured notes on correctness: **`labs/lab14/README.md`**. The chain is now
`Lab 14 needs Lab 13 needs ... needs Lab 7`; the Lab control centre shows all eight labs and an *Operations (Lab 14)* panel.

---

# Exercise 13 - Deploying MongoDB with Containers and the Cloud
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

One project that contains **Labs 7, 8, 9, 10, 11, 12 and 13** and a website for all of them (Kavach CTI).
Lab 13 deploys MongoDB as in production: a **3-member replica set with keyFile authentication in Docker Compose**, a
**Prometheus exporter sidecar**, a **Kubernetes StatefulSet** (k3s inside Docker) and an **Atlas** cluster in Terraform -
and is **applied to Lab 10**: the classifier billing keeps every rupee right through step-downs, crashes and deleted pods.
Lab 11 runs on a real **sharded cluster**; Lab 12 runs on a **hardened MongoDB** (`mongo-secure`: authentication with a
keyFile, SCRAM-SHA-256 only, `--tlsMode requireTLS`, client certificates required) and is **applied to Lab 7**.

## The mentor's rule: starting a lab starts the labs below it
```
Lab 13  needs  Lab 12  needs  Lab 11  needs  Lab 10  needs  Lab 9  needs  Lab 8  needs  Lab 7
```
| You start | What runs, in this order |
|---|---|
| Lab 7  | 7 |
| Lab 8  | 7 -> 8 |
| Lab 9  | 7 -> 8 -> 9 |
| Lab 10 | 7 -> 8 -> 9 -> 10 |
| Lab 11 | 7 -> 8 -> 9 -> 10 -> 11 (and the sharded cluster) |
| Lab 12 | 7 -> 8 -> 9 -> 10 -> 11 -> 12 (and mongo-secure) |
| Lab 13 | 7 -> 8 -> 9 -> 10 -> 11 -> 12 -> 13 (and prod-mongo-1/2/3, exporter sidecar, Prometheus, k3s) |

It works in three places, all using the same code (`orchestrator/labctl.py`):
1. **Docker Compose** - `docker compose up -d lab13` (each lab is a container that `depends_on` the one below it).
2. **Command line** - `./labctl 13` (or `./labctl 8 --full`).
3. **Website** - open **Lab control centre**, press *Start* on any lab.

The labs below the one you start run in **quick** mode (a few seconds: proves they work). The lab you start runs in
**full** mode when it is Lab 10, 11, 12 or 13 (always) or when you tick *full* / use `--full`.

## Mentor's update: each lab is applied to the one below it
| Applied | What it means in this project |
|---|---|
| **Lab 10 on Lab 7** | The **Classifier** page now has a paywall: every user gets **3 free classifications**, then it costs **Rs 100 per month** (30 days). Sign in with name + e-mail, add money to the wallet (simulated UPI), subscribe. Every use, top-up and subscription is ONE multi-document ACID transaction (`backend/app/billing.py`, database `cti_billing`). The use is only counted if the classification succeeded; 10 simultaneous requests get exactly 3 free trials; a crash after the wallet debit rolls back; a double click on Pay charges once. |
| **Lab 11 on Lab 10** | When Lab 11 starts, the billing data **moves to the sharded cluster** (verified copy, then the website switches over): accounts `{_id: 'hashed'}`, usage `{user_id: 'hashed', at: 1}`, payments `{user_id: 'hashed'}`, revenue in 8 buckets. A 300-user load test runs on it; mongos reports which transactions committed on one shard and which needed a **two-phase commit** across shards. The Lab 10 ACID checks are repeated across shards. |

| **Lab 12 on Lab 7** | The Kavach platform gets a hardened home: its data is copied to `mongo-secure` (verified), and every part of the platform gets its own least-privilege account over TLS - the **website** reads as `analyst_app` (find only, customers through a masked view), the **5-minute auto-updater** writes as `ingest_app` (no delete, no drop; after every cycle it pushes the newest reports to the secure server), the **classifier billing** uses `finance_app` (allowed only from private networks). Schema validation (IOC types, wallet >= 0, max 3 free trials), Client-Side Field Level Encryption of UPI ids (root sees ciphertext), audit trail (profiler + change stream), and the mentor's threat model with live evidence. |

| **Lab 13 on Lab 10** | The classifier billing is copied (verified) to the **production replica set** `prod-mongo-1/2/3` and used there by the least-privilege user `billing_app`. The Lab 10 ACID checks are repeated, then 24 customers sign in, use their 3 free classifications, are refused the 4th, top up Rs 200 and subscribe for Rs 100 **while the primary steps down, and again while it is killed (kill -9)**. Every journey must end with wallet Rs 100, 3 free + 2 plan uses, 2 payments; money and revenue invariants are checked. In Kubernetes a client pod moves money A -> B in transactions while the PRIMARY pod is deleted; A + B stays 2,000. |

Where to see it: **Containers & cloud lab 13** page (tabs Compose replica set, Monitoring, Kubernetes, Billing on prod, Compose vs Kubernetes, Atlas + Terraform, Connect), **Security lab 12** page (tabs Threat model, Auth + RBAC, Redaction, TLS, *Kavach on the secure server*, Connect), and **Classifier** page (the paywall), **Transactions lab 10** page (panel "Applied to the classifier", button *Run the ACID checks*),
**Sharding lab 11** page (tab "Billing on shards"), and a section in each report. Command line:
`docker compose exec lab10 lab10 billing` and `docker compose exec lab11 lab11 billing`.
Note: after Lab 11 has moved the billing data, the classifier needs the sharded cluster - start with `docker compose up -d lab11`.

## Run it (needs only Docker Desktop)
```bash
cd M_NAGASAI_GIRISHARAN_24BTRCL114_LAB13
cp .env.example .env            # optional: put your Docker Hub user in it
docker compose up -d --build lab13      # everything of Labs 7-12 + prod rs0 (keyFile) + exporter + Prometheus + k3s
open http://localhost:8000/#/lab13      # Windows: start http://localhost:8000/#/lab13
```
First start: Lab 7 downloads the threat data (1-3 minutes; if there is no internet it loads sample data), then Labs
8, 9, 10, 11 (quick) and Lab 12 (full, about 1 minute). Watch it at **http://localhost:8000/#/labs** or with
`docker compose logs -f lab13`. Memory: about 16 mongod processes + mongos + Kubernetes; give Docker Desktop **at least 8 GB** (Settings -> Resources).
Lab 13 runs in full after Lab 12 (10-15 minutes, most of it Kubernetes starting and pulling mongo:7.0 the first time).
On a small machine: `LAB13_K8S=off docker compose up -d lab13` skips Lab 13.2.

Compass: Labs 7-10 `mongodb://localhost:27017/?directConnection=true` (databases `cti_platform`, `labdb`, `lab9`,
`lab10_bank`, `lab10_shop`); Lab 11 `mongodb://localhost:27030` (mongos: `lab11`, `lab11_shop`, `lab11_reshard`). Lab 12 `localhost:27040` needs TLS,
the CA, a client certificate and a password - easiest: `docker compose exec lab12 lab12 shell analyst_app`
(Compass: see the *Connect* tab of the Security lab 12 page).

## Website pages
| Page | What it shows |
|---|---|
| Lab control centre | the 7 -> 8 -> 9 -> 10 -> 11 -> 12 -> 13 chain, status of every step, live output, auto-updater, replica set and sharded cluster health |
| Overview, Threat feed, Vulnerabilities, IP intelligence, Sources | Lab 7 platform (unchanged) |
| Classifier | Lab 7 classifier with the Lab 10 paywall: 3 free classifications, then Rs 100 per month (account bar, wallet, subscription, usage history) |
| Schema lab 7.1, Cache lab 7.2, Replica set lab 8, Write concern lab 9, Transactions lab 10 | as in Exercise 10 |
| **Sharding lab 11** | *Cluster*: live topology (mongos, config servers, shards with primary/secondary), chunks and documents per shard. *Shard keys (11.1)*: measured decision matrix with adjustable weights, where new orders went, query targeting. *Hotspot detection (11.2)*: insert 1,000 more and watch which shard receives them, explain() presets, collStats, getShardDistribution. *Resharding*: run `reshardCollection` live and follow its phases, steps and limitations. *Billing on shards*: the classifier billing on the shards, commit types, load test |

| **Security lab 12** | *Threat model*: the mentor's diagram (attack surface -> controls -> CIA triad), each control green only when verified on the server. *Auth + RBAC (12.1)*: the classroom snippet and MongoDB's answer, roles, users, the user x operation matrix (run it again live). *Redaction*: HR vs staff documents, tests, the exclusion-view leak. *TLS (12.2)*: six connection attempts, the handshake, the certificate chain. *Kavach (12 on 7)*: threat feed read as analyst_app, masked customers, "try it as a user" console, permission matrix, schema validation, log-in hardening, CSFLE, audit trail. *Connect*: mongosh/Compass commands |

## Lab 13 on the command line
```bash
docker compose exec lab13 lab13 all          # 13.1 + activity + 13 on 10 + 13.2 + cloud + results/Exercise-13_Report.pdf
docker compose exec lab13 lab13 13.1         # initiate + verify the keyFile replica set, rogue member, stepDown + crash
docker compose exec lab13 lab13 crash        # kill -9 the PRIMARY while PyMongo reads/writes (numbers printed)
docker compose exec lab13 lab13 activity     # exporter sidecar + Prometheus + sidecar restart test
docker compose exec lab13 lab13 billing      # Lab 10 billing through a step-down and a crash
docker compose exec lab13 lab13 13.2         # StatefulSet on k3s, delete mongo-2, delete the PRIMARY during transactions
docker compose exec lab13 lab13 pods         # kubectl get pods,pvc,svc -n lab13
docker compose exec lab13 lab13 shell admin  # mongosh to the production replica set
```
Source: `backend/app/lab13.py` (logic) and `labs/lab13/` (compose, k8s, monitoring, terraform, scripts, report).
Details, and how to run the compose file and the manifests by hand: `labs/lab13/README.md`.

## Notes on correctness - Lab 13 (observed on MongoDB 7.0.34)
- The reference compose health check logs in as `admin` / `AdminPass123!`, a user that is never created: measured exit code 1
  "Authentication failed" -> the container would never be healthy. Here: a password-less `ping`.
- A host keyfile with `chmod 400` (reference `./keyfile` bind mount) belongs to the host user; mongod runs as uid 999 and cannot
  read it on Linux. The keyfile is made in a volume with `chown 999:999` + `chmod 400`.
- `--keyFile` already enables authorization (`--auth` is redundant); `rs.initiate()` and the first user use the localhost exception.
- The solution's InitContainer that *generates* the keyfile per pod gives every pod a different key: `rs.initiate` fails with
  NodeNotFound "Authentication failed" (measured). The key is generated once into a Secret; the InitContainer only installs it.
- `--wiredTigerCacheSizeGB` must be >= 0.25 (0.1 is refused at start-up).
- Without an init process mongod is PID 1 and a SIGKILL from inside the container is ignored: members run with `init: true`.
- rs.stepDown(): new primary in < 1 s, no error reached the app (retryable writes). kill -9: ~10-12 s (electionTimeoutMillis).
  No write acknowledged with `w: majority` was lost in either case.
- Lab 10's transaction retry budget (~3 s) is shorter than a crash election (~11 s): recovery needs an application-level retry
  that first checks whether the operation had committed (idempotency key / operation id).
- On a SECONDARY, inserts are counted in `opcountersRepl`, not `opcounters` - the dashboards add both.
- percona mongodb_exporter 0.40 `--collect-all` returned only `mongodb_up` against MongoDB 7.0; 0.47.2 with the
  diagnosticdata + replicasetstatus collectors works without errors.
- Atlas M0 needs `provider_name = "TENANT"`; `terraform validate` cannot catch it (API-side rule). The reference output
  `cluster_uri` repeats `mongodb+srv://`.

## Lab 12 on the command line
```bash
docker compose exec lab12 lab12 all          # 12.1 + activity + 12.2 + Lab 12 on Lab 7 + results/Exercise-12_Report.pdf
docker compose exec lab12 lab12 12.1         # admin + least-privilege users, verification matrix
docker compose exec lab12 lab12 redaction    # view employees_public without salary
docker compose exec lab12 lab12 12.2         # TLS tests + certificate chain
docker compose exec lab12 lab12 kavach       # Lab 12 applied to Lab 7
docker compose exec lab12 lab12 shell analyst_app   # mongosh over TLS as a least-privilege user (try db.reports.deleteOne({}))
docker compose exec lab12 lab12 plain        # a connection WITHOUT TLS is refused
docker compose exec lab12 lab12 certs        # the certificate chain
```
Source: `backend/app/lab12.py` (logic) and `labs/lab12/` (certificates, scripts, mongosh files, report). Details: `labs/lab12/README.md`.

## Lab 11 on the command line
```bash
docker compose exec lab11 lab11 all        # 11.1 + 11.2 + resharding + results/Exercise-11_Report.pdf
docker compose exec lab11 lab11 11.1       # only Lab 11.1 (shard key decision matrix)
docker compose exec lab11 lab11 11.2       # only Lab 11.2 (hotspot_data vs even_data)
docker compose exec lab11 lab11 reshard    # only the resharding activity
docker compose exec lab11 lab11 status     # like sh.status()
mongosh mongodb://localhost:27030          # then: sh.status(), use lab11, db.hotspot_data.getShardDistribution()
```
Source: `backend/app/lab11.py` (logic) and `labs/lab11/` (scripts, cluster setup, report). Details: `labs/lab11/README.md`.

## Submission (Docker Hub + PDF)
```bash
./publish.sh <your-dockerhub-username>
```
Builds the image (Intel + Apple Silicon), pushes it to `https://hub.docker.com/r/<user>/nosql-lab13`, starts
everything **from the pushed image**, runs Lab 13 and writes `results/Exercise-13_Report.pdf` (the Docker URL is on page 1).
Submit that PDF in Classroom. For grading, demonstrate from the uploaded image only:
```bash
docker run --rm <user>/nosql-lab13 compose > docker-compose.yml
docker compose up -d lab13
```

## Full mode of the earlier labs
```bash
./labctl 8 --full     # adds failover (stepDown) and the priority election activity
./labctl 9 --full     # adds journaling activity, the network partition (9.2) and the Lab 9 report
./labctl 7 --full     # adds the Lab 7.2 working-set test (100,000 documents)
docker compose exec lab10 lab10 all   # full Lab 10 + its report (Lab 10 runs quick when it is a dependency of Lab 11)
```
Manual Lab 8 arbiter activity: see `labs/lab8/README.md` (`docker compose` file for the arbiter is in `labs/lab8/Activity_8.1_TryItYourself_Arbiter/`).

## Notes on correctness - Lab 12 (observed on MongoDB 7.0.34)
- The classroom `createRole` with `actions: ["find", "aggregate"]` is **refused** (BadValue "Unrecognized action: aggregate").
  An aggregation is authorised by `find`. The solution's "delete" is not an action either: it is `remove`.
- `db.createRole()` creates the role in the current database; the snippet's user references it with `db: "admin"`, so it
  must be run after `use admin` (the lab creates every role in admin).
- With `--tlsCAFile` and without `--tlsAllowConnectionsWithoutCertificates`, mongod **requires client certificates**:
  TLS without one is closed during the handshake. The legacy `mongo` shell no longer exists (removed in 6.0): use mongosh.
- The server certificate needs a **subjectAltName** - PyMongo checks the host name against it (a cert without
  `DNS:localhost` fails with "Hostname mismatch").
- mongod connects to **itself** over TLS through its replica-set member name (index builds wait on it): the member host
  (`mongo-secure`) must be in the server certificate's SAN, or `createIndexes` hangs (observed while testing).
- `--keyFile` turns authorization on. The first user is created through the localhost exception; on first boot the server
  is bound to 127.0.0.1 **and** its own host name, otherwise `rs.initiate` fails with "No host ... maps to this node".
- A redaction view written as an exclusion (`salary: 0`) leaks fields added later (measured); an allow-list does not.
- `userAdminAnyDatabase` reads no data but can create a root user - it is as sensitive as root.
- Community edition: no `auditLog` and no encrypted storage engine. The lab uses the profiler (records the user of each
  operation; refused attempts are not recorded), change streams with pre-images, and Client-Side Field Level Encryption.
- When a user is dropped and re-created, MongoDB logs out its open connections; the website re-checks cached clients.

## Notes on correctness - Lab 11 (observed on MongoDB 7.0.34)
- A ranged shard key on an empty collection starts as **one chunk on the primary shard**, so the classroom result
  "9,500 on shard A and 500 on shard B" needs the older range to be split off and migrated first. The lab does that
  split + `moveChunk` explicitly (what the balancer would do), then shows every new insert going to one shard.
- The balancer is stopped while measuring, so the counts show routing; it is switched on again afterwards.
- A range query on a hashed shard key is broadcast and does a COLLSCAN on every shard (explain() E2).
- `reshardCollection` needs enough distinct values in its sample: the default 90 initial chunks failed on the skewed
  order data, so the lab passes `numInitialChunks: 32`; the 5-minute minimum duration is lowered to 5 s for the demo.

## Notes on correctness - Lab 10 (differences from the classroom sample text)
- `update_one` with a filter such as `balance >= 100` does **not** fail when nothing matches, so the sample
  `multi_document_transaction.py` would still credit the other account. This project checks `matched_count` and raises, which aborts the transaction (the website shows the bug live).
- `pymongo.errors.WriteConflict` does not exist. A write conflict is an `OperationFailure` (code 112) with the label `TransientTransactionError`; the retry loop uses the label.
- MongoDB has no deadlock waiting between transactions - the second writer is aborted immediately. "Deadlock handling" = retry with exponential back-off.
- Transactions need a replica set; that is why Lab 10 (and 8, 9) run on the 3-node set, not a standalone `mongod`.

## Folder map
`backend/` FastAPI + Lab 7 logic + `lab10.py` + `lab11.py` + `lab12.py` + `lab13.py` · `frontend/` website · `orchestrator/` the dependency chain · `labs/lab7_*`, `labs/lab8`, `labs/lab9`, `labs/lab10`, `labs/lab11`, `labs/lab12`, `labs/lab13` · `docker-compose*.yml`, `Dockerfile`, `publish.sh`, `labctl` · `docs/` older READMEs and the Exercise 8 report · `results/` outputs (PDF, JSON, logs).

Original Lab 7 README (macOS without Docker): `docs/README_lab7_original.md`.

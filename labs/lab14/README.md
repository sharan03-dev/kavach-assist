# Lab 14 - Managing Database Services in Production

Lab 14 operates the **Lab 13 production replica set** (prod-mongo-1/2/3, keyFile auth) and, following the mentor's rule,
protects the layers below it: the **Lab 10 billing ledger** (moved onto prod rs0 by Lab 13) and the **Kavach platform** (Lab 7).

| File | What it is |
|---|---|
| `monitor_dashboard.py` | **Lab 14.1** standalone script: connection count, opcounters, WiredTiger cache, replica set status of *every* member; tabulate table appended to `metrics.log` every 30 s; WARNING below 80 % cache hit ratio / above 10 s lag. **Activity**: `requests.post(webhook_url, json=alert_payload)` in try/except, Slack *and* Discord payloads, FIRING/RESOLVED only. |
| `backup_verify.py` | **Lab 14.2** standalone script (`--uri URI --out DIR`): mongodump to `backup-YYYYmmddTHHMMSSZ`, return code in `manifest.json`, `schedule.every().hour`, 7-day retention with `shutil.rmtree`, 1,000-document restore verification with an assertion loop, point-in-time restore from the oplog, 1k/10k/100k benchmark, RPO/RTO. |
| `ops_daemon.py` | runs in the `lab14` container: `schedule.every(30).seconds` monitor + `schedule.every().hour` backups (billing ledger + Kavach, gzip, 7-day retention). |
| `run_lab14.py` | runs everything, writes `results/lab14_results.json`, `console_14.txt`, `metrics.log`, the PDF. |
| `make_report.py` | `results/Exercise-14_Report.pdf` (Docker URL on page 1). |
| `lab14` | command menu (`docker compose exec lab14 lab14 ...`). |
| `../../backend/app/lab14.py`, `lab14_api.py` | drills, users, billing recovery, website API. |

## Run
```bash
docker compose up -d --build lab14            # 7 -> 8 -> ... -> 13 -> 14; Lab 14 runs in full and starts the ops daemon
open http://localhost:8000/#/lab14            # Operations lab 14 page
docker compose exec lab14 lab14 all           # everything again + the PDF
docker compose exec lab14 lab14 dashboard     # one table now          lab14 monitor   (every 30 s, foreground)
docker compose exec lab14 lab14 lag           # replication-lag drill   lab14 cache    (cold-cache drill)
docker compose exec lab14 lab14 verify|pitr|benchmark
docker compose exec lab14 lab14 log 60        # tail results/metrics.log
```
The scripts work against any MongoDB:
```bash
python labs/lab14/monitor_dashboard.py --uri mongodb://localhost:27017 --interval 30 --log metrics.log --webhook https://hooks.slack.com/services/...
python labs/lab14/backup_verify.py --uri mongodb://localhost:27017 --out ./backups all
```
Real Slack/Discord: put `LAB14_WEBHOOK_URL=<url>[,<url>]` in `.env`, then `docker compose up -d lab14`. Without it, alerts go to the
website's built-in `#mongodb-alerts` channel (answers like the real services: Slack 200 "ok", Discord 204).

## Users (least privilege, measured on the server)
| User | Roles | Can | Cannot |
|---|---|---|---|
| monitor_app | clusterMonitor, read@local | serverStatus, replSetGetStatus | read any business data, write |
| backup_app | backup, restore | read everything, insert (restore) | update, delete - **but createUser is allowed by `restore`** |
| admin | root | the oplog replay step of point-in-time recovery only | - |

## The drills (thresholds fire for real)
* **Replication lag > 10 s**: `db.fsyncLock()` on a secondary that no other member syncs from, while 5 writes/s go on with w: majority; `fsyncUnlock()` -> RESOLVED.
* **Cache hit ratio < 80 %**: restart a secondary (never prod-mongo-1: the exporter sidecar shares its network), read 60,000 documents from it (cold, ~20 %), read again (warm, 100 %) -> RESOLVED.

## Notes on correctness (measured on MongoDB 7.0.34, database tools 100.13)
- serverStatus has **no "bytes read from cache"** counter (solution text). Hit ratio = 1 - `pages read into cache` / `pages requested from the cache`, per interval; the since-start-up ratio hides a cold cache (99 % while the interval showed 21 %).
- A SECONDARY counts replicated writes in `opcountersRepl`, not `opcounters`.
- **Chained replication**: locking the secondary that the other secondary syncs from froze both - w: majority writes stopped (0 acknowledged). Lock a leaf member.
- `deleteMany()` is logged as **batched applyOps entries in admin.$cmd** (10 deletes each): search `o.applyOps.ns` to find the incident.
- `mongorestore --oplogReplay` as backup + restore applies inserts and is **refused at the first update** (applyOps Unauthorized) - the collection is left half restored; only the checksum shows it. The replay runs as admin.
- Transactions (Lab 10 billing) replay correctly - one applyOps entry per transaction.
- During the billing attack (wallets 0, payments deleted) `money_conserved` **stays true** (0 - 0 = 0): only `revenue = subscriptions` and the digest catch it.
- Passwords go to mongodump/mongorestore in a mode-600 `--config` file, not on the command line (`ps`).
- Webhook URLs are redacted in every log (the token is the secret); alerts are posted on change only.
- The image copies `mongodump`/`mongorestore` from the official `mongo:7.0` image (build fails early if they don't run).
- Disk: hourly gzip backups kept 7 days = 168 copies per target; change `LAB14_BACKUP_EVERY_MIN` on a small disk.

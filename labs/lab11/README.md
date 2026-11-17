# Exercise 11 - Sharding & Horizontal Scalability

Everything here runs on a **real sharded cluster** (started by `docker compose up -d lab11`):

```
application -> mongos (localhost:27030) -> config server replica set  cfgrs  = cfg1
                                        -> shardA = shard-a1 (primary) + shard-a2 (secondary)
                                        -> shardB = shard-b1 (primary) + shard-b2 (secondary)
```

| File | What it does |
|---|---|
| `setup/clusterctl.py` | `init`: rs.initiate cfgrs/shardA/shardB, wait for mongos, `sh.addShard` x2 (run by the `shard-init` container). `status`: like `sh.status()` |
| `run_lab11.py` | runs 11.1 + 11.2 + the resharding activity, saves `results/console_11.txt`, `results/lab11_results.json`, builds the PDF with `--report` |
| `lab_01_shard_key_eval.py` | Lab 11.1 only (same file name as the course repository) |
| `lab_02_shard_key_hotspot.py` | Lab 11.2 only |
| `activity_11_1_resharding.py` | Activity 11.1 only (live `reshardCollection`) |
| `make_report.py` | `results/Exercise-11_Report.pdf` (Docker Hub URL on page 1) |
| `lab11` | command menu inside the container: `lab11 11.1 / 11.2 / reshard / all / report URL / status` |
| `../../backend/app/lab11.py` | all the logic, shared by the scripts and the website page `#/lab11` |

## Lab 11.1 - Evaluate shard key choices
24,000 e-commerce orders (2,696 customers; 40 % sign up during the year; a few customers order a lot) are loaded into
**one sharded collection per candidate key** - `order_id`, `customer_id`, `order_date`, hashed `customer_id`, plus two
extra rows from the course material (`category`, and `{customer_id: 'hashed', order_date: 1}`). The first 20 % are split
at the median and half migrated to shardB (the balancer's job, done explicitly), then the rest arrive in time order with
the balancer stopped. Measured: distinct values and the share of the most common value (cardinality), where the new
orders went (distribution), the last batch (hot-spot), and `explain()` of four weighted queries (targeting).
Result: **hashed customer_id** is recommended; `order_date` sends 100 % of new orders to one shard.

## Lab 11.2 - Shard key hotspot detection
`db.hotspot_data` `{ts: 1}` vs `db.even_data` `{user_id: 'hashed'}`, 10,000 documents each, `collStats` per shard,
`getShardDistribution()`, `serverStatus().opcounters` of each shard primary and `explain()` of five range/equality
queries. Result: 9,500 / 500 vs about 5,000 / 5,000; the ts range targets one shard, a range on the hashed key goes to
both shards and does a COLLSCAN on each.

## Activity 11.1 - Resharding (MongoDB 5.0+)
The bad `{order_date: 1}` collection is resharded to `{customer_id: 'hashed'}` **live** while a writer inserts an order
every 20 ms; the phases come from `$currentOp` / `config.reshardingOperations`. For the demo the 5-minute minimum
(`reshardingMinimumOperationDurationMillis`) is lowered to 5 s and restored afterwards. Steps and limitations are in
`backend/app/lab11.py` (`RESHARD_STEPS`, `RESHARD_LIMITS`) and in the report.

## Lab 11 applied to Lab 10 - the classifier billing on the shards (mentor's update)
`lab11 billing` (part of `lab11 all`): moves `cti_billing` (the Lab 10 paywall of the Lab 7 classifier: 3 free uses, then
Rs 100/month) from rs0 to the shards with a verified copy, runs a 300-user load test, and repeats the Lab 10 ACID
experiments across shards. mongos `serverStatus().transactions.commitTypes` shows single-shard commits vs two-phase
commits. Design points found on the cluster: a unique index on the payment idempotency key alone is refused in a sharded
collection (it must contain the shard key, so it is `{user_id, idem}`), and revenue is split into 8 bucket documents to
avoid one hot document.

## Things MongoDB 7.0 does differently from the classroom text (all observed on the cluster)
- A ranged key on an empty collection is **one chunk on one shard**; the "500 on shard B" only appears after older data
  is split off and migrated. This lab does that explicitly so the run is repeatable.
- A hashed key on an empty collection is pre-split by MongoDB into chunks on every shard.
- A range query on a hashed key is broadcast **and** each shard scans the whole collection (the hashed index cannot
  serve a range).
- `reshardCollection` with default settings failed on the skewed order data ("can only make 89 chunks" of 90);
  the lab passes `numInitialChunks: 32` (8 chunks gave a 70/30 split because the boundaries come from too few samples).
- mongos does not listen until the config server replica set is initiated, so `shard-init` initiates first and waits
  for mongos afterwards.

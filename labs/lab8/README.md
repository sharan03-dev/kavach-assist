# Exercise 8 – MongoDB Replica Sets, Failover and Elections

| Directory | Activity | MongoDB file | Python script |
|---|---|---|---|
| `Activity_8.1_Replica_Set` | Lab 8.1 – deploy & verify | `docker-compose.yml`, `init_replica_set.js` | `verify_replication.py` |
| `Activity_8.1_TryItYourself_Arbiter` | Add an arbiter | `docker-compose.arbiter.yml`, `add_arbiter.js`, `remove_arbiter.js` | `arbiter_analysis.py` |
| `Activity_8.2_Failover` | Lab 8.2 – failover | `step_down.js` | `failover_monitor.py` |
| `Activity_8.2_TryItYourself_Priority` | Predict election by priority | `set_priorities.js` | `predict_election.py` |

## One-time setup

1. `pip install -r requirements.txt`
2. Let your machine resolve the member names (the driver connects to the hosts listed in the replica-set config):
   - macOS / Linux: `sudo sh -c 'echo "127.0.0.1 mongo1 mongo2 mongo3 mongo-arb" >> /etc/hosts'`
   - Windows (Notepad as Administrator): add `127.0.0.1 mongo1 mongo2 mongo3 mongo-arb` to `C:\Windows\System32\drivers\etc\hosts`

## Lab 8.1
```bash
cd Activity_8.1_Replica_Set
docker compose up -d
docker exec -i mongo1 mongosh --port 27017 --quiet < init_replica_set.js
python verify_replication.py
```

## Lab 8.1 – Try it yourself (arbiter)
```bash
cd ../Activity_8.1_TryItYourself_Arbiter
docker compose -f docker-compose.arbiter.yml up -d
docker exec -i mongo1 mongosh "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0" --quiet < add_arbiter.js
python arbiter_analysis.py
# restore the 3-node set before Lab 8.2
docker exec -i mongo1 mongosh "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0" --quiet < remove_arbiter.js
docker compose -f docker-compose.arbiter.yml down
```

## Lab 8.2
```bash
cd ../Activity_8.2_Failover
docker exec -i mongo1 mongosh "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0" --quiet < step_down.js
# terminal 1
python failover_monitor.py
# terminal 2 – force another stepDown and watch terminal 1 reconnect
docker exec -i mongo1 mongosh "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0" --quiet --eval "rs.stepDown(60)"
# (or let the script do it: python failover_monitor.py --stepdown-after 10 --duration 40)
```

## Lab 8.2 – Try it yourself (priorities)
```bash
cd ../Activity_8.2_TryItYourself_Priority
docker exec -i mongo1 mongosh "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0" --quiet < set_priorities.js
python predict_election.py --watch-takeover
```

## Clean up
```bash
cd ../Activity_8.1_Replica_Set && docker compose down -v
```

## Troubleshooting
- `ServerSelectionTimeoutError: mongo1:27017 ...` → the hosts-file entry is missing.
- `AlreadyInitialized` → harmless; the set was initialised on an earlier run.
- Starting over completely: `docker compose down -v` deletes the volumes.

# Exercise 13 - Deploying MongoDB with Containers and the Cloud

`docker compose up -d lab13` starts Labs 7 -> 12 and then Lab 13 with its own services:

| Service | What it is |
|---|---|
| `lab13-keyfile` | one-shot: `openssl rand -base64 756 > keyfile`, `chown 999:999`, `chmod 400` (+ random passwords, Prometheus config) |
| `prod-mongo-1/2/3` | `mongod --replSet rs0 --keyFile /etc/mongodb/keyfile` - health check, `restart: unless-stopped`, `init`, 768 MB / 1 CPU, log rotation. Host ports 27051-27053 |
| `prod-mongo-1-exporter` | **sidecar**: percona mongodb_exporter 0.47.2 with `network_mode: service:prod-mongo-1` (port 9216) |
| `prometheus` | scrapes the sidecar every 5 s, 4 alert rules - http://localhost:9090 |
| `k3s` | a one-node Kubernetes (k3s v1.30.6) in a privileged container, port 6443, for Lab 13.2 |
| `lab13` | runs `run_lab13.py` (everything below) and writes `results/Exercise-13_Report.pdf` |

## Lab 13.1 - replica set with Docker Compose
1. **Compose file** - `compose/docker-compose.production.yml` (standalone, same settings as the project's compose file).
2. **Keyfile** - made in a named volume, mounted read-only into the three members only.
3. **Initialise + verify** - `rs.initiate()` and the first `createUser` through the **localhost exception**
   (`docker exec prod-mongo-1 mongosh`), then least-privilege users `lab_app`, `billing_app`, `exporter`. Checks: health,
   restart policy, keyfile 400/999 and identical on all members, auth enforced (code 13 / 18), 1 PRIMARY + 2 SECONDARY,
   and a 4th mongod with a **different keyfile cannot join** (health 0, "Authentication failed").
4. **PyMongo failover** - reads and writes every 50 ms (retryable, `w: majority`) during `rs.stepDown()` and during a real
   crash (`kill -9` of mongod; Docker restarts the container). Measured: time to a new primary, longest pause, errors seen,
   acknowledged vs stored writes (0 lost).

Standalone, by hand:
```bash
cd labs/lab13/compose
docker compose -f docker-compose.production.yml up -d   # keyfile -> 3 healthy members
./init-replica.sh                                       # rs.initiate + admin + rs.status
./failover_test.sh                                      # PyMongo reads continuously while rs.stepDown() runs
```

## Activity - Prometheus exporter sidecar
The sidecar shares prod-mongo-1's network namespace (Docker shows `NetworkMode: container:<id of prod-mongo-1>`), connects to
`127.0.0.1:27017` as `exporter` (`clusterMonitor` + `read` on `local`); its password comes from an environment variable, not the
command line. The lab inserts 3,000 documents and shows the counter rise in Prometheus, then restarts prod-mongo-1 and measures
what happens to the sidecar (Kubernetes restarts a pod's containers together; Docker does not).

## Lab 13.2 - Kubernetes StatefulSet
`k8s/namespace.yaml`, `k8s/mongo-headless-svc.yaml` (clusterIP None, selector app: mongo, publishNotReadyAddresses),
`k8s/mongo-statefulset.yaml` (replicas 3, serviceName mongo-headless-svc, volumeClaimTemplates, InitContainer
`keyfile-init`, readiness probe tcp 27017), `k8s/mongo-client.yaml` (application pod). All validated with kubeconform
(strict, Kubernetes 1.30).

* `rs.initiate({_id: 'rs0', members: [mongo-0/1/2.mongo-headless-svc:27017]})` from `mongo-0` (localhost exception).
* **Test resiliency**: 1,000 documents written with `w: 3`, `kubectl delete pod mongo-2`, timed until it is SECONDARY again;
  new pod uid, **same PVC**, same 1,000 documents.
* **13.2 applied to Lab 10**: the client pod moves Rs 1 from A to B in multi-document transactions
  (`mongosh/k8s_transfers.js`) while the **PRIMARY pod** is deleted; afterwards A + B = 2,000 and ledger = commits.
* **The solution text, tested**: `k8s/classroom-variant/` generates the keyfile inside each pod (as written) -> every pod has a
  different key -> `rs.initiate` fails with NodeNotFound "Authentication failed". The real StatefulSet takes the key from ONE
  Secret and the InitContainer only installs it (owner 999, mode 400, in-memory emptyDir).

By hand on any cluster (Docker Desktop Kubernetes, minikube, ...):
```bash
./compose/generate_keyfile.sh --k8s          # keyfile + Secret mongo-keyfile
kubectl -n lab13 create secret generic mongo-admin --from-literal=password="$(openssl rand -base64 18)"
kubectl apply -f k8s/namespace.yaml -f k8s/mongo-headless-svc.yaml -f k8s/mongo-statefulset.yaml
kubectl -n lab13 rollout status statefulset/mongo
kubectl -n lab13 exec -it mongo-0 -- mongosh --eval "rs.initiate({_id:'rs0',members:[{_id:0,host:'mongo-0.mongo-headless-svc:27017'},{_id:1,host:'mongo-1.mongo-headless-svc:27017'},{_id:2,host:'mongo-2.mongo-headless-svc:27017'}]})"
kubectl -n lab13 delete pod mongo-2 && kubectl -n lab13 get pods -w
```

## Lab 13 applied to Lab 10 (mentor's rule)
The classifier billing (3 free uses, then Rs 100/month) is copied to prod rs0 (verified) and used as `billing_app`
(custom role: readWrite on cti_billing + replSetGetConfig). The Lab 10 ACID checks are repeated there, then customer journeys
run **through** a step-down and a crash. After an error the app checks the operation's own record (payment idempotency key,
usage operation id) before retrying, so nothing is charged or counted twice. Every journey must end with wallet Rs 100,
free_used 3, 5 uses, 2 payments; money and revenue invariants must hold.

## Cloud - Atlas free tier with Terraform
`terraform/reference/main.tf` is the mentor's file, unchanged; `terraform/main.tf` is corrected:
M0 needs `provider_name = "TENANT"` + `backing_provider_name = "AWS"` (mongodbatlas_advanced_cluster), the URI output no longer
repeats `mongodb+srv://`, the password is URL-encoded, access is one CIDR (0.0.0.0/0 refused by a validation), the user has
readWrite on one database. `lab13 cloud` runs `terraform validate` on both (needs internet for the provider) and, if
`ATLAS_URI` is set in `.env`, connects to the real cluster.

## Files
| File | Purpose |
|---|---|
| `../../backend/app/lab13.py` | all logic, shared by the scripts and the website page `#/lab13` |
| `run_lab13.py` / `make_report.py` | run everything, save `results/lab13_results.json` + `console_13.txt`, build the PDF |
| `lab13` | command menu: `lab13 all / 13.1 / activity / billing / 13.2 / cloud / stepdown / crash / pods / shell` |
| `setup/prodkeys.py` | keyfile + passwords + Prometheus config (the `lab13-keyfile` container) |
| `compose/` | standalone production compose file, keyfile / init / failover scripts, the reference file |
| `k8s/` | Kubernetes manifests (+ `classroom-variant/`) |
| `monitoring/` | prometheus.yml + alerts.yml (validated with promtool) |
| `mongosh/k8s_transfers.js` | the in-cluster transaction client |
| `terraform/` | corrected main.tf, tfvars example, `reference/main.tf` |

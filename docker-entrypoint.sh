#!/usr/bin/env bash
# Entrypoint of every container built from this image.
#   rs-init           initialise replica set rs0, wait until healthy, exit   (infrastructure)
#   shard-init        initiate cfgrs + shardA + shardB, wait for mongos, addShard x2, exit (Lab 11 infrastructure)
#   lab7              website on :8000 (+ HTTPS :8443, Kavach Assist chat) + activate Lab 7 (database, indexes, 5-minute auto-updater)
#   scanner-bot       final assessment: the background vulnerability scanner (fixes only after a confirmation in chat)
#   lab12-certs       Lab 12: create the CA, server/client certificates, passwords and bootstrap.sh in /certs, exit
#   lab13-keyfile     Lab 13: openssl rand -base64 756 > keyfile (owner 999, mode 400) + passwords + Prometheus config, exit
#   exporter-sidecar  Lab 13 activity: mongodb_exporter in prod-mongo-1's network namespace (127.0.0.1:27017 -> :9216)
#   lab8 ... lab18    activate that lab (lab14 also starts the ops daemon: monitor every 30 s + hourly backups) (its prerequisite lab is already healthy: compose depends_on)
#   compose           print the demo compose file that pulls this image from Docker Hub
#   <other>           run the command
set -uo pipefail
cd /app
rm -f /tmp/lab-ready     # a marker left from the last run would make the next lab start too early
case "${1:-lab7}" in
  rs-init)
    until python labs/lab9/setup/rsctl.py init; do sleep 3; done
    python labs/lab9/setup/rsctl.py wait-healthy --timeout 180 || true
    python labs/lab9/setup/rsctl.py status || true ;;
  shard-init)
    until python labs/lab11/setup/clusterctl.py init --timeout 180; do echo "[shard-init] retrying in 5 s"; sleep 5; done ;;
  lab12-certs)
    python labs/lab12/setup/certs.py init --out "${CERTS_DIR:-/certs}" ;;
  lab13-keyfile)
    python labs/lab13/setup/prodkeys.py init --keys /keys --out /prod --mon /monitoring ;;
  exporter-sidecar)
    until [ -s /prod/secrets.json ]; do echo "[exporter] waiting for /prod/secrets.json"; sleep 2; done
    MONGODB_USER=exporter
    MONGODB_PASSWORD="$(python -c "import json; print(json.load(open('/prod/secrets.json'))['exporter'])")"
    export MONGODB_USER MONGODB_PASSWORD          # from the environment, not the command line (not in docker inspect)
    exec mongodb_exporter --mongodb.uri="mongodb://127.0.0.1:27017/admin" --mongodb.direct-connect \
      --collector.diagnosticdata --collector.replicasetstatus --compatible-mode --web.listen-address=":9216" ;;
  scanner-bot)
    exec python -m backend.app.assist.scanner_bot ;;
  lab7)
    python -m uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --timeout-graceful-shutdown 10 &
    WEB=$!
    # the same website over HTTPS on :8443 (self-signed certificate made at start; the labs keep using plain :8000)
    mkdir -p /tmp/tls
    if openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
         -keyout /tmp/tls/key.pem -out /tmp/tls/cert.pem >/dev/null 2>&1; then
      ASSIST_INGEST=0 python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile /tmp/tls/key.pem \
        --ssl-certfile /tmp/tls/cert.pem --timeout-graceful-shutdown 10 --log-level warning &
      TLS=$!
    fi
    # graceful shutdown (docker stop): pass SIGTERM on, so uvicorn finishes open requests and closes its connection pools
    trap 'kill -TERM $WEB ${TLS:-} 2>/dev/null; wait $WEB ${TLS:-} 2>/dev/null; exit 0' TERM INT
    python orchestrator/labctl.py activate 7 --mode "${LAB_MODE:-quick}" || echo "[lab7] activation reported a problem - see the website"
    touch /tmp/lab-ready
    echo "[lab7] READY  ->  http://localhost:${PORT:-8000}"
    wait $WEB ;;
  lab8|lab9|lab10|lab11|lab12|lab13|lab14|lab15|lab16|lab17|lab18)
    N="${1#lab}"
    python orchestrator/labctl.py activate "$N" --mode "${LAB_MODE:-quick}" || echo "[lab$N] activation reported a problem - see the Lab control centre"
    touch /tmp/lab-ready
    echo "[lab$N] READY"
    exec sleep infinity ;;
  compose) cat /app/docker-compose.hub.yml ;;
  *) exec "$@" ;;
esac

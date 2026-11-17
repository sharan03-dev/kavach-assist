#!/usr/bin/env bash
# Finishes the submission: Lab 18 activity again (data already loaded, ~1 min), Lab 16 "applied to Lab 10" with the
# sharded cluster running, then the three PDFs.        Usage:  bash finish.sh [dockerhub-user]
set -u
HUBUSER="${1:-sharan10860}"; cd "$(dirname "$0")"
export LAB_IMAGE="$HUBUSER/nosql-lab18:latest" LAB13_K8S=off
url() { echo "https://hub.docker.com/r/$HUBUSER/nosql-lab$1"; }
C="docker compose -f docker-compose.demo.yml"
V="-v $PWD/backend:/app/backend -v $PWD/labs:/app/labs"
R() { $C run --rm --no-deps $V "$@"; }
docker stop lab16 >/dev/null 2>&1; ids=$(docker ps -q --filter name=lab18-run); [ -n "$ids" ] && docker stop $ids >/dev/null
echo "== 1/4 Lab 18 activity (communities) on the loaded data"
R -e DOCKER_URL="$(url 18)" lab18 python labs/lab18/rerun_activity.py
echo "== 2/4 start the sharded cluster (the Lab 10 billing ledger lives there since Lab 11)"
docker start cfg1 shard-a1 shard-a2 shard-b1 shard-b2 >/dev/null; sleep 15; docker start mongos >/dev/null
printf "   waiting for mongos"; until docker exec mongos mongosh --port 27030 --quiet --eval "db.adminCommand('ping').ok" >/dev/null 2>&1; do printf "."; sleep 5; done; echo " ok"; sleep 10
echo "== 3/4 Lab 16 applied to Lab 10"
R lab18 python labs/lab16/run_lab16.py --only applied
echo "== 4/4 the three reports"
for n in 16 17 18; do R -e DOCKER_URL="$(url $n)" lab18 bash /app/labs/lab$n/lab$n report "$(url $n)"; done
python3 - <<'PY'
import json
for n in (16, 17, 18):
    for c in json.load(open(f"results/lab{n}_results.json")).get("checks", []):
        print(f"  Lab {n} [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}")
PY
echo; echo "Submit results/Exercise-16_Report.pdf, Exercise-17_Report.pdf, Exercise-18_Report.pdf"

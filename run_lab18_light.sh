#!/usr/bin/env bash
# Runs Lab 18 with only the containers it needs (MongoDB rs0, Cassandra, Neo4j, website) so Docker has enough memory,
# shows the output live, then rebuilds the three PDF reports.        Usage:  bash run_lab18_light.sh [dockerhub-user]
set -u
HUBUSER="${1:-sharan10860}"; cd "$(dirname "$0")"
export LAB_IMAGE="$HUBUSER/nosql-lab18:latest" LAB13_K8S=off
url() { echo "https://hub.docker.com/r/$HUBUSER/nosql-lab$1"; }
C="docker compose -f docker-compose.demo.yml"
echo "== why did containers stop? (OOMKilled = Docker ran out of memory)"
for c in cassandra neo4j lab16 lab17 lab18; do printf '   %-10s ' "$c"; docker inspect -f '{{.State.Status}}  OOMKilled={{.State.OOMKilled}}  exit={{.State.ExitCode}}' "$c" 2>/dev/null || echo "-"; done
echo "== 1/4 pause the containers Lab 18 does not need (they come back with: $C up -d lab18)"
docker stop -t 20 k3s prometheus prod-mongo-1-exporter shard-a1 shard-a2 shard-b1 shard-b2 cfg1 mongos mongo-secure \
  prod-mongo-1 prod-mongo-2 prod-mongo-3 atlas-local lab8 lab9 lab10 lab11 lab12 lab13 lab14 lab15 lab16 lab17 lab18 >/dev/null 2>&1
echo "== 2/4 fresh Cassandra + Neo4j"
docker start mongo1 mongo2 mongo3 lab7 >/dev/null 2>&1
docker restart cassandra neo4j >/dev/null
printf "   waiting for Cassandra"; until docker exec cassandra cqlsh -e "SELECT release_version FROM system.local" >/dev/null 2>&1; do printf "."; sleep 5; done; echo " ok"
printf "   waiting for Neo4j"; until docker exec neo4j cypher-shell -u neo4j -p 'Lab16-neo4j!' "RETURN 1" >/dev/null 2>&1; do printf "."; sleep 5; done; echo " ok"
echo "   code in the pushed image has the latest fixes: $($C run --rm --no-deps lab18 grep -c _resilient backend/app/lab18.py) (should be 6)"
V="-v $PWD/backend:/app/backend -v $PWD/labs:/app/labs"     # run the code in this folder (always the newest fixes)
echo "== 3/4 Lab 18 (100,000 threats, about 15 minutes - output is live)"
$C run --rm --no-deps $V -e DOCKER_URL="$(url 18)" lab18 python labs/lab18/run_lab18.py --report
echo "== 4/4 reports"
for n in 16 17 18; do $C run --rm --no-deps $V -e DOCKER_URL="$(url $n)" lab18 "lab$n" report "$(url $n)"; done
grep -E "PASS|FAIL" results/console_18.txt
echo; echo "Submit results/Exercise-16_Report.pdf, Exercise-17_Report.pdf, Exercise-18_Report.pdf"
echo "For the live demo later (starts all labs again):  LAB_IMAGE=$LAB_IMAGE $C up -d lab18"

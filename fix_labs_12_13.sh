#!/usr/bin/env bash
# Fixes the two red cards in the Lab control centre (Lab 12 and Lab 13) and re-runs them.   Usage: bash fix_labs_12_13.sh
cd "$(dirname "$0")"
echo "== 1/3 put the fixed Lab 12 code into the running containers"
for c in lab7 lab12 lab13 lab14 lab15 lab16 lab17 lab18; do docker cp backend/app/lab12.py $c:/app/backend/app/lab12.py >/dev/null 2>&1; done
echo "== 2/3 Lab 12 again (about 1 minute)"
docker exec lab12 python orchestrator/labctl.py activate 12 --mode full | grep -E "PASS|FAIL|OK:|FAILED:"
echo "== 3/3 Lab 13: restart the exporter sidecar (it loses prod-mongo-1's network when that container restarts), then Lab 13 again (3-5 minutes)"
docker restart prod-mongo-1-exporter >/dev/null; sleep 10
docker exec lab13 python orchestrator/labctl.py activate 13 --mode full | grep -E "PASS|FAIL|OK:|FAILED:"
echo; echo "Refresh http://localhost:8000/#/labs - Lab 12 and Lab 13 should be Active."

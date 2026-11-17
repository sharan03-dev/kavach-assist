#!/bin/bash
cd "$(dirname "$0")"
echo "== copy fixed Lab 13 code into the running containers"
for c in lab7 lab12 lab13 lab14 lab15 lab16 lab17 lab18; do docker cp backend/app/lab13.py $c:/app/backend/app/lab13.py >/dev/null 2>&1; done
echo "== Lab 13 again (3-5 minutes)"
docker exec lab13 python orchestrator/labctl.py activate 13 --mode full | grep -E "PASS|FAIL|OK:|FAILED:|sidecar|!!"
echo; echo "Refresh http://localhost:8000/#/labs - Lab 13 should be Active."

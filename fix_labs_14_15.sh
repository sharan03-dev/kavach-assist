#!/bin/bash
# Re-run Lab 14 then Lab 15 one after the other (nothing else may be running)
cd "$(dirname "$0")"
docker restart prod-mongo-1-exporter >/dev/null; sleep 8
echo "== Lab 14 (about 10 minutes)"
docker exec lab14 python orchestrator/labctl.py activate 14 --mode full | grep -E "\[PASS\]|\[FAIL\]|OK:|FAILED:|^!!"
echo "== Lab 15 (about 2 minutes)"
docker exec lab15 python orchestrator/labctl.py activate 15 --mode full | grep -E "\[PASS\]|\[FAIL\]|OK:|FAILED:|^!!"
echo; echo "Refresh http://localhost:8000/#/labs - Lab 14 and Lab 15 should be Active."

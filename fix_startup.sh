#!/bin/bash
# Rebuild the lab image with the one-lab-at-a-time fix, restart the labs in order, then show the status.
cd "$(dirname "$0")"
echo "== 1/3 rebuild the image (2-5 minutes)"
docker compose build lab7 || exit 1
echo "== 2/3 recreate the lab containers - they now run 7 -> 8 -> ... -> 18 one after the other"
docker exec mongo1 mongosh --quiet --eval 'db.getSiblingDB("cti_platform").locks.deleteMany({})' >/dev/null 2>&1
docker compose up -d --force-recreate lab7 lab8 lab9 lab10 lab11 lab12 lab13 lab14 lab15 lab16 lab17 lab18
echo "== 3/3 done starting. The chain takes about 60-90 minutes."
echo "Watch http://localhost:8000/#/labs   or run:  docker exec lab7 python orchestrator/labctl.py status"

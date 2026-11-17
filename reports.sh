#!/usr/bin/env bash
# Rebuilds only the three PDF reports (seconds).   Usage: bash reports.sh [dockerhub-user]
HUBUSER="${1:-sharan10860}"; cd "$(dirname "$0")"
export LAB_IMAGE="$HUBUSER/nosql-lab18:latest"
for n in 16 17 18; do docker compose -f docker-compose.demo.yml run --rm --no-deps -v "$PWD/backend:/app/backend" -v "$PWD/labs:/app/labs" \
  -e DOCKER_URL="https://hub.docker.com/r/$HUBUSER/nosql-lab$n" lab18 bash /app/labs/lab$n/lab$n report "https://hub.docker.com/r/$HUBUSER/nosql-lab$n"; done

#!/usr/bin/env bash
# Re-runs ONLY what changed after a fix: rebuilds + pushes the image (nosql-lab16/17/18), then recreates the website (lab7),
# lab16 and lab18 from the new image - Labs 7-15 and 17 keep running, nothing is wiped.   Usage: ./fix_and_rerun.sh <dockerhub-user>
set -eu
HUBUSER="${1:-sharan10860}"; cd "$(dirname "$0")"
IMAGE="$HUBUSER/nosql-lab18:latest"; url() { echo "https://hub.docker.com/r/$HUBUSER/nosql-lab$1"; }
echo "== 1/4 build (amd64 + arm64) and push"
docker buildx inspect labbuilder >/dev/null 2>&1 || docker buildx create --name labbuilder >/dev/null
docker buildx build --builder labbuilder --platform linux/amd64,linux/arm64 --build-arg IMAGE_NAME="$IMAGE" \
  -t "$HUBUSER/nosql-lab16:latest" -t "$HUBUSER/nosql-lab17:latest" -t "$IMAGE" --push .
docker pull "$IMAGE"
C="docker compose -f docker-compose.demo.yml"
export LAB_IMAGE="$IMAGE" DOCKER_URL="$(url 18)" LAB13_K8S="${LAB13_K8S:-off}"
wait_healthy() { for i in $(seq 1 720); do [ "$(docker inspect -f '{{.State.Health.Status}}' "$1" 2>/dev/null)" = healthy ] && return 0; sleep 5; done; return 1; }
echo "== 2/4 website (lab7) with the new code"
$C up -d --no-deps --force-recreate lab7; wait_healthy lab7
echo "== 3/4 Lab 16 (a few minutes), then Lab 18 (5-15 minutes)"
[ "${SKIP16:-0}" = 1 ] || { $C up -d --no-deps --force-recreate lab16; wait_healthy lab16; }
$C up -d --no-deps --force-recreate lab18; wait_healthy lab18
echo "== 4/4 reports"
for n in 16 17 18; do docker exec -e DOCKER_URL="$(url $n)" "lab$n" "lab$n" report "$(url $n)" || true; done
grep -E "PASS|FAIL" results/console_16.txt results/console_18.txt || true
echo; echo "Submit results/Exercise-16_Report.pdf, Exercise-17_Report.pdf, Exercise-18_Report.pdf  (website: http://localhost:8000/#/lab18)"

#!/usr/bin/env bash
# =============================================================================
# Exercises 16, 17 and 18 in ONE command (Docker Desktop must be running; Settings -> Resources: give it >= 10 GB memory):
#
#     ./publish.sh <your-dockerhub-username>
#
#   1. writes .env (Docker Hub user + URL)       2. docker login
#   3. builds ONE image for Intel AND Apple Silicon and pushes it as three repositories (same image, one per exercise):
#        https://hub.docker.com/r/<user>/nosql-lab16   https://hub.docker.com/r/<user>/nosql-lab17   https://hub.docker.com/r/<user>/nosql-lab18
#   4. starts Labs 7 -> 8 -> ... -> 17 -> 18 FROM THE PUSHED IMAGE (Cassandra, Neo4j and MongoDB Atlas Local included)
#   5. writes results/Exercise-16_Report.pdf, Exercise-17_Report.pdf, Exercise-18_Report.pdf (each with its Docker URL)
#
#   Lab 13.2 Kubernetes (k3s) is switched off by default here (LAB13_K8S=off) to save memory; use LAB13_K8S=k3s ./publish.sh <user>
#   if your machine has 16 GB or more.
# =============================================================================
set -eu
HUBUSER="${1:-}"; [ -n "$HUBUSER" ] || { echo "usage: ./publish.sh <dockerhub-username>"; exit 2; }
cd "$(dirname "$0")"
K8S="${LAB13_K8S:-off}"
IMAGE="$HUBUSER/nosql-lab18:latest"
url() { echo "https://hub.docker.com/r/$HUBUSER/nosql-lab$1"; }
docker info >/dev/null 2>&1 || { echo "Docker is not running - start Docker Desktop first."; exit 1; }
printf 'DOCKERHUB_USER=%s\nDOCKER_URL=%s\nLAB13_K8S=%s\n' "$HUBUSER" "$(url 18)" "$K8S" > .env
echo "== 1/4 docker login ($HUBUSER)"; docker login
echo "== 2/4 build (amd64 + arm64) and push $HUBUSER/nosql-lab16, -lab17, -lab18"
docker buildx inspect labbuilder >/dev/null 2>&1 || docker buildx create --name labbuilder >/dev/null
docker buildx build --builder labbuilder --platform linux/amd64,linux/arm64 --build-arg IMAGE_NAME="$IMAGE" \
  -t "$HUBUSER/nosql-lab16:latest" -t "$HUBUSER/nosql-lab17:latest" -t "$IMAGE" --push .
echo "== 3/4 start Labs 7 -> 8 -> ... -> 17 -> 18 from the pushed image"
docker compose down -v >/dev/null 2>&1 || true
docker compose -f docker-compose.demo.yml down -v >/dev/null 2>&1 || true
docker pull "$IMAGE"
docker run --rm "$IMAGE" compose > docker-compose.demo.yml
LAB_IMAGE="$IMAGE" docker compose -f docker-compose.demo.yml pull --ignore-buildable
LAB_IMAGE="$IMAGE" DOCKER_URL="$(url 18)" LAB13_K8S="$K8S" docker compose -f docker-compose.demo.yml up -d lab18
echo "   waiting for Lab 18 (first start: 30-60 minutes in total - every lab from 7 to 18 runs once, in order)"
echo "   follow it live:  http://localhost:8000/#/labs     or     docker compose -f docker-compose.demo.yml logs -f lab16 lab17 lab18"
for i in $(seq 1 900); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' lab18 2>/dev/null)" = healthy ] && break
  sleep 5
done
echo "== 4/4 reports"
for n in 16 17 18; do docker exec -e DOCKER_URL="$(url $n)" "lab$n" "lab$n" report "$(url $n)" || true; done
echo; echo "Website         : http://localhost:8000/#/lab18   (also #/lab16, #/lab17)"
echo "Neo4j Browser   : http://localhost:7474  (neo4j / Lab16-neo4j!)"
for n in 16 17 18; do echo "Exercise $n      : $(url $n)   ->  submit results/Exercise-${n}_Report.pdf"; done

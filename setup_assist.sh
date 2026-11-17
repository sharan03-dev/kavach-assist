#!/bin/bash
# Final assessment - Kavach Assist. Builds the image, starts what the chat needs and prints how to sign in.
#   bash setup_assist.sh            start it next to whatever is already running
#   bash setup_assist.sh --lean     first stop the heavy lab containers (recommended on an 8 GB Mac, gives the model more memory)
cd "$(dirname "$0")" || exit 1

echo "== 1/5 secrets in .env (never in code or in the compose file)"
touch .env
has() { grep -Eq "^$1=.+" .env; }
setkv() { grep -v "^$1=" .env > .env.tmp; mv .env.tmp .env; echo "$1=$2" >> .env; }
has ADMIN_EMAIL    || setkv ADMIN_EMAIL "admin@kavach.local"
has ADMIN_PASSWORD || setkv ADMIN_PASSWORD "Kavach-$(openssl rand -hex 4)"
has JWT_SECRET     || setkv JWT_SECRET "$(openssl rand -hex 32)"
mkdir -p scan_target results
ADMIN_EMAIL=$(grep '^ADMIN_EMAIL=' .env | cut -d= -f2-); ADMIN_PASSWORD=$(grep '^ADMIN_PASSWORD=' .env | cut -d= -f2-)

if [ "$1" = "--lean" ]; then
  echo "== stopping the lab containers that the chat does not need (bring them back later with: docker compose up -d)"
  docker compose stop lab18 lab17 lab16 lab15 lab14 lab13 lab12 lab11 lab10 lab9 lab8 cassandra neo4j k3s prometheus prod-mongo-1-exporter \
    prod-mongo-1 prod-mongo-2 prod-mongo-3 mongo-secure mongos shard-a1 shard-a2 shard-b1 shard-b2 cfg1 >/dev/null 2>&1
fi

echo "== 2/5 build the image (2-5 minutes the first time)"
docker compose build lab7 || { echo "BUILD FAILED - send a screenshot"; exit 1; }

echo "== 3/5 start: replica set, website (lab7), Atlas Local (vector search), Ollama (language model), scanner bot"
docker compose up -d lab7 atlas-local ollama scanner-bot || { echo "START FAILED - send a screenshot"; exit 1; }

echo "== 4/5 wait until the website is healthy"
for i in $(seq 1 120); do
  if curl -fsS -o /dev/null http://localhost:8000/health 2>/dev/null; then break; fi
  sleep 2
done
curl -s http://localhost:8000/health; echo

echo "== 5/5 checks"
docker exec lab7 python -m unittest tests.test_assist 2>&1 | tail -3
docker compose ps --format '{{.Name}}: {{.Status}}' lab7 atlas-local ollama scanner-bot 2>/dev/null
docker logs lab7 2>&1 | grep "\[assist\]" | tail -4

cat <<MSG

------------------------------------------------------------------
Open            http://localhost:8000            (HTTPS: https://localhost:8443 - self-signed, the browser warns once)
Administrator   $ADMIN_EMAIL
Password        $ADMIN_PASSWORD                  (stored in .env)
Other users     "Create account" on the sign-in page, or Google (after GOOGLE_CLIENT_ID is set - see README)

The language model (about 2 GB) downloads in the background on the first start.
Until it is ready the chat answers with the built-in writer - the pill on the chat page shows the state.
------------------------------------------------------------------
MSG

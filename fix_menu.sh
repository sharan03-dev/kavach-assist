#!/usr/bin/env bash
# Puts the scrolling-menu fix into the running website (lab7) and checks it.
cd "$(dirname "$0")"
docker cp frontend/index.html lab7:/app/frontend/index.html
docker cp frontend/assets/styles.css lab7:/app/frontend/assets/styles.css
if curl -s http://localhost:8000/ | grep -q "overflow-y:auto"; then
  echo "OK - the website now has the fix. In Chrome press Cmd+Shift+R on http://localhost:8000"
else
  echo "NOT applied - is the lab7 container running?  docker ps | grep lab7"
fi

#!/usr/bin/env bash
# Runs failover_test.py in a throw-away Python container on the compose network (lab13-prodnet).
set -euo pipefail
cd "$(dirname "$0")"
docker run --rm --network lab13-prodnet -v "$PWD:/w" -w /w python:3.12-slim \
  sh -c "pip install -q 'pymongo>=4.9,<5' && python failover_test.py --password '$(cat admin-password.txt)'"

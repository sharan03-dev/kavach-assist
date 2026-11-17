#!/bin/bash
# Keep MongoDB updated every 5 minutes:  ./run_updater.sh   (Ctrl+C to stop)
cd "$(dirname "$0")"
[ -d .venv ] && source .venv/bin/activate
brew services list 2>/dev/null | grep -q "mongodb-community.*started" || brew services start mongodb-community@8.0
python backend/scripts/auto_update.py "$@"

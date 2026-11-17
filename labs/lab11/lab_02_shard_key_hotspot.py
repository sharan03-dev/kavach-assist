"""Lab 11.2 - Shard key hotspot detection: db.hotspot_data {ts: 1} vs db.even_data {user_id: 'hashed'}.

Same file name as in the course repository (chapter-11). The course version only simulates the result; this one
runs the experiment on the real sharded cluster via run_lab11.py, so the output and the report agree.

    python labs/lab11/lab_02_shard_key_hotspot.py            (inside Docker:  docker compose exec lab11 lab11 11.2)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.exit(subprocess.call([sys.executable, os.path.join(HERE, "run_lab11.py"), "--only", "11.2", *sys.argv[1:]]))

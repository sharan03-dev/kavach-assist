"""Lab 11.1 - Evaluate shard key choices (measured on the sharded cluster).

Same file name as in the course repository (chapter-11). The course version only simulates the result; this one
runs the experiment on the real sharded cluster via run_lab11.py, so the output and the report agree.

    python labs/lab11/lab_01_shard_key_eval.py            (inside Docker:  docker compose exec lab11 lab11 11.1)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.exit(subprocess.call([sys.executable, os.path.join(HERE, "run_lab11.py"), "--only", "11.1", *sys.argv[1:]]))

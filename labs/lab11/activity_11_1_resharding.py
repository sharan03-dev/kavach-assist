"""Activity 11.1 - Try it yourself: MongoDB 5.0+ resharding, run live (steps + limitations).

Same file name as in the course repository (chapter-11). The course version only simulates the result; this one
runs the experiment on the real sharded cluster via run_lab11.py, so the output and the report agree.

    python labs/lab11/activity_11_1_resharding.py            (inside Docker:  docker compose exec lab11 lab11 reshard)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.exit(subprocess.call([sys.executable, os.path.join(HERE, "run_lab11.py"), "--only", "reshard", *sys.argv[1:]]))

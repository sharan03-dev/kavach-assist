#!/usr/bin/env python3
"""Lab 14 operations daemon (started by Lab 14 in the lab14 container; pid in /tmp/lab14_daemon.pid).

  every 30 s   monitor_dashboard: sample prod rs0 as monitor_app -> results/metrics.log + cti_platform.lab14_metrics,
               WARNING lines, webhook on FIRING / RESOLVED          (Lab 14.1 + activity)
  every hour   backup_verify.run_backup of the billing ledger (prod rs0, backup_app) and the Kavach platform (rs0),
               then the 7-day retention policy                       (Lab 14.2)
Both jobs use the schedule library:  schedule.every(30).seconds.do(...)  /  schedule.every().hour.do(...)
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from backend.app import lab14  # noqa: E402

if __name__ == "__main__":
    lab14.daemon(log=lambda m: print(f"{datetime.now():%H:%M:%S} {m}", flush=True))

"""Re-runs only the Lab 18 activity (communities) on the data already loaded, updates results + report.
    python labs/lab18/rerun_activity.py"""
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "labs"))
from backend.app import lab18 as L  # noqa: E402

RES = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
out = json.loads((RES / "lab18_results.json").read_text())
r = out["lab_18_1"]
r["communities"] = json.loads(json.dumps(L.communities(), default=str))
spec = importlib.util.spec_from_file_location("run18", ROOT / "labs/lab18/run_lab18.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
r["pass"] = all(r["benchmark"]["same_answer"].get(k, False) for k in ("q2", "q3")) and r["fp"]["identical"] and r["communities"]["pass"]
out["checks"] = [{"name": n, "ok": bool(v)} for n, v in m.checks(out)]
(RES / "lab18_results.json").write_text(json.dumps(out, indent=2, default=str))
for c in out["checks"]:
    print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}")
spec = importlib.util.spec_from_file_location("rep18", ROOT / "labs/lab18/make_report.py"); mr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)
print("Report written:", mr.build(out, RES, os.getenv("DOCKER_URL", "")))

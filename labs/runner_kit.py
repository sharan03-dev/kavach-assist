"""Shared runner for Labs 16-18: tee the console to results/console_<lab>.txt, run the parts, print PASS/FAIL, save JSON,
store the results in MongoDB (cti_platform.lab_results) and build the PDF report."""
import importlib.util
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
RESULTS.mkdir(parents=True, exist_ok=True)


class Tee:
    def __init__(self, path):
        self.f, self.o = open(path, "w", encoding="utf-8"), sys.stdout

    def write(self, s):
        self.o.write(s); self.f.write(s)

    def flush(self):
        self.o.flush(); self.f.flush()


def banner(t):
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}", flush=True)


def guarded(name, fn, *a, **kw):
    t0 = time.time()
    try:
        out = fn(*a, **kw)
    except Exception as exc:                           # noqa: BLE001
        print(f"!! {name} failed: {type(exc).__name__}: {exc}")
        traceback.print_exc(limit=4, file=sys.stdout)
        out = {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "pass": False}
    if isinstance(out, dict):
        out.setdefault("seconds", round(time.time() - t0, 1))
    return out


def run(lab: int, parts, args, checks_fn, header: str):
    """parts = [(key, title, callable(quick) -> dict)]"""
    sys.stdout = Tee(RESULTS / (f"console_{lab}.txt" if not args.only else f"console_{lab}_{args.only}.txt"))
    print(f"root@lab{lab}-runner:/app# lab{lab} {'--only ' + args.only if args.only else 'all'}{' --quick' if args.quick else ''}")
    print(header, flush=True)
    p = RESULTS / f"lab{lab}_results.json"
    try:
        out = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        out = {}
    from backend.app import polyglot as PG
    av = PG.availability(force=True)
    out["environment"] = {"at": datetime.now(timezone.utc).isoformat(), "quick": args.quick,
                          "backends": {k: (v.get("version") if v.get("ok") else "unavailable: " + str(v.get("error"))[:80])
                                       for k, v in av.items() if isinstance(v, dict) and "ok" in v}, "hosts": av.get("hosts")}
    for k, v in out["environment"]["backends"].items():
        print(f"   {k:<10} {v}")
    for key, title, fn in parts:
        if args.only in (None, key):
            banner(title)
            out[key] = guarded(key, fn, args.quick)
    banner("SUMMARY")
    ok = True
    checks = checks_fn(out)
    for n, v in checks:
        print(f"  [{'PASS' if v else 'FAIL'}] {n}")
        ok &= bool(v)
    out["checks"] = [{"name": n, "ok": bool(v)} for n, v in checks]
    out = json.loads(json.dumps(out, default=str))
    p.write_text(json.dumps(out, indent=2))
    try:
        from backend.app.db import get_db
        get_db().lab_results.replace_one({"_id": f"lab_{lab}"}, {"_id": f"lab_{lab}", **out}, upsert=True)
    except Exception as exc:                           # noqa: BLE001
        print(f"(could not store results in MongoDB: {str(exc)[:120]})")
    print(f"\nSaved results/lab{lab}_results.json, results/console_{lab}.txt and MongoDB cti_platform.lab_results (_id lab_{lab})")
    if args.report and not args.only:
        sys.stdout.flush()
        spec = importlib.util.spec_from_file_location(f"lab{lab}_report", ROOT / "labs" / f"lab{lab}" / "make_report.py")
        mr = importlib.util.module_from_spec(spec); spec.loader.exec_module(mr)
        print(f"Report written: {mr.build(out, RESULTS, os.getenv('DOCKER_URL', ''))}")
    return ok

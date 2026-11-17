"""Keep the CTI database fresh - runs forever and refreshes MongoDB every 5 minutes.

    python backend/scripts/auto_update.py                 # every 5 minutes (default)
    python backend/scripts/auto_update.py --interval 60   # every 60 seconds (handy for a quick demo)
    python backend/scripts/auto_update.py --once          # run a single cycle and exit
    python backend/scripts/auto_update.py --quiet         # only print one summary line per cycle

What happens in each cycle
    news reports (30 RSS feeds)   -> every cycle                    (new articles are inserted + classified)
    GCVE + CISA KEV               -> every  --gcve-every  cycles    (default 1  = every cycle)
    IOC / IP blacklist feeds      -> every  --feeds-every cycles    (default 6  = every 30 min, they are big)
    one "heartbeat" document      -> every cycle, in cti_platform.ingest_runs (kind = "auto_update")

Open MongoDB Compass -> cti_platform -> ingest_runs and press refresh: a new document appears every
5 minutes, so you can show your mentor that the updater is alive even when no new article was published.
Stop with Ctrl+C. Run it in a second terminal next to the website - they do not disturb each other.
"""
import argparse
import asyncio
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import config, ingest  # noqa: E402
from backend.app.db import ensure_indexes, get_db, ping  # noqa: E402

UTC = timezone.utc
STOP = threading.Event()


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def say(msg: str):
    print(f"[{stamp()}] {msg}", flush=True)


def _step(name, fn, summary, results, quiet):
    """Run one refresh step. A failing step never stops the cycle - the error is stored instead."""
    t0 = time.perf_counter()
    log = (lambda *_a, **_k: None) if quiet else (lambda m="": print("   ", m, flush=True))
    try:
        out = fn(log)
        status = (out or {}).get("status", "ok") if isinstance(out, dict) else "ok"
        entry = {"step": name, "status": status, "seconds": round(time.perf_counter() - t0, 1)}
        entry.update(summary(out))
    except Exception as exc:  # network / parsing problems must not kill the 5-minute loop
        entry = {"step": name, "status": "error", "seconds": round(time.perf_counter() - t0, 1),
                 "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    results.append(entry)
    return entry


def run_cycle(db, n, gcve_every, feeds_every, quiet):
    started = datetime.now(UTC)
    t0 = time.perf_counter()
    results = []
    due = lambda every: every > 0 and (n - 1) % every == 0   # cycle 1 always runs everything  # noqa: E731

    # Order matters (same as setup_database.py): exploitation data first, so new reports are classified with it.
    if due(gcve_every):
        _step("cisa_kev", lambda log: ingest.import_kev(db, log=log),
              lambda o: {"items": o.get("items")}, results, quiet)
        _step("gcve", lambda log: asyncio.run(ingest.import_gcve(db, log=log)),
              lambda o: {"sightings": o.get("sightings"), "vulns_sighted": o.get("vulns_sighted")}, results, quiet)
    if due(feeds_every):
        def feeds(log):
            res = ingest.import_ioc_feeds(db, log=log)
            return {"status": "ok" if any(r["status"] == "ok" for r in res) else "error", "res": res}
        _step("ioc_feeds", feeds,
              lambda o: {"feeds_ok": sum(r["status"] == "ok" for r in o["res"]), "feeds_failed":
                         sum(r["status"] != "ok" for r in o["res"]),
                         "indicators": sum(r.get("items", 0) for r in o["res"])}, results, quiet)
    news_entry = _step("news", lambda log: asyncio.run(ingest.ingest_news(db, log=log)),
                       lambda o: {"new_reports": o["new_reports"], "feeds_checked": len(o["results"]),
                                  "feeds_failed": sum(r["status"] != "ok" for r in o["results"])}, results, quiet)

    finished = datetime.now(UTC)
    # The heartbeat document: one per cycle, visible in Compass -> cti_platform -> ingest_runs
    db.ingest_runs.insert_one({
        "kind": "auto_update", "cycle": n, "started_at": started, "finished_at": finished,
        "duration_s": round(time.perf_counter() - t0, 1),
        "new_reports": news_entry.get("new_reports", 0),
        "results": results,                                   # EMBEDDED, bounded (max 4 steps)
        "totals": {"reports": db.reports.estimated_document_count(),
                   "indicators": db.indicators.estimated_document_count(),
                   "vulnerabilities": db.vulnerabilities.estimated_document_count()},
    })
    # keep the collection small: a week of 5-minute heartbeats is about 2,000 documents
    db.ingest_runs.delete_many({"kind": "auto_update", "started_at": {"$lt": finished - timedelta(days=7)}})

    # Lab 12 applied to Lab 7: push the fresh data to the hardened server as the least-privilege ingest_app (TLS)
    if os.path.exists(os.path.join(os.getenv("CERTS_DIR", "/certs"), "secrets.json")):
        try:
            from backend.app import lab12
            if db.lab_chain.find_one({"_id": "lab12", "status": "ok"}):
                lab12.sync_secure(db, log=None if quiet else say)
        except Exception as exc:                          # noqa: BLE001 - the secure server is optional
            if not quiet:
                say(f"secure sync skipped: {str(exc)[:120]}")

    failed = [r["step"] for r in results if r["status"] == "error"]
    say(f"cycle {n} done in {time.perf_counter() - t0:.0f}s - "
        f"{news_entry.get('new_reports', 0)} new reports"
        + (f" - FAILED steps: {', '.join(failed)}" if failed else " - all steps ok"))


def main():
    env_int = lambda k, d: int(os.getenv(k, d))  # noqa: E731
    ap = argparse.ArgumentParser(description="Refresh the CTI MongoDB database on a schedule")
    ap.add_argument("--interval", type=int, default=env_int("AUTO_UPDATE_INTERVAL", 300),
                    help="seconds between cycles (default 300 = 5 minutes)")
    ap.add_argument("--gcve-every", type=int, default=env_int("AUTO_UPDATE_GCVE_EVERY", 1),
                    help="refresh GCVE + CISA KEV every N cycles (default 1; 0 = never)")
    ap.add_argument("--feeds-every", type=int, default=env_int("AUTO_UPDATE_FEEDS_EVERY", 6),
                    help="refresh the big IP/URL blacklists every N cycles (default 6; 0 = never)")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--quiet", action="store_true", help="one summary line per cycle")
    args = ap.parse_args()
    if args.interval < 10:
        sys.exit("--interval must be at least 10 seconds")

    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())

    say(f"Auto-updater started: every {args.interval}s | GCVE/KEV every {args.gcve_every} cycle(s) | "
        f"IOC feeds every {args.feeds_every} cycle(s) | database '{config.DB_NAME}'")
    say("Press Ctrl+C to stop.")

    n = 0
    next_run = time.monotonic()
    while not STOP.is_set():
        n += 1
        if ping():
            try:
                db = get_db()
                if n == 1:
                    ensure_indexes(db)
                    ingest.seed_catalog(db)      # makes sure the sources exist even on a fresh database
                run_cycle(db, n, args.gcve_every, args.feeds_every, args.quiet)
            except Exception as exc:
                say(f"cycle {n} failed: {type(exc).__name__}: {exc}")
        else:
            say(f"MongoDB not reachable at {config.MONGO_URI} - will retry next cycle")
        if args.once:
            break
        # fixed schedule (no drift); if a cycle ran longer than the interval, skip the missed slots
        next_run += args.interval
        now = time.monotonic()
        if next_run <= now:
            next_run = now + args.interval - ((now - next_run) % args.interval)
        wait = next_run - now
        say(f"next update at {(datetime.now() + timedelta(seconds=wait)).strftime('%H:%M:%S')}")
        STOP.wait(max(0, next_run - time.monotonic()))
    say("Auto-updater stopped.")


if __name__ == "__main__":
    main()

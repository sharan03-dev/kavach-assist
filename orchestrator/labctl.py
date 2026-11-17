#!/usr/bin/env python3
"""labctl - starts a lab AND everything it depends on (mentor's rule).

    python orchestrator/labctl.py run 18          # Lab 7 -> ... -> 17 -> 18 (everything)
    python orchestrator/labctl.py run 17          # Lab 7 -> ... -> 16 -> 17
    python orchestrator/labctl.py run 16          # Lab 7 -> ... -> 15 -> 16
    python orchestrator/labctl.py run 15          # Lab 7 -> ... -> 14 -> 15
    python orchestrator/labctl.py run 14          # Lab 7 -> 8 -> ... -> 13 -> 14 (14 in full mode, the rest quick)
    python orchestrator/labctl.py run 13          # Lab 7 -> 8 -> ... -> 12 -> 13 (13 in full mode, the rest quick)
    python orchestrator/labctl.py run 12          # Lab 7 -> 8 -> ... -> 11 -> 12 (12 in full mode, the rest quick)
    python orchestrator/labctl.py run 11          # Lab 7 -> 8 -> 9 -> 10 -> 11 (11 in full mode, the rest quick)
    python orchestrator/labctl.py run 10          # Lab 7 -> 8 -> 9 -> 10 (10 in full mode, the rest quick)
    python orchestrator/labctl.py run 8           # Lab 7 -> 8
    python orchestrator/labctl.py run 9 --full    # ... 9 in full mode (includes the network partition)
    python orchestrator/labctl.py run 11 --full-all   # every lab in full mode (about 17 minutes)
    python orchestrator/labctl.py activate 9      # only this lab (what the Docker container of the lab runs)
    python orchestrator/labctl.py status          # chain status table
    python orchestrator/labctl.py plan 10         # show the order without running anything

quick = fast proof that the lab works (seconds).   full = the complete lab with all activities and reports.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import chain, config  # noqa: E402
from backend.app.db import ensure_indexes, get_db, ping  # noqa: E402

UTC = timezone.utc
PY = sys.executable
RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
RESULTS.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("RESULTS_DIR", str(RESULTS))


class Ctx:
    def __init__(self, lab: int):
        self.lab, self.db, self.steps = lab, get_db(), []

    def say(self, msg: str = ""):
        print(msg, flush=True)
        chain.log(self.db, self.lab, msg)

    def step(self, name: str, fn):
        """Run fn() as a named step; record status + seconds on the lab document."""
        t0 = time.perf_counter()
        self.say(f"\n--- {name}")
        rec = {"name": name, "status": "running", "seconds": None}
        self.steps.append(rec)
        chain.set_status(self.db, self.lab, steps=self.steps)
        try:
            out = fn()
            rec["status"] = "ok" if out is not False else "failed"
        except Exception as exc:                          # noqa: BLE001
            rec["status"], rec["error"] = "failed", f"{type(exc).__name__}: {exc}"
            self.say(f"!! {rec['error']}")
        rec["seconds"] = chain.elapsed(t0)
        chain.set_status(self.db, self.lab, steps=self.steps)
        return rec["status"] == "ok"

    def sh(self, cmd: list[str], env: dict | None = None, cwd: Path = ROOT, timeout: int = 1800) -> bool:
        self.say("$ " + " ".join(str(c) for c in cmd))
        p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env={**os.environ, **(env or {})}, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
        t0 = time.time()
        for line in p.stdout:                             # type: ignore[union-attr]
            self.say(line.rstrip("\n"))
            if time.time() - t0 > timeout:
                p.kill()
                self.say("!! timeout")
                return False
        return p.wait() == 0


# ----------------------------------------------------------------------------- helpers
def wait_mongo(ctx: Ctx, seconds: int = 180) -> bool:
    t0 = time.time()
    while time.time() - t0 < seconds:
        if ping():
            ctx.say(f"MongoDB reachable at {config.MONGO_URI}")
            return True
        time.sleep(2)
    ctx.say("!! MongoDB is not reachable")
    return False


def rs_ready(ctx: Ctx) -> bool:
    """Replica set healthy: needed by Labs 8, 9, 10 (transactions need a replica set)."""
    r = ctx.sh([PY, ROOT / "labs/lab9/setup/rsctl.py", "init"])
    return r and ctx.sh([PY, ROOT / "labs/lab9/setup/rsctl.py", "wait-healthy", "--timeout", "120"])


def updater_running() -> bool:
    pid = Path("/tmp/auto_update.pid")
    try:
        os.kill(int(pid.read_text()), 0)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def start_updater(ctx: Ctx):
    """Mentor: keep MongoDB data fresh every 5 minutes (backend/scripts/auto_update.py)."""
    if os.getenv("START_UPDATER", "1") != "1":
        ctx.say("auto-updater disabled (START_UPDATER=0)")
        return True
    if updater_running():
        ctx.say("auto-updater already running (every %s s)" % os.getenv("AUTO_UPDATE_INTERVAL", "300"))
        return True
    log = open(RESULTS / "auto_update.log", "a")
    p = subprocess.Popen([PY, ROOT / "backend/scripts/auto_update.py", "--quiet"], cwd=ROOT, stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True)
    Path("/tmp/auto_update.pid").write_text(str(p.pid))
    ctx.say(f"auto-updater started (pid {p.pid}) - a heartbeat document is written to ingest_runs every 5 minutes")
    return True


# ----------------------------------------------------------------------------- the four labs
def activate_7(ctx: Ctx, mode: str) -> bool:
    ok = ctx.step("MongoDB reachable", lambda: wait_mongo(ctx))
    ok &= ctx.step("Replica set rs0 healthy (primary + 2 secondaries)", lambda: rs_ready(ctx)) \
        if "replicaSet" in config.MONGO_URI else True
    ok &= ctx.step("Indexes (text, TTL, capped collection, CIDR range)", lambda: ensure_indexes(get_db()))

    def seed():
        db = get_db()
        if db.reports.estimated_document_count() > 0:
            ctx.say(f"database already built: {db.reports.estimated_document_count():,} reports, "
                    f"{db.indicators.estimated_document_count():,} indicators")
            return True
        ctx.say("database empty - building it (1-3 minutes, needs internet; falls back to sample data)")
        if ctx.sh([PY, ROOT / "backend/scripts/setup_database.py"]):
            return True
        return ctx.sh([PY, ROOT / "backend/scripts/setup_database.py", "--offline"])
    ok &= ctx.step("CTI database built (reports, IOCs, vulnerabilities)", seed)

    def lab71():
        from backend.app import labs
        db = get_db()
        res = labs.run_activity_71(db, 150, True)
        ctx.say("Activity 7.1 embedded redesign done: " + str(res)[:300])
        return True
    ok &= ctx.step("Lab 7.1 schema analysis + embedded redesign", lab71)
    ok &= ctx.step("Auto-updater (refresh MongoDB every 5 minutes)", lambda: start_updater(ctx))
    if mode == "full":
        ok &= ctx.step("Lab 7.2 working-set analysis (100,000 documents)",
                       lambda: ctx.sh([PY, ROOT / "labs/lab7_2/lab_02_working_set_analysis.py"]))
    return bool(ok)


def activate_8(ctx: Ctx, mode: str) -> bool:
    d = ROOT / "labs/lab8"
    ok = ctx.step("Replica set rs0 healthy", lambda: rs_ready(ctx))
    ok &= ctx.step("Lab 8.1 insert with w:3 and read back from every secondary",
                   lambda: ctx.sh([PY, d / "Activity_8.1_Replica_Set/verify_replication.py"]))
    if mode == "full":
        ok &= ctx.step("Lab 8.2 failover (rs.stepDown while a monitor keeps reading)",
                       lambda: ctx.sh([PY, d / "Activity_8.2_Failover/failover_monitor.py",
                                       "--stepdown-after", "8", "--duration", "40"], cwd=d / "Activity_8.2_Failover"))
        if shutil.which("docker"):
            uri = config.MONGO_URI
            ok &= ctx.step("Lab 8.2 activity: priorities 3 / 1 / 2 and predicted election",
                           lambda: ctx.sh(["sh", "-c", f'docker exec -i mongo1 mongosh "{uri}" --quiet < '
                                           f'{d}/Activity_8.2_TryItYourself_Priority/set_priorities.js'])
                           and ctx.sh([PY, d / "Activity_8.2_TryItYourself_Priority/predict_election.py",
                                       "--watch-takeover"]))
        else:
            ctx.say("docker CLI not available here - skipping the priority activity (run it from the host, see README)")
    return bool(ok)


def activate_9(ctx: Ctx, mode: str) -> bool:
    d = ROOT / "labs/lab9"
    ok = ctx.step("Replica set healthy, priority-2 member is PRIMARY",
                  lambda: ctx.sh([PY, d / "setup/rsctl.py", "wait-healthy", "--timeout", "120", "--preferred-primary"])
                  or ctx.sh([PY, d / "setup/rsctl.py", "wait-healthy", "--timeout", "60"]))
    if mode == "full" and shutil.which("docker"):
        url = os.getenv("DOCKER_URL", "")
        ok &= ctx.step("Lab 9 complete: 9.1, journaling activity, 9.2 network partition, report",
                       lambda: ctx.sh(["bash", d / "lab9", "all"] + ([url] if url else []), timeout=1500))
    else:
        if mode == "full":
            ctx.say("docker CLI not available here - running the benchmarks only (partition needs docker)")
        n = ["--docs", "1000", "--rounds", "5"] if mode == "full" else ["--docs", "200", "--rounds", "3", "--warmup", "20"]
        ok &= ctx.step("Lab 9.1 write concern latency (w:1, w:majority, w:all)",
                       lambda: ctx.sh([PY, d / "Activity_9.1_Write_Concern_Latency/write_concern_latency.py"] + n))
    return bool(ok)


def activate_10(ctx: Ctx, mode: str) -> bool:
    ok = ctx.step("Replica set healthy (transactions need one)", lambda: rs_ready(ctx))
    args = ["--report"] + (["--quick"] if mode == "quick" else [])
    ok &= ctx.step("Lab 10.1 + 10.2 transactions, concurrency test, throughput, report",
                   lambda: ctx.sh([PY, ROOT / "labs/lab10/run_lab10.py"] + args))
    return bool(ok)


def activate_11(ctx: Ctx, mode: str) -> bool:
    """Lab 11 runs on its own sharded cluster (mongos + config server replica set + shardA + shardB)."""
    d = ROOT / "labs/lab11"
    ok = ctx.step("Sharded cluster ready (config servers, shardA, shardB, mongos)",
                  lambda: ctx.sh([PY, d / "setup/clusterctl.py", "init", "--timeout", "180"]))
    args = ["--report"] + (["--quick"] if mode == "quick" else [])
    ok &= ctx.step("Lab 11.1 shard keys + 11.2 hotspot + resharding activity + report",
                   lambda: ctx.sh([PY, d / "run_lab11.py"] + args, timeout=1200))
    return bool(ok)


def activate_12(ctx: Ctx, mode: str) -> bool:
    """Lab 12 runs on its own hardened server mongo-secure (auth + keyFile + requireTLS + client certificates)."""
    d = ROOT / "labs/lab12"

    def ready():
        from backend.app import lab12
        t0 = time.time()
        while time.time() - t0 < 180:
            av = lab12.available()
            if av["available"]:
                st = lab12.status()
                ctx.say(f"mongo-secure {st['version']} at {st['host']}: replica set {st['set']}, tlsMode {st['tls_mode']}, "
                        f"client certificates required: {st['client_certs_required']}, mechanisms {st['mechanisms']}")
                return True
            time.sleep(3)
        ctx.say("!! " + av["reason"])
        return False
    ok = ctx.step("Secure server ready (auth + TLS + client certificates)", ready)
    args = [] if mode == "full" else ["--quick"]
    ok &= ctx.step("Lab 12.1 RBAC + redaction activity + 12.2 TLS + Lab 12 on Lab 7 (Kavach) + report",
                   lambda: ctx.sh([PY, d / "run_lab12.py", "--report"] + args, timeout=900))
    return bool(ok)


def activate_13(ctx: Ctx, mode: str) -> bool:
    """Lab 13: production replica set in Docker Compose (13.1 + exporter activity), Kubernetes StatefulSet (13.2),
    Lab 10 billing through failovers (13 on 10) and the Atlas/Terraform review. Each part is a step of its own."""
    d = ROOT / "labs/lab13"
    from backend.app import lab13

    def prod_ready():
        try:
            lab13.secrets()
        except RuntimeError as exc:
            ctx.say("!! " + str(exc))
            return False
        return lab13.wait_containers(log=ctx.say)
    ok = ctx.step("Production containers healthy (keyfile made, 3 x mongod --replSet rs0 --keyFile)", prod_ready)
    if not ok:
        return False
    args = ["--report"] + ([] if mode == "full" else ["--quick"])
    ok &= ctx.step("Lab 13.1 Compose replica set + failover, activity (exporter sidecar), 13.2 Kubernetes, 13 on 10, "
                   "cloud + report", lambda: ctx.sh([PY, d / "run_lab13.py"] + args, timeout=2400))
    return bool(ok)


def daemon_running() -> bool:
    try:
        os.kill(int(Path("/tmp/lab14_daemon.pid").read_text()), 0)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def activate_14(ctx: Ctx, mode: str) -> bool:
    """Lab 14: monitoring + alerting + backups of the Lab 13 production set, and recovery of the Lab 10 billing ledger."""
    d = ROOT / "labs/lab14"
    from backend.app import lab13, lab14

    def ready():
        av = lab13.available()
        if not av["available"]:
            ctx.say("!! " + av["reason"])
            return False
        lab14.ensure_users(log=ctx.say)
        return True
    ok = ctx.step("Production set reachable + least-privilege users monitor_app / backup_app", ready)
    if not ok:
        return False

    def daemon():
        if daemon_running():
            ctx.say("lab14 daemon already running")
            return True
        log = open(RESULTS / "lab14_daemon.log", "a")
        p = subprocess.Popen([PY, d / "ops_daemon.py"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        Path("/tmp/lab14_daemon.pid").write_text(str(p.pid))
        ctx.say(f"lab14 daemon started (pid {p.pid}): metrics every {lab14.INTERVAL} s -> results/metrics.log, backups every "
                f"{lab14.BACKUP_EVERY_MIN} min -> {lab14.BACKUP_DIR} (7-day retention)")
        return True
    ok &= ctx.step("Operations daemon (schedule: monitor every 30 s, backup every hour)", daemon)
    args = ["--report"] + ([] if mode == "full" else ["--quick"])
    ok &= ctx.step("Lab 14.1 dashboard + webhook drills, 14.2 backups / verification / PITR / benchmark, 14 on 13 billing DR, "
                   "report", lambda: ctx.sh([PY, d / "run_lab14.py"] + args, timeout=1800))
    return bool(ok)


def activate_15(ctx: Ctx, mode: str) -> bool:
    """Lab 15: the TaskTracker SaaS on the production set (Lab 13) - user, schema, demo tenants, then every Lab 15 test."""
    d = ROOT / "labs/lab15"
    from backend.app import lab13, lab15

    def ready():
        av = lab13.available()
        if not av["available"]:
            ctx.say("!! " + av["reason"])
            return False
        lab15.ensure_user(log=ctx.say)
        lab15.seed_demo(log=ctx.say)
        return True
    ok = ctx.step("TaskTracker on the production set: user tasktracker_app, schema, indexes, demo tenants", ready)
    if not ok:
        return False
    args = ["--report"] + ([] if mode == "full" else ["--quick"])
    ok &= ctx.step("Lab 15.1 API + isolation + rate limiting + RBAC, 15.2 migration, applied to 14..7, report",
                   lambda: ctx.sh([PY, d / "run_lab15.py"] + args, timeout=1800))
    return bool(ok)


def activate_16(ctx: Ctx, mode: str) -> bool:
    """Lab 16: Cassandra + Neo4j next to MongoDB - 16.1 three models, activity (hybrid), 16.2 FraudQueryService, 16 on 10."""
    from backend.app import lab16, polyglot as PG

    def ready():
        PG.wait_for(("mongodb", "cassandra", "neo4j"), 420, log=ctx.say)
        lab16.ensure_schema(log=ctx.say)
        return True
    ok = ctx.step("Cassandra + Neo4j reachable, keyspace lab16 + graph constraints", ready)
    if not ok:
        return False
    args = ["--report"] + ([] if mode == "full" else ["--quick"])
    ok &= ctx.step("Lab 16.1 three data models, activity (hybrid architecture), 16.2 FraudQueryService + tests, applied to Lab 10, report",
                   lambda: ctx.sh([PY, ROOT / "labs/lab16/run_lab16.py"] + args, timeout=1800))
    return bool(ok)


def activate_17(ctx: Ctx, mode: str) -> bool:
    """Lab 17: vector search + RAG on MongoDB Atlas Local (mongod + mongot)."""
    from backend.app import ai, lab17, polyglot as PG

    def ready():
        try:
            PG.wait_for(("vector",), 420, log=ctx.say)
        except RuntimeError as exc:
            ctx.say(f"!! {exc} - Lab 17 will use exact NumPy search instead of $vectorSearch")
        s = lab17.search_ok()
        info = ai.embedder_info()
        ctx.say(f"   search engine (mongot): {'yes' if s['ok'] else 'NO - ' + str(s.get('error'))[:120]}; embeddings: {info['provider']} "
                f"{info['model']} ({info['dims']} dimensions); generator: {ai.generator_info()['provider']}")
        return True
    ok = ctx.step("MongoDB Atlas Local (vector search) reachable + embedding model loaded", ready)
    args = ["--report"] + ([] if mode == "full" else ["--quick"])
    ok &= ctx.step("Lab 17.1 RAG pipeline, activity (conversation memory), 17.2 HNSW vs IVF, Ask Kavach (17 on 7), report",
                   lambda: ctx.sh([PY, ROOT / "labs/lab17/run_lab17.py"] + args, timeout=2400))
    return bool(ok)


def activate_18(ctx: Ctx, mode: str) -> bool:
    """Lab 18: the comparative capstone on 100,000 threats (10,000 in quick mode)."""
    from backend.app import polyglot as PG
    ok = ctx.step("MongoDB, Cassandra and Neo4j reachable", lambda: bool(PG.wait_for(("mongodb", "cassandra", "neo4j"), 420, log=ctx.say)))
    if not ok:
        return False
    args = ["--report"] + ([] if mode == "full" else ["--quick"])
    ok &= ctx.step("Lab 18.1 load + 4 benchmarks x 10 runs + false positives + communities, 18.2 comparative report",
                   lambda: ctx.sh([PY, ROOT / "labs/lab18/run_lab18.py"] + args, timeout=3600))
    return bool(ok)


LABS = {7: activate_7, 8: activate_8, 9: activate_9, 10: activate_10, 11: activate_11, 12: activate_12, 13: activate_13,
        14: activate_14, 15: activate_15, 16: activate_16, 17: activate_17, 18: activate_18}
TOP_LABS = (10, 11, 12, 13, 14, 15, 16, 17, 18)           # the lab you start always runs in full mode when it is one of these


LOCK_ID = "activation_lock"
LOCK_STALE_S = 90 * 60                                    # a lock older than this is from a crashed run


def acquire_lock(lab: int) -> str:
    """Only one lab may run at a time (Lab 13 crashes the production set on purpose; a parallel lab would fail)."""
    from pymongo.errors import DuplicateKeyError, PyMongoError
    me = f"lab{lab}-{os.getpid()}-{os.uname().nodename}"
    said = False
    while True:
        try:
            col = get_db().locks
            col.delete_one({"_id": LOCK_ID, "at": {"$lt": datetime.now(UTC).timestamp() - LOCK_STALE_S}})
            col.insert_one({"_id": LOCK_ID, "holder": me, "lab": lab, "at": datetime.now(UTC).timestamp()})
            return me
        except DuplicateKeyError:
            if not said:
                cur = get_db().locks.find_one({"_id": LOCK_ID}) or {}
                print(f"[lab{lab}] waiting: Lab {cur.get('lab')} is running (one lab at a time)", flush=True)
                said = True
        except PyMongoError:
            pass
        time.sleep(5)


def release_lock(me: str):
    try:
        get_db().locks.delete_one({"_id": LOCK_ID, "holder": me})
    except Exception:                                     # noqa: BLE001
        pass


def activate(lab: int, mode: str) -> bool:
    me = acquire_lock(lab)
    try:
        return _activate(lab, mode)
    finally:
        release_lock(me)


def _activate(lab: int, mode: str) -> bool:
    ctx = Ctx(lab)
    chain.set_status(ctx.db, lab, status="running", mode=mode, started_at=datetime.now(UTC), steps=[], error=None)
    ctx.say(f"\n{'=' * 72}\nACTIVATING {chain.TITLES[lab]}   [mode: {mode}]\n{'=' * 72}")
    t0 = time.perf_counter()
    try:
        ok = LABS[lab](ctx, mode)
    except Exception as exc:                              # noqa: BLE001
        ok = False
        ctx.say(f"!! {type(exc).__name__}: {exc}")
    chain.set_status(ctx.db, lab, status="ok" if ok else "failed", finished_at=datetime.now(UTC),
                     seconds=chain.elapsed(t0), steps=ctx.steps)
    ctx.say(f"{'OK' if ok else 'FAILED'}: Lab {lab} ({chain.elapsed(t0)} s)")
    return ok


def run_chain(target: int, full: bool = False, full_all: bool = False) -> bool:
    order = chain.chain_for(target)
    mode_of = {lab: ("full" if full_all or (full and lab == target) or (lab == target and target in TOP_LABS) else "quick")
               for lab in order}
    if not ping():
        wait_mongo(Ctx(target))
    db = get_db()
    chain.reset_for_run(db, order, target, mode_of)
    chain.log(db, target, f"Starting Lab {target}: order = " + " -> ".join(f"Lab {n}" for n in order))
    for lab in order:
        if not activate(lab, mode_of[lab]):
            for rest in order[order.index(lab) + 1:]:
                chain.set_status(db, rest, status="skipped", error=f"Lab {lab} failed")
            return False
    chain.log(db, target, f"All {len(order)} labs active.")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "activate", "plan"):
        p = sub.add_parser(name)
        p.add_argument("lab", type=int, choices=sorted(LABS))
        p.add_argument("--full", action="store_true", help="full mode for the requested lab")
        p.add_argument("--full-all", action="store_true", help="full mode for every lab in the chain")
        p.add_argument("--mode", choices=["quick", "full"], help="(activate) explicit mode")
    sub.add_parser("status")
    a = ap.parse_args()

    if a.cmd == "plan":
        print(" -> ".join(f"Lab {n}" for n in chain.chain_for(a.lab)))
    elif a.cmd == "status":
        snap = chain.snapshot(get_db(), tail=0)
        print(f"{'LAB':<8}{'STATUS':<10}{'MODE':<8}{'SECONDS':<9}REQUIRES")
        for d in snap["labs"]:
            print(f"Lab {d['lab']:<4}{d.get('status', 'idle'):<10}{d.get('mode', '-'):<8}{str(d.get('seconds', '-')):<9}"
                  f"{'Lab ' + str(d['requires']) if d['requires'] else '-'}")
    elif a.cmd == "run":
        sys.exit(0 if run_chain(a.lab, a.full, a.full_all) else 1)
    elif a.cmd == "activate":
        mode = a.mode or ("full" if a.full else os.getenv("LAB_MODE", "quick"))
        sys.exit(0 if activate(a.lab, mode) else 1)


if __name__ == "__main__":
    main()

"""Lab 13 - Deploying MongoDB with containers and the cloud - and applied to Lab 10 (mentor's rule).

    Lab 13.1   production Docker Compose: 3-member replica set rs0 (prod-mongo-1/2/3) with keyFile authentication,
               health checks, restart policies, resource limits, log rotation; keyfile made with
               `openssl rand -base64 756` + chmod 400 (owner 999 = mongodb); rs.initiate() through the localhost
               exception; verification (keyfile identical on every member, auth enforced, 1 PRIMARY + 2 SECONDARY,
               a member with a different keyfile cannot join); PyMongo failover: rs.stepDown() and a real crash
               (SIGKILL of mongod -> Docker restarts the container) while a script reads and writes continuously
    Activity   Prometheus exporter SIDECAR for prod-mongo-1 (shares its network namespace) + Prometheus + alert rules
    Lab 13.2   Kubernetes (k3s running inside Docker): headless Service, StatefulSet with volumeClaimTemplates,
               InitContainer for the keyfile, readiness probe on 27017; rs.initiate from mongo-0; delete mongo-2 and
               watch it come back with its data; delete the PRIMARY pod while a client pod runs transactions;
               the classroom's per-pod keyfile generation tested as written (it fails: every pod gets another key)
    13 on 10   the Lab 10 classifier billing (Rs 100 / month, 3 free uses) copied to the production replica set:
               the Lab 10 ACID checks, then customer journeys running THROUGH a step-down and a crash - every
               rupee and every free trial is checked afterwards (no loss, no double charge)
    Cloud      the reference Terraform file for an Atlas M0 cluster reviewed, corrected and `terraform validate`d
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import secrets as _secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter, namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure, PyMongoError

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "labs" / "lab13"
PROD_RS = os.getenv("PROD_RS", "rs0")
PROD_HOSTS = [h.strip() for h in os.getenv("PROD_HOSTS", "prod-mongo-1:27017,prod-mongo-2:27017,prod-mongo-3:27017").split(",")
              if h.strip()]
PROD_CONTAINERS = [c.strip() for c in os.getenv("PROD_CONTAINERS", "").split(",") if c.strip()] or \
    [h.rsplit(":", 1)[0] for h in PROD_HOSTS]
PROD_DIR = Path(os.getenv("PROD_DIR", "/prod"))
KUBE_DIR = Path(os.getenv("KUBE_DIR", "/kube"))
NETWORK = os.getenv("RS_NETWORK", "kavach-net")
EXPORTER_CONTAINER = os.getenv("EXPORTER_CONTAINER", "prod-mongo-1-exporter")
EXPORTER_URL = os.getenv("EXPORTER_URL", "http://prod-mongo-1:9216/metrics")
PROM_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
K8S_MODE = os.getenv("LAB13_K8S", "k3s").lower()
K3S = os.getenv("K3S_CONTAINER", "k3s")
K3S_HOST = os.getenv("K3S_HOST", "k3s")
NS, NS_BAD = "lab13", "lab13-classroom"
MONGO_IMAGE = os.getenv("LAB13_MONGO_IMAGE", "mongo:7.0")
REFERENCE_PASSWORD = "AdminPass123!"          # the hard-coded password of the reference compose healthcheck
HEAVY = threading.Lock()
R = namedtuple("R", "rc out err")
_clients: dict = {}
_lock = threading.Lock()


def _j(x):
    return json.loads(json.dumps(x, default=str))


def now_iso():
    return datetime.now(UTC).isoformat()


# ============================================================================ processes (docker / kubectl)
def sh(cmd, input: str | None = None, timeout: int = 120) -> R:
    try:
        p = subprocess.run([str(c) for c in cmd], input=input, capture_output=True, text=True, timeout=timeout)
        return R(p.returncode, p.stdout or "", p.stderr or "")
    except FileNotFoundError as exc:
        return R(127, "", str(exc))
    except subprocess.TimeoutExpired:
        return R(124, "", f"timeout after {timeout} s")


def docker(*args, input: str | None = None, timeout: int = 120) -> R:
    return sh(["docker", *args], input=input, timeout=timeout)


def inspect(name: str) -> dict | None:
    r = docker("inspect", name, timeout=20)
    if r.rc != 0:
        return None
    try:
        return json.loads(r.out)[0]
    except (ValueError, IndexError):
        return None


def mongosh(container: str, js: str, *, user: str | None = None, password: str | None = None, timeout: int = 60) -> R:
    """mongosh INSIDE a member container (= localhost, so the localhost exception applies before any user exists)."""
    cmd = ["exec", container, "mongosh", "--quiet", "--port", "27017"]
    if user:
        cmd += ["-u", user, "-p", password or "", "--authenticationDatabase", "admin"]
    return docker(*cmd, "--eval", js, timeout=timeout)


def _last_json(text: str):
    for ln in reversed([x for x in text.splitlines() if x.strip()]):
        try:
            return json.loads(ln)
        except ValueError:
            continue
    return None


def _ts(s: str | None) -> float | None:
    """Docker timestamp (RFC 3339 with nanoseconds) -> epoch seconds."""
    if not s or s.startswith("0001"):
        return None
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?", s)
    if not m:
        return None
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC).timestamp()
    return base + (float("0" + m.group(2)[:7]) if m.group(2) else 0.0)


# ============================================================================ secrets + connections
def secrets() -> dict:
    p = PROD_DIR / "secrets.json"
    if not p.exists():
        raise RuntimeError(f"{p} not found - the lab13-keyfile container creates it (docker compose up -d lab13)")
    return json.loads(p.read_text())


def container_of(host: str | None) -> str | None:
    if host in PROD_HOSTS:
        return PROD_CONTAINERS[PROD_HOSTS.index(host)]
    return None


def new_client(user: str | None = "admin", *, sst: int = 4000, direct: str | None = None, password: str | None = None,
               **kw) -> MongoClient:
    opts = dict(serverSelectionTimeoutMS=sst, connectTimeoutMS=2500, socketTimeoutMS=20000, tz_aware=True,
                appname=f"lab13-{user or 'anon'}", retryWrites=True, retryReads=True)
    if direct:
        opts.update(host=direct, directConnection=True)
    else:
        opts.update(host=PROD_HOSTS, replicaSet=PROD_RS)
    if user:
        opts.update(username=user, password=password if password is not None else secrets()[user], authSource="admin")
    opts.update(kw)
    return MongoClient(**opts)


def client(user: str = "admin") -> MongoClient:
    with _lock:
        c = _clients.get(user)
        if c is None:
            c = _clients[user] = new_client(user, sst=5000, w="majority")
        return c


def reset_clients():
    with _lock:
        for c in _clients.values():
            try:
                c.close()
            except Exception:                          # noqa: BLE001
                pass
        _clients.clear()


_direct: dict = {}


def hello(host: str) -> dict:
    """Unauthenticated `hello` straight to one member (allowed before log-in): state without needing a password."""
    c = _direct.get(host)
    if c is None:
        c = _direct[host] = MongoClient(host, directConnection=True, serverSelectionTimeoutMS=1200, connectTimeoutMS=1000,
                                        socketTimeoutMS=2000, appname="lab13-probe")
    try:
        h = c.admin.command("hello")
        return {"up": True, "set": h.get("setName"), "primary": bool(h.get("isWritablePrimary")),
                "secondary": bool(h.get("secondary")), "me": h.get("me"), "seen_primary": h.get("primary")}
    except PyMongoError as exc:
        return {"up": False, "error": type(exc).__name__}


def hello_all() -> dict:
    with ThreadPoolExecutor(len(PROD_HOSTS)) as ex:
        return dict(zip(PROD_HOSTS, ex.map(hello, PROD_HOSTS)))


def primary_host(h: dict | None = None) -> str | None:
    h = h or hello_all()
    return next((k for k, v in h.items() if v.get("primary")), None)


def available() -> dict:
    try:
        secrets()
    except RuntimeError as exc:
        return {"available": False, "reason": str(exc)}
    try:
        client().admin.command("ping")
        return {"available": True}
    except Exception as exc:                          # noqa: BLE001
        reset_clients()
        return {"available": False, "reason": f"production replica set not ready ({str(exc)[:140]})"}


def rs_summary(c: MongoClient | None = None) -> dict:
    st = (c or client()).admin.command("replSetGetStatus")
    prim = next((m for m in st["members"] if m["stateStr"] == "PRIMARY"), None)
    out = []
    for m in st["members"]:
        lag = None
        if prim and m.get("optimeDate") and prim.get("optimeDate"):
            lag = round((prim["optimeDate"] - m["optimeDate"]).total_seconds(), 1)
        out.append({"name": m["name"], "state": m["stateStr"], "health": m.get("health"), "uptime": m.get("uptime"),
                    "lag_s": lag, "sync_from": m.get("syncSourceHost") or "", "ping_ms": m.get("pingMs"),
                    "last_heartbeat_message": m.get("lastHeartbeatMessage", "")})
    ecm = st.get("electionCandidateMetrics") or {}
    return {"set": st["set"], "term": st.get("term"), "members": out, "primary": prim["name"] if prim else None,
            "majority": st.get("majorityVoteCount"), "last_election_reason": ecm.get("lastElectionReason"),
            "last_election": ecm.get("lastElectionDate")}


# ============================================================================ Lab 13.1 - deploy + initiate
def _healthy(cname: str) -> str:
    i = inspect(cname)
    if not i:
        return "missing"
    st = i.get("State", {})
    return (st.get("Health") or {}).get("Status") or st.get("Status", "?")


def wait_containers(log=print, timeout: int = 240) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        states = {c: _healthy(c) for c in PROD_CONTAINERS}
        if all(s == "healthy" for s in states.values()):
            log("   containers healthy: " + ", ".join(f"{c} {s}" for c, s in states.items()))
            return True
        time.sleep(3)
    log("!! containers not healthy: " + ", ".join(f"{c} {s}" for c, s in states.items()))
    return False


def _can_login(user: str = "admin") -> bool:
    try:
        c = new_client(user, sst=3000)
        c.admin.command("ping")
        c.close()
        return True
    except PyMongoError:
        return False


def initiate(log=print) -> dict:
    """rs.initiate() + first admin through the localhost exception (mongosh inside prod-mongo-1), then the service users."""
    out = {"steps": []}
    sec = secrets()
    c0 = PROD_CONTAINERS[0]
    r = mongosh(c0, "const h = db.hello(); print(JSON.stringify({set: h.setName || null, primary: !!h.isWritablePrimary}))")
    h0 = _last_json(r.out) or {}
    cfg = {"_id": PROD_RS, "members": [{"_id": i, "host": h} for i, h in enumerate(PROD_HOSTS)]}
    if not h0.get("set"):
        js = f"printjson(rs.initiate({json.dumps(cfg)}))"
        log(f"   $ docker exec {c0} mongosh --eval 'rs.initiate({json.dumps(cfg)})'   # localhost exception: no user yet")
        r = mongosh(c0, js, timeout=60)
        ok = '"ok": 1' in r.out.replace("ok: 1", '"ok": 1')
        out["initiate"] = {"ok": ok, "output": (r.out + r.err).strip()[-400:]}
        log("   " + ("rs.initiate -> { ok: 1 }" if ok else "!! " + (r.out + r.err).strip()[-300:]))
    else:
        out["initiate"] = {"ok": True, "already": True}
        log(f"   replica set {h0['set']} already initiated")
    t0 = time.time()
    prim = None
    while time.time() - t0 < 90:
        prim = primary_host()
        if prim:
            break
        time.sleep(1)
    if not prim:
        raise RuntimeError("no PRIMARY was elected within 90 s")
    out["primary"], out["election_s"] = prim, round(time.time() - t0, 1)
    log(f"   PRIMARY: {prim} (after {out['election_s']} s)")
    if not _can_login("admin"):
        pc = container_of(prim)
        js = (f"db.getSiblingDB('admin').createUser({{user: 'admin', pwd: {json.dumps(sec['admin'])}, "
              f"roles: [{{role: 'root', db: 'admin'}}], mechanisms: ['SCRAM-SHA-256']}}); print('created')")
        r = mongosh(pc, js)
        log(f"   $ docker exec {pc} mongosh --eval \"db.getSiblingDB('admin').createUser({{user:'admin', pwd:'***', roles:['root']}})\" "
            f"-> {'created' if 'created' in r.out else (r.out + r.err).strip()[-200:]}")
        t1 = time.time()
        while not _can_login("admin") and time.time() - t1 < 30:
            time.sleep(1)
    out["admin_login"] = _can_login("admin")
    adm = client("admin")
    roles = {r_["role"] for r_ in adm.admin.command("rolesInfo", 1)["roles"]}
    priv = [{"resource": {"cluster": True}, "actions": ["replSetGetConfig", "replSetGetStatus"]}]
    if "billingRole" not in roles:
        adm.admin.command("createRole", "billingRole", privileges=priv, roles=[{"role": "readWrite", "db": "cti_billing"}])
    users = {
        "lab_app": ([{"role": "readWrite", "db": "lab13"}], "failover reader/writer (Lab 13.1 step 4)"),
        "billing_app": ([{"role": "billingRole", "db": "admin"}], "Lab 10 classifier billing on the production set"),
        "exporter": ([{"role": "clusterMonitor", "db": "admin"}, {"role": "read", "db": "local"}],
                     "Prometheus exporter sidecar (activity)"),
    }
    have = {u["user"] for u in adm.admin.command("usersInfo")["users"]}
    for u, (rl, _) in users.items():
        if u in have:
            adm.admin.command("updateUser", u, pwd=sec[u], roles=rl)
        else:
            adm.admin.command("createUser", u, pwd=sec[u], roles=rl, mechanisms=["SCRAM-SHA-256"])
    out["users"] = [{"user": "admin", "roles": ["root"], "purpose": "first user, created through the localhost exception"}] + \
        [{"user": u, "roles": [x["role"] + "@" + x["db"] for x in rl], "purpose": p} for u, (rl, p) in users.items()]
    log("   users: " + ", ".join(f"{u['user']} ({', '.join(u['roles'])})" for u in out["users"]))
    out["rs"] = rs_summary(adm)
    return out


def verify_deployment(log=print) -> dict:
    """Step 3: every member healthy, keyFile identical and private, authentication enforced, 1 PRIMARY + 2 SECONDARY."""
    res = {"containers": [], "checks": {}}
    shas = []
    for cname, host in zip(PROD_CONTAINERS, PROD_HOSTS):
        i = inspect(cname) or {}
        hc, st, cfg = i.get("HostConfig", {}), i.get("State", {}), i.get("Config", {})
        stat = docker("exec", cname, "sh", "-c", "stat -c '%a %U:%G %s' /etc/mongodb/keyfile; sha256sum /etc/mongodb/keyfile",
                      timeout=20).out.split()
        perm = {"mode": stat[0], "owner": stat[1], "bytes": int(stat[2]), "sha256": stat[3]} if len(stat) >= 4 else {}
        shas.append(perm.get("sha256"))
        args = cfg.get("Cmd") or []
        res["containers"].append({
            "container": cname, "host": host, "image": cfg.get("Image"), "status": st.get("Status"),
            "health": (st.get("Health") or {}).get("Status"), "restart_policy": (hc.get("RestartPolicy") or {}).get("Name"),
            "restarts": i.get("RestartCount", 0), "init": hc.get("Init"), "memory_limit_mb": round((hc.get("Memory") or 0) / 2**20),
            "cpus": round((hc.get("NanoCpus") or 0) / 1e9, 2), "log": f"{(hc.get('LogConfig') or {}).get('Type')} "
            f"{json.dumps((hc.get('LogConfig') or {}).get('Config') or {})}",
            "command": " ".join(args), "keyfile": perm,
            "mounts": [f"{m.get('Name') or m.get('Source')} -> {m.get('Destination')} ({'rw' if m.get('RW') else 'ro'})"
                       for m in i.get("Mounts", [])],
            "ports": sorted({f"{v[0]['HostPort']}->{k}" for k, v in (i.get("NetworkSettings", {}).get("Ports") or {}).items() if v})})
    for c in res["containers"]:
        log(f"   {c['container']:<13} {c['status']}/{c['health']}  restart={c['restart_policy']}  mem={c['memory_limit_mb']} MB  "
            f"keyfile {c['keyfile'].get('mode')} {c['keyfile'].get('owner')} {c['keyfile'].get('bytes')} B sha256 "
            f"{(c['keyfile'].get('sha256') or '')[:12]}")
    # authentication is enforced: no credentials / a wrong password are refused
    try:
        nc = new_client(None, sst=4000)
        nc.lab13.probe.find_one()
        unauth = {"result": "allowed"}
    except OperationFailure as exc:
        unauth = {"result": "denied", "code": exc.code, "detail": str(exc)[:160]}
    except PyMongoError as exc:
        unauth = {"result": "error", "detail": str(exc)[:160]}
    try:
        new_client("admin", password="wrong-password", sst=4000).admin.command("ping")
        wrong = {"result": "allowed"}
    except OperationFailure as exc:
        wrong = {"result": "auth-failed", "code": exc.code, "detail": str(exc)[:160]}
    except PyMongoError as exc:
        wrong = {"result": "error", "detail": str(exc)[:160]}
    log(f"   find() without credentials -> {unauth['result']} (code {unauth.get('code')}); wrong password -> "
        f"{wrong['result']} (code {wrong.get('code')})")
    # the reference file's health check (admin / AdminPass123!) - measured
    r = mongosh(PROD_CONTAINERS[0], "db.runCommand({ping: 1}).ok", user="admin", password=REFERENCE_PASSWORD, timeout=30)
    ref = {"exit_code": r.rc, "output": (r.out + r.err).strip().splitlines()[-1:] or [""]}
    ref["output"] = ref["output"][0][:200]
    log(f"   reference health check (mongosh -u admin -p {REFERENCE_PASSWORD}) -> exit {r.rc}: {ref['output'][:110]}")
    rs = rs_summary()
    states = Counter(m["state"] for m in rs["members"])
    res.update({"unauthenticated": unauth, "wrong_password": wrong, "reference_healthcheck": ref, "rs": rs})
    ch = res["checks"]
    ch["all members healthy (Docker health check)"] = all(c["health"] == "healthy" for c in res["containers"])
    ch["restart policy unless-stopped on every member"] = all(c["restart_policy"] == "unless-stopped" for c in res["containers"])
    ch["keyfile mode 400, owner mongodb (999)"] = all(c["keyfile"].get("mode") == "400" and
                                                       c["keyfile"].get("owner", "").startswith(("mongodb", "999"))
                                                       for c in res["containers"])
    ch["the same keyfile on all three members"] = len(set(shas)) == 1 and shas[0] is not None
    ch["--replSet rs0 and --keyFile on every member"] = all("--replSet" in c["command"] and "--keyFile" in c["command"]
                                                             for c in res["containers"])
    ch["authentication enforced (no credentials -> code 13, wrong password -> code 18)"] = \
        unauth.get("code") == 13 and wrong.get("code") == 18
    ch["1 PRIMARY + 2 SECONDARY, all health 1"] = states.get("PRIMARY") == 1 and states.get("SECONDARY") == 2 and \
        all(m["health"] == 1 for m in rs["members"])
    ch["reference health check (hard-coded admin password) fails"] = ref["exit_code"] != 0
    res["pass"] = all(v for k, v in ch.items())
    for m in rs["members"]:
        log(f"   rs.status(): {m['name']:<20} {m['state']:<10} health {m['health']}  lag {m['lag_s']} s")
    return res


def rogue_member(log=print) -> dict:
    """A mongod with ANOTHER keyfile is added to rs0: it must not be able to join (keyFile = internal authentication)."""
    name = "prod-mongo-rogue"
    docker("rm", "-f", name, timeout=30)
    script = ("openssl rand -base64 756 > /tmp/rogue.key && chown 999:999 /tmp/rogue.key && chmod 400 /tmp/rogue.key && "
              "exec docker-entrypoint.sh mongod --replSet rs0 --bind_ip_all --keyFile /tmp/rogue.key --wiredTigerCacheSizeGB 0.25")
    r = docker("run", "-d", "--name", name, "--hostname", name, "--network", NETWORK, "--entrypoint", "bash", MONGO_IMAGE,
               "-c", script, timeout=120)
    if r.rc != 0:
        return {"ran": False, "detail": (r.out + r.err).strip()[-300:]}
    t_add = time.time()
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    host = f"{name}:27017"
    adm = client("admin")
    res = {"ran": True, "host": host}
    try:
        for _ in range(20):                                   # wait until the rogue mongod listens
            if hello(host).get("up"):
                break
            time.sleep(1)
        cfg = adm.admin.command("replSetGetConfig")["config"]
        cfg["version"] += 1
        cfg["members"].append({"_id": max(m["_id"] for m in cfg["members"]) + 1, "host": host, "priority": 0, "votes": 0})
        adm.admin.command("replSetReconfig", cfg)
        log(f"   rs.add({{host: '{host}', priority: 0, votes: 0}})  # its keyfile was generated separately")
        time.sleep(9)
        st = adm.admin.command("replSetGetStatus")
        m = next(x for x in st["members"] if x["name"] == host)
        res.update({"state": m["stateStr"], "health": m.get("health"), "heartbeat_message": m.get("lastHeartbeatMessage", "")})
        logs = ""
        for c in PROD_CONTAINERS + [name]:
            logs += docker("logs", "--since", since, c, timeout=20).out + docker("logs", "--since", since, c, timeout=20).err
        hits = [ln for ln in logs.splitlines() if "uthentication failed" in ln or "AuthenticationFailed" in ln]
        res["auth_failures_logged"] = len(hits)
        res["log_sample"] = (re.sub(r"\s+", " ", hits[0])[:260] if hits else "")
        log(f"   {host}: state {res['state']}, health {res['health']}; 'Authentication failed' log lines: {len(hits)}")
    finally:
        try:
            cfg = adm.admin.command("replSetGetConfig")["config"]
            if any(x["host"] == host for x in cfg["members"]):
                cfg["version"] += 1
                cfg["members"] = [x for x in cfg["members"] if x["host"] != host]
                adm.admin.command("replSetReconfig", cfg)
                log(f"   rs.remove('{host}')")
        except PyMongoError as exc:
            log(f"   (could not remove the rogue member: {exc})")
        docker("rm", "-f", name, timeout=30)
    res["seconds"] = round(time.time() - t_add, 1)
    res["pass"] = res.get("health") == 0 and res.get("state") != "SECONDARY"
    return res


# ============================================================================ Lab 13.1 step 4 - failover with PyMongo
CRASH_SCRIPT = ("for p in /proc/[0-9]*; do [ \"$(cat $p/comm 2>/dev/null)\" = mongod ] && kill -9 ${p#/proc/}; done; true")


def crash_member(cname: str) -> R:
    """SIGKILL of mongod inside the container (tini is PID 1, so the kill works and the container exits with 137)."""
    return docker("exec", cname, "bash", "-c", CRASH_SCRIPT, timeout=20)


def _state_of(cname: str) -> dict:
    i = inspect(cname) or {}
    st = i.get("State", {})
    return {"status": st.get("Status"), "running": st.get("Running"), "started": _ts(st.get("StartedAt")),
            "restarts": i.get("RestartCount", 0), "exit_code": st.get("ExitCode"), "health": (st.get("Health") or {}).get("Status")}


def failover(event: str = "stepdown", log=print, after_s: float = 5.0, min_after: float = 18.0, max_after: float = 120.0) -> dict:
    """PyMongo reads and writes continuously while the PRIMARY steps down (planned) or is killed (crash)."""
    assert event in ("stepdown", "crash")
    app = new_client("lab_app", sst=2000, socketTimeoutMS=4000, w="majority")
    coll = app.lab13.failover_probe
    coll.create_index("run")
    run = f"{event}-{int(time.time())}"
    stop = threading.Event()
    t0 = time.perf_counter()
    reads, writes, prim_seen, acked = [], [], [], []
    dedup = [0]

    def ms():
        return round((time.perf_counter() - t0) * 1000)

    def addr():
        try:
            p = app.primary
            return f"{p[0]}:{p[1]}" if p else None
        except Exception:                                  # noqa: BLE001
            return None

    def reader():
        while not stop.is_set():
            a = ms()
            try:
                coll.find_one({"run": run}, sort=[("_id", -1)])
                reads.append((a, ms(), True, addr()))
            except PyMongoError as exc:
                reads.append((a, ms(), False, type(exc).__name__))
            time.sleep(0.05)

    def writer():
        seq = 0
        while not stop.is_set():
            seq += 1
            _id = f"{run}-{seq:06d}"
            tries = 0
            while True:
                tries += 1
                a = ms()
                try:
                    coll.insert_one({"_id": _id, "run": run, "seq": seq, "at": datetime.now(UTC)})
                    writes.append((a, ms(), True, addr(), tries))
                    acked.append(_id)
                    break
                except DuplicateKeyError:                 # an earlier attempt had committed: the retry is de-duplicated
                    writes.append((a, ms(), True, "duplicate-key", tries))
                    acked.append(_id)
                    dedup[0] += 1
                    break
                except PyMongoError as exc:
                    writes.append((a, ms(), False, type(exc).__name__, tries))
                    if stop.is_set() and tries > 50:
                        return
                    time.sleep(0.1)
            time.sleep(0.05)

    old = primary_host()
    oldc = container_of(old)
    before = _state_of(oldc) if event == "crash" else {}
    ths = [threading.Thread(target=reader, daemon=True), threading.Thread(target=writer, daemon=True)]
    [t.start() for t in ths]
    log(f"   PyMongo (replicaSet=rs0, retryWrites=true, w=majority) reads + writes every 50 ms; PRIMARY = {old}")
    time.sleep(after_s)
    ev_ms = ms()
    if event == "stepdown":
        log(f"   t={ev_ms / 1000:.1f} s  rs.stepDown(20) on {old}")
        try:
            client("admin").admin.command("replSetStepDown", 20, secondaryCatchUpPeriodSecs=10)
        except PyMongoError as exc:
            log(f"   (stepDown returned {type(exc).__name__} - expected, the primary closes connections)")
    else:
        log(f"   t={ev_ms / 1000:.1f} s  CRASH: kill -9 mongod inside {oldc} (docker restart policy: unless-stopped)")
        crash_member(oldc)
    new_primary, new_at, back_running, back_secondary = None, None, None, None
    restarts_after = None
    while True:
        h = hello_all()
        p = primary_host(h)
        t = ms()
        prim_seen.append((t, p))
        if new_primary is None and p and (p != old or event == "crash"):
            if p != old or t - ev_ms > 3000:
                new_primary, new_at = p, t
        if event == "crash":
            s = _state_of(oldc)
            if back_running is None and s["running"] and s["started"] and before.get("started") and s["started"] > before["started"]:
                back_running, restarts_after = t, s["restarts"]
            if back_running and back_secondary is None and h.get(old, {}).get("secondary"):
                back_secondary = t
        el = (t - ev_ms) / 1000
        done = el >= min_after and new_primary and (event == "stepdown" or back_secondary)
        if done or el >= max_after:
            break
        time.sleep(0.5)
    time.sleep(1.5)
    stop.set()
    [t.join(timeout=15) for t in ths]
    present = {d["_id"] for d in coll.find({"run": run}, {"_id": 1})}
    ack = set(acked)
    lost, extra = sorted(ack - present), sorted(present - ack)

    def gap(rows):
        ok = sorted(r[1] for r in rows if r[2])
        best = (0, 0)
        for a, b in zip(ok, ok[1:]):
            if b - a > best[0]:
                best = (b - a, a)
        return best

    wg, rg = gap(writes), gap(reads)
    errs_w = Counter(w[3] for w in writes if not w[2])
    errs_r = Counter(r[3] for r in reads if not r[2])
    try:
        rs = rs_summary()
    except PyMongoError:
        rs = {}
    end = ms()
    buckets = []
    for b0 in range(0, end, 500):
        wb = [w for w in writes if b0 <= w[1] < b0 + 500]
        rb = [r for r in reads if b0 <= r[1] < b0 + 500]
        pb = [p for t_, p in prim_seen if b0 <= t_ < b0 + 500]
        buckets.append({"t": b0, "w_ok": sum(1 for w in wb if w[2]), "w_err": sum(1 for w in wb if not w[2]),
                        "r_ok": sum(1 for r in rb if r[2]), "r_err": sum(1 for r in rb if not r[2]),
                        "primary": pb[-1] if pb else None})
    out = {"event": event, "run": run, "old_primary": old, "new_primary": new_primary, "event_ms": ev_ms,
           "election_ms": (new_at - ev_ms) if new_at else None,
           "write_gap_ms": wg[0], "write_gap_from_ms": wg[1], "read_gap_ms": rg[0], "read_gap_from_ms": rg[1],
           "writes_ok": len(acked), "write_errors": dict(errs_w), "write_attempts": len(writes),
           "reads_ok": sum(1 for r in reads if r[2]), "read_errors": dict(errs_r), "deduplicated_retries": dedup[0],
           "acknowledged": len(ack), "stored": len(present), "lost_acknowledged_writes": len(lost), "unexpected_extra": len(extra),
           "last_election_reason": rs.get("last_election_reason"), "term": rs.get("term"), "rs_after": rs.get("members"),
           "timeline": buckets, "duration_ms": end}
    if event == "crash":
        out.update({"crashed_container": oldc, "container_running_again_ms": (back_running - ev_ms) if back_running else None,
                    "rejoined_secondary_ms": (back_secondary - ev_ms) if back_secondary else None,
                    "restart_count_before": before.get("restarts"), "restart_count_after": restarts_after})
    out["pass"] = bool(new_primary) and not lost and not extra and (event == "stepdown" or bool(back_secondary))
    log(f"   new PRIMARY {new_primary} after {out['election_ms']} ms ({out['last_election_reason']}); longest write pause "
        f"{wg[0]} ms, read pause {rg[0]} ms; errors seen by the app: writes {dict(errs_w) or 0}, reads {dict(errs_r) or 0}")
    if event == "crash":
        log(f"   Docker restarted {oldc} after {out['container_running_again_ms']} ms (RestartCount "
            f"{before.get('restarts')} -> {restarts_after}); it was SECONDARY again after {out['rejoined_secondary_ms']} ms")
    log(f"   acknowledged writes {len(ack)}, stored {len(present)}, LOST {len(lost)}, retries de-duplicated {dedup[0]}")
    app.close()
    return out


# ============================================================================ Activity - exporter sidecar + Prometheus
def http_get(url: str, timeout: float = 6) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:      # noqa: S310 - fixed internal URLs
        return r.read().decode("utf-8", "replace")


def prom(path: str, **params) -> dict:
    q = urllib.parse.urlencode(params)
    return json.loads(http_get(f"{PROM_URL}{path}{'?' + q if q else ''}", timeout=8))


def prom_value(q: str):
    try:
        r = prom("/api/v1/query", query=q)["data"]["result"]
        return [{"labels": x["metric"], "value": float(x["value"][1])} for x in r]
    except Exception:                                  # noqa: BLE001
        return None


def scrape_exporter() -> dict:
    t0 = time.perf_counter()
    try:
        txt = http_get(EXPORTER_URL, timeout=8)
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:140]}"}
    lines = [ln for ln in txt.splitlines() if ln and not ln.startswith("#")]
    fam = {re.split(r"[{ ]", ln, 1)[0] for ln in lines}

    def vals(name):
        out = []
        for ln in lines:
            if ln.startswith(name + "{") or ln.startswith(name + " "):
                lab = dict(re.findall(r'(\w+)="([^"]*)"', ln.split("}")[0])) if "{" in ln else {}
                try:
                    out.append((lab, float(ln.rsplit(" ", 1)[1])))
                except ValueError:
                    pass
        return out
    up = vals("mongodb_up")
    members = {lab.get("member_idx"): lab.get("member_state") for lab, v in vals("mongodb_rs_members_state")}
    conns = {lab.get("state"): v for lab, v in vals("mongodb_connections")}
    ops = {lab.get("type"): v for lab, v in vals("mongodb_op_counters_total")}
    return {"ok": True, "ms": round((time.perf_counter() - t0) * 1000), "lines": len(lines),
            "families": len([f for f in fam if f.startswith("mongodb_")]), "mongodb_up": up[0][1] if up else None,
            "members": members, "connections_current": conns.get("current"), "op_counters": ops}


INSERTS = ('sum(mongodb_op_counters_total{type="insert"}) + sum(mongodb_mongod_op_counters_repl_total{type="insert"})')
INSERT_RATE = ('sum(rate(mongodb_op_counters_total{type="insert"}[30s])) + '
               'sum(rate(mongodb_mongod_op_counters_repl_total{type="insert"}[30s]))')


def monitoring(log=print, restart_test: bool = True) -> dict:
    """Activity: the sidecar shares prod-mongo-1's network namespace; Prometheus scrapes it; alert rules evaluate it."""
    res = {}
    side, main = inspect(EXPORTER_CONTAINER) or {}, inspect(PROD_CONTAINERS[0]) or {}
    nm = (side.get("HostConfig") or {}).get("NetworkMode", "")
    res["sidecar"] = {"container": EXPORTER_CONTAINER, "status": (side.get("State") or {}).get("Status"),
                      "network_mode": nm, "shares_netns_with": PROD_CONTAINERS[0] if main.get("Id") and main["Id"] in nm else None,
                      "image": (side.get("Config") or {}).get("Image"),
                      "command": " ".join((side.get("Config") or {}).get("Cmd") or []),
                      "env_names": sorted(e.split("=", 1)[0] for e in (side.get("Config") or {}).get("Env") or []
                                          if e.startswith("MONGODB_"))}
    log(f"   sidecar {EXPORTER_CONTAINER}: NetworkMode {nm[:22]}... -> shares the network namespace of "
        f"{res['sidecar']['shares_netns_with']}; it connects to mongodb://127.0.0.1:27017 as user 'exporter' (clusterMonitor)")
    s = scrape_exporter()
    if not (s.get("ok") and s.get("mongodb_up") == 1):
        # the 13.1 crash test (kill -9 + Docker restart of prod-mongo-1) leaves the sidecar in the old network namespace;
        # restart it so it joins the new one (Kubernetes restarts a pod's containers together)
        log(f"   sidecar lost prod-mongo-1's network after the 13.1 restart ({s.get('error') or 'mongodb_up 0'}); "
            f"docker restart {EXPORTER_CONTAINER}")
        res["sidecar_reattached"] = True
        docker("restart", EXPORTER_CONTAINER, timeout=60)
        for _ in range(30):
            s = scrape_exporter()
            if s.get("ok") and s.get("mongodb_up") == 1:
                break
            time.sleep(1)
        time.sleep(11)                                # let Prometheus mark the target up again (5 s interval)
    res["scrape"] = s
    if s.get("ok"):
        log(f"   GET {EXPORTER_URL}: {s['lines']} samples, {s['families']} mongodb_* metric families in {s['ms']} ms; "
            f"mongodb_up {s['mongodb_up']}, members {s['members']}, connections {s['connections_current']}")
    else:
        log(f"   !! exporter not reachable: {s.get('error')}")
    # load -> opcounters rise in Prometheus
    # inserts on a PRIMARY count in opcounters, the same inserts applied on a SECONDARY in opcountersRepl: the sidecar's
    # member can be either, so the lab adds both (measured: a secondary showed opcounters.insert = 0 under load)
    before = prom_value(INSERTS)
    app = new_client("lab_app", sst=5000, w="majority")
    docs = [{"k": i, "pad": "x" * 64, "at": datetime.now(UTC)} for i in range(500)]
    t0 = time.time()
    for _ in range(6):
        app.lab13.metrics_load.insert_many([dict(d) for d in docs])
    app.lab13.metrics_load.drop()
    app.close()
    log(f"   generated load: 3,000 inserts in {time.time() - t0:.1f} s; waiting for two scrapes (5 s interval)")
    time.sleep(12)
    after = prom_value(INSERTS)
    rate = prom_value(INSERT_RATE)
    try:
        tg = prom("/api/v1/targets")["data"]["activeTargets"]
        res["targets"] = [{"job": t["labels"].get("job"), "url": t.get("scrapeUrl"), "health": t.get("health"),
                           "last_scrape_ms": round(1000 * float(t.get("lastScrapeDuration") or 0), 1)} for t in tg]
        rules = prom("/api/v1/rules")["data"]["groups"]
        res["rules"] = [{"name": r["name"], "state": r.get("state"), "query": r.get("query")} for g in rules for r in g["rules"]]
    except Exception as exc:                          # noqa: BLE001
        res["targets"], res["rules"], res["prometheus_error"] = [], [], str(exc)[:160]
    res["inserts_before"] = before[0]["value"] if before else None
    res["inserts_after"] = after[0]["value"] if after else None
    res["insert_rate_per_s"] = round(rate[0]["value"], 1) if rate else None
    res["members_from_prometheus"] = {x["labels"].get("member_idx"): x["labels"].get("member_state")
                                      for x in (prom_value("mongodb_rs_members_state") or [])}
    log(f"   Prometheus: targets {[(t['job'], t['health']) for t in res.get('targets', [])]}; inserts counter "
        f"{res['inserts_before']} -> {res['inserts_after']}, rate {res['insert_rate_per_s']}/s; "
        f"{len(res.get('rules', []))} alert rules loaded")
    if restart_test:
        res["restart_test"] = sidecar_restart_test(log)
    ok_target = any(t["job"] == "mongodb" and t["health"] == "up" for t in res.get("targets", []))
    res["pass"] = bool(s.get("ok") and s.get("mongodb_up") == 1 and res["sidecar"]["shares_netns_with"] and ok_target and
                       res["inserts_after"] is not None and res["inserts_before"] is not None and
                       res["inserts_after"] > res["inserts_before"])
    return res


def sidecar_restart_test(log=print) -> dict:
    """What happens to a sidecar when its main container restarts? (Kubernetes restarts a pod's containers together.)"""
    main = PROD_CONTAINERS[0]
    out = {"main": main}
    host = PROD_HOSTS[0]
    if primary_host() == host:                        # keep it a clean experiment: move the primary away first
        try:
            client("admin").admin.command("replSetStepDown", 30, secondaryCatchUpPeriodSecs=10)
        except PyMongoError:
            pass
        time.sleep(4)
    t0 = time.time()
    r = docker("restart", main, timeout=90)
    out["restart_rc"] = r.rc
    for _ in range(60):
        if hello(host).get("up"):
            break
        time.sleep(1)
    out["main_back_s"] = round(time.time() - t0, 1)
    time.sleep(3)
    s1 = scrape_exporter()
    out["scrape_after_main_restart"] = {"ok": s1.get("ok"), "mongodb_up": s1.get("mongodb_up"), "error": s1.get("error")}
    out["sidecar_broken"] = not (s1.get("ok") and s1.get("mongodb_up") == 1)
    log(f"   docker restart {main}: back after {out['main_back_s']} s; sidecar scrape now -> "
        f"{'OK' if not out['sidecar_broken'] else 'FAILS (' + str(s1.get('error') or 'mongodb_up 0') + ')'}")
    if out["sidecar_broken"]:
        t1 = time.time()
        docker("restart", EXPORTER_CONTAINER, timeout=60)
        for _ in range(20):
            s2 = scrape_exporter()
            if s2.get("ok") and s2.get("mongodb_up") == 1:
                break
            time.sleep(1)
        out["sidecar_restarted"] = True
        out["sidecar_ok_after_restart"] = bool(s2.get("ok") and s2.get("mongodb_up") == 1)
        out["sidecar_fix_s"] = round(time.time() - t1, 1)
        log(f"   docker restart {EXPORTER_CONTAINER} -> scrape OK again: {out['sidecar_ok_after_restart']} "
            f"({out['sidecar_fix_s']} s). In Kubernetes both containers of a pod are restarted together.")
    time.sleep(6)
    try:
        q = prom("/api/v1/query_range", query='up{job="mongodb"}', start=t0 - 15, end=time.time(), step="5")
        vals = q["data"]["result"][0]["values"] if q["data"]["result"] else []
        out["prometheus_up"] = [[round(float(t) - t0, 1), int(float(v))] for t, v in vals]
        al = prom("/api/v1/alerts")["data"]["alerts"]
        out["alerts_now"] = [{"name": a["labels"].get("alertname"), "state": a.get("state")} for a in al]
    except Exception:                                  # noqa: BLE001
        out["prometheus_up"], out["alerts_now"] = [], []
    out["pass"] = (not out["sidecar_broken"]) or bool(out.get("sidecar_ok_after_restart"))
    return out


# ============================================================================ Lab 13 applied to Lab 10 - billing
def billing_copy(log=print) -> dict:
    """Copy the live classifier billing (rs0 or the Lab 11 cluster) to the production set and verify it."""
    from . import billing
    src = billing.backend(refresh=True)
    adm = client("admin")
    dst = adm[billing.DB]
    res = {"source": src["label"], "collections": {}}
    if src["name"] == "migrating":
        raise RuntimeError("the billing data is being moved by Lab 11 right now - try again in a few seconds")
    for c in ("accounts", "usage", "payments", "revenue"):
        docs = list(src["client"][billing.DB][c].find())
        dst[c].drop()
        if docs:
            for i in range(0, len(docs), 1000):
                dst[c].insert_many(docs[i:i + 1000], ordered=False)
        n = dst[c].count_documents({})
        res["collections"][c] = {"source": len(docs), "copied": n, "verified": n == len(docs)}
    billing.ensure_schema(adm)
    a, b = billing.invariants(src["client"]), billing.invariants(adm)
    res["invariants_source"], res["invariants_copy"] = a, b
    res["verified"] = all(v["verified"] for v in res["collections"].values()) and \
        (a["wallets"], a["revenue"], a["topups"]) == (b["wallets"], b["revenue"], b["topups"])
    log(f"   copied cti_billing from the {src['label']}: " + ", ".join(f"{k} {v['copied']:,}" for k, v in res["collections"].items())
        + f"; wallets Rs {b['wallets']:,}, revenue Rs {b['revenue']:,} on both -> verified {res['verified']}")
    return res


def billing_acid(log=print) -> dict:
    from . import billing
    bc = new_client("billing_app", sst=5000, w="majority")
    try:
        return billing.acid_checks(bc, threads=10, log=lambda m: log("   " + m))
    finally:
        bc.close()


def billing_failover(event: str = "stepdown", users: int = 24, spread_s: float = 14.0, log=print) -> dict:
    """Customer journeys of the classifier paywall run THROUGH a failover. Every journey must end exactly right."""
    from . import billing
    bc = new_client("billing_app", sst=2000, socketTimeoutMS=6000, w="majority")
    db = bc[billing.DB]
    tag = f"{int(time.time()) % 1000000:06d}"
    lock = threading.Lock()
    st = Counter()
    errors = Counter()

    def was_committed(committed):
        """Ask until the cluster answers (during an election the check itself can fail)."""
        for _ in range(60):
            try:
                return bool(committed())
            except PyMongoError:
                time.sleep(0.5)
        return False

    def retry(op, committed, label):
        deadline = time.time() + 120
        attempts = 0
        while True:
            attempts += 1
            try:
                return op()
            except billing.BillingError:
                # a retry that is refused may only be refused because the first attempt DID commit
                if attempts > 1 and was_committed(committed):
                    with lock:
                        st["recovered_committed"] += 1
                    return None
                raise
            except PyMongoError as exc:
                with lock:
                    errors[type(exc).__name__] += 1
                    st["app_retries"] += 1
                if time.time() > deadline:
                    raise
                time.sleep(0.4)
                if was_committed(committed):         # it had committed before the error reached the app
                    with lock:
                        st["recovered_committed"] += 1
                    return None

    def journey(i):
        time.sleep(i * spread_s / users)
        uid = f"lab13-{tag}-{i:03d}@failover.test"
        r = {"user": uid, "free": 0, "refused": 0, "sub_uses": 0, "error": None}
        try:
            retry(lambda: billing.sign_in(uid, f"Failover user {i}", client=bc, synthetic=True),
                  lambda: db.accounts.find_one({"_id": uid}), "sign_in")
            for k in range(6):
                opid = f"{tag}-{i}-u{k}"
                try:
                    retry(lambda: billing.consume(uid, lambda: billing._fake_work(2), client=bc, note=opid),
                          lambda: db.usage.find_one({"user_id": uid, "note": opid}), "consume")
                    if k < 3:
                        r["free"] += 1
                    else:
                        r["sub_uses"] += 1
                except billing.PaymentRequired:
                    r["refused"] += 1
                if k == 3:                                   # after the refusal: pay and subscribe
                    retry(lambda: billing.topup(uid, 200, f"upi-{tag}-{i}", client=bc),
                          lambda: db.payments.find_one({"user_id": uid, "idem": f"upi-{tag}-{i}"}), "topup")
                    retry(lambda: billing.subscribe(uid, f"pay-{tag}-{i}", client=bc),
                          lambda: db.payments.find_one({"user_id": uid, "idem": f"pay-{tag}-{i}"}), "subscribe")
                time.sleep(0.25)
        except Exception as exc:                        # noqa: BLE001
            r["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        return r

    old = primary_host()
    trig = {}

    def trigger():
        time.sleep(spread_s * 0.35)
        trig["at"] = time.time()
        if event == "stepdown":
            try:
                client("admin").admin.command("replSetStepDown", 20, secondaryCatchUpPeriodSecs=10)
            except PyMongoError:
                pass
        else:
            crash_member(container_of(old))
    t0 = time.time()
    tt = threading.Thread(target=trigger, daemon=True)
    tt.start()
    with ThreadPoolExecutor(users) as ex:
        results = list(ex.map(journey, range(users)))
    tt.join()
    secs = round(time.time() - t0, 1)
    log(f"   {users} customer journeys during a {'rs.stepDown()' if event == 'stepdown' else 'crash (kill -9)'} of {old}: "
        f"{secs} s, app-level retries {st['app_retries']} ({dict(errors) or 'none'}), operations found committed after an "
        f"error {st['recovered_committed']}")
    if event == "crash":                                 # let the crashed member come back before checking
        cn = container_of(old)
        for _ in range(120):
            if hello(old).get("secondary"):
                break
            time.sleep(1)
        log(f"   {cn} is back: {hello(old).get('secondary')}")
    per = []
    for r in results:
        a = db.accounts.find_one({"_id": r["user"]}) or {}
        uses = list(db.usage.find({"user_id": r["user"]}))
        pays = list(db.payments.find({"user_id": r["user"]}))
        ok = (r["error"] is None and a.get("wallet") == 100 and a.get("free_used") == 3 and len(uses) == 5 and
              Counter(u["kind"] for u in uses) == Counter({"free": 3, "subscription": 2}) and
              sorted(p["purpose"] for p in pays) == ["subscription", "topup"] and r["refused"] == 1 and
              len({u.get("note") for u in uses}) == 5)
        per.append({"user": r["user"], "wallet": a.get("wallet"), "free_used": a.get("free_used"), "usage": len(uses),
                    "payments": len(pays), "refused": r["refused"], "error": r["error"], "ok": ok})
    inv = billing.invariants(bc)
    bad = [p for p in per if not p["ok"]]
    out = {"event": event, "old_primary": old, "new_primary": primary_host(), "users": users, "seconds": secs,
           "app_retries": st["app_retries"], "errors": dict(errors), "recovered_committed": st["recovered_committed"],
           "journeys_ok": len(per) - len(bad), "journeys_bad": bad[:5],
           "expected_per_user": "wallet Rs 100 (200 top-up - 100 plan), 3 free + 2 plan uses, 1 refusal, 2 payments",
           "invariants": inv, "pass": not bad and inv["money_conserved"] and inv["revenue_matches"]}
    log(f"   result: {out['journeys_ok']}/{users} journeys exactly right (wallet 100, 3 free, 1 refused, 5 uses, 2 payments); "
        f"money conserved {inv['money_conserved']}, revenue = subscriptions {inv['revenue_matches']}")
    bc.close()
    return out


# ============================================================================ Lab 13.2 - Kubernetes
def _kubeconfig() -> Path | None:
    src = KUBE_DIR / "kubeconfig.yaml"
    if not src.exists():
        return None
    txt = src.read_text().replace("https://127.0.0.1:6443", f"https://{K3S_HOST}:6443")
    dst = Path(tempfile.gettempdir()) / "lab13-kubeconfig.yaml"
    dst.write_text(txt)
    os.chmod(dst, 0o600)
    try:                                       # so that a plain `kubectl` inside the lab13 container works too
        home = Path.home() / ".kube"
        home.mkdir(exist_ok=True)
        (home / "config").write_text(txt)
        os.chmod(home / "config", 0o600)
    except OSError:
        pass
    return dst


def kubectl(*args, input: str | None = None, timeout: int = 120) -> R:
    kc = _kubeconfig()
    if shutil.which("kubectl") and kc:
        return sh(["kubectl", "--kubeconfig", kc, *args], input=input, timeout=timeout)
    return sh(["docker", "exec", "-i", K3S, "kubectl", *args], input=input, timeout=timeout)


def kjson(*args, timeout: int = 60) -> dict:
    r = kubectl(*args, "-o", "json", timeout=timeout)
    if r.rc != 0:
        raise RuntimeError((r.err or r.out).strip()[-300:])
    return json.loads(r.out)


def kmongosh(pod: str, js: str, *, ns: str = NS, auth: bool = False, uri: str | None = None, container: str = "mongod",
             timeout: int = 60, nodb: bool = False) -> R:
    cmd = ["exec", "-n", ns, pod, "-c", container, "--", "mongosh", "--quiet"]
    if nodb:                                   # the script opens its own connection (new Mongo(URI))
        cmd.append("--nodb")
    elif uri:
        cmd.append(uri)
    if auth:
        cmd += ["-u", "admin", "-p", k8s_admin_password(), "--authenticationDatabase", "admin"]
    return kubectl(*cmd, "--eval", js, timeout=timeout)


def k8s_admin_password() -> str:
    r = kubectl("get", "secret", "mongo-admin", "-n", NS, "-o", "jsonpath={.data.password}", timeout=30)
    return base64.b64decode(r.out.strip()).decode() if r.rc == 0 and r.out.strip() else ""


def k8s_uri(user: bool = True) -> str:
    hosts = ",".join(f"mongo-{i}.mongo-headless-svc:27017" for i in range(3))
    cred = f"admin:{urllib.parse.quote(k8s_admin_password(), safe='')}@" if user else ""
    return f"mongodb://{cred}{hosts}/?replicaSet=rs0&authSource=admin&serverSelectionTimeoutMS=4000"


def k8s_ready(log=print, timeout: int = 300) -> dict:
    if K8S_MODE == "off":
        return {"ready": False, "reason": "LAB13_K8S=off"}
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        r = kubectl("get", "nodes", "-o", "json", timeout=20)
        if r.rc == 0:
            try:
                nodes = json.loads(r.out)["items"]
                ready = [n for n in nodes if any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"]["conditions"])]
                if ready:
                    n = ready[0]
                    info = n["status"]["nodeInfo"]
                    return {"ready": True, "node": n["metadata"]["name"], "kubelet": info.get("kubeletVersion"),
                            "runtime": info.get("containerRuntimeVersion"), "os": info.get("osImage"),
                            "waited_s": round(time.time() - t0, 1),
                            "client": "kubectl " + ("(image) --kubeconfig" if shutil.which("kubectl") and _kubeconfig()
                                                    else f"via docker exec {K3S}")}
            except (ValueError, KeyError):
                pass
        last = (r.err or r.out).strip()[-200:]
        time.sleep(4)
    return {"ready": False, "reason": f"Kubernetes API not ready after {timeout} s: {last}"}


def preload_image(log=print) -> dict:
    """Give k3s the mongo image the host already has (no second download, no Docker Hub rate limit). Best effort."""
    r = docker("exec", K3S, "crictl", "images", "-o", "json", timeout=30)
    if r.rc == 0 and f"docker.io/library/{MONGO_IMAGE}" in r.out:
        return {"preloaded": True, "already": True}
    if docker("image", "inspect", MONGO_IMAGE, timeout=20).rc != 0:
        return {"preloaded": False, "reason": "image not on the host - k3s pulls it from Docker Hub"}
    t0 = time.time()
    r = sh(["bash", "-c", f"docker save {MONGO_IMAGE} | docker exec -i {K3S} ctr -n k8s.io images import -"], timeout=600)
    ok = r.rc == 0
    log(f"   docker save {MONGO_IMAGE} | ctr images import -> {'imported' if ok else 'failed, k3s will pull it'} "
        f"({time.time() - t0:.0f} s)")
    return {"preloaded": ok, "seconds": round(time.time() - t0, 1), "detail": "" if ok else (r.err or r.out)[-200:]}


def _manifest(name: str) -> str:
    return (LAB / "k8s" / name).read_text()


def _secret_yaml(name: str, ns: str, data: dict) -> str:
    return json.dumps({"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {"name": name, "namespace": ns},
                       "data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}})


def pods_summary(ns: str = NS) -> list:
    try:
        items = kjson("get", "pods", "-n", ns, timeout=20)["items"]
    except Exception:                                  # noqa: BLE001
        return []
    out = []
    for p in items:
        cs = (p["status"].get("containerStatuses") or [])
        out.append({"pod": p["metadata"]["name"], "uid": p["metadata"]["uid"][:8], "phase": p["status"].get("phase"),
                    "ready": all(c.get("ready") for c in cs) if cs else False, "restarts": sum(c.get("restartCount", 0) for c in cs),
                    "ip": p["status"].get("podIP"), "node": p["spec"].get("nodeName"),
                    "init": [f"{c['name']}: {list((c.get('state') or {}).keys())[0] if c.get('state') else '?'}"
                             for c in (p["status"].get("initContainerStatuses") or [])],
                    "created": p["metadata"].get("creationTimestamp")})
    return out


def k8s_rs_state(pod: str = "mongo-0") -> dict:
    """rs.status() through any pod that answers (each mongod knows the whole set)."""
    js = ("const s = rs.status(); print(JSON.stringify({term: s.term, members: s.members.map(m => ({name: m.name, "
          "state: m.stateStr, health: m.health, uptime: m.uptime}))}))")
    for p in [pod] + [f"mongo-{i}" for i in range(3) if f"mongo-{i}" != pod]:
        r = kmongosh(p, js, auth=True, timeout=30)
        d = _last_json(r.out)
        if d:
            d["via"] = p
            return d
    return {}


def k8s_deploy(log=print) -> dict:
    res = {"cluster": k8s_ready(log)}
    if not res["cluster"].get("ready"):
        raise RuntimeError(res["cluster"].get("reason", "Kubernetes not ready"))
    c = res["cluster"]
    log(f"   Kubernetes ready: node {c['node']}, {c['kubelet']}, {c['runtime']} ({c['client']})")
    res["image"] = preload_image(log)
    kubectl("apply", "-f", "-", input=_manifest("namespace.yaml"))
    have = kubectl("get", "secret", "mongo-keyfile", "-n", NS, timeout=20).rc == 0
    if not have:
        key = subprocess.run(["openssl", "rand", "-base64", "756"], capture_output=True, text=True, check=True).stdout
        kubectl("apply", "-f", "-", input=_secret_yaml("mongo-keyfile", NS, {"keyfile": key}))
        kubectl("apply", "-f", "-", input=_secret_yaml("mongo-admin", NS, {"username": "admin",
                                                                              "password": _secrets.token_urlsafe(18)}))
        log("   keyfile generated ONCE (openssl rand -base64 756) and stored in Secret mongo-keyfile; admin password in "
            "Secret mongo-admin")
    res["secret_created_now"] = not have
    for f in ("mongo-headless-svc.yaml", "mongo-statefulset.yaml", "mongo-client.yaml"):
        r = kubectl("apply", "-f", "-", input=_manifest(f))
        log(f"   $ kubectl apply -f {f}: " + " | ".join(x.strip() for x in (r.out + r.err).splitlines() if x.strip())[:160])
    t0 = time.time()
    r = kubectl("rollout", "status", "statefulset/mongo", "-n", NS, "--timeout=600s", timeout=620)
    kubectl("wait", "--for=condition=Ready", "pod/mongo-client", "-n", NS, "--timeout=300s", timeout=320)
    res["rollout_s"] = round(time.time() - t0, 1)
    log(f"   kubectl rollout status statefulset/mongo -> {r.out.strip().splitlines()[-1] if r.out.strip() else r.err.strip()[-160:]} "
        f"({res['rollout_s']} s)")
    if r.rc != 0:
        raise RuntimeError("the StatefulSet did not become ready: " + (r.err or r.out)[-200:])
    res["pods"] = pods_summary()
    # rs.initiate exactly as the solution says, from mongo-0, through the localhost exception
    st = _last_json(kmongosh("mongo-0", "const h = db.hello(); print(JSON.stringify({set: h.setName || null}))").out) or {}
    cfg = ("{_id: 'rs0', members: [{_id: 0, host: 'mongo-0.mongo-headless-svc:27017'}, "
           "{_id: 1, host: 'mongo-1.mongo-headless-svc:27017'}, {_id: 2, host: 'mongo-2.mongo-headless-svc:27017'}]}")
    if not st.get("set"):
        r = kmongosh("mongo-0", f"printjson(rs.initiate({cfg}))", timeout=60)
        res["initiate"] = (r.out + r.err).strip()[-300:]
        log(f"   $ kubectl exec -it mongo-0 -- mongosh --eval \"rs.initiate({cfg})\" -> "
            f"{'ok: 1' if 'ok: 1' in r.out else res['initiate'][-160:]}")
    else:
        res["initiate"] = "already initiated"
    prim = None
    for _ in range(90):
        for i in range(3):
            d = _last_json(kmongosh(f"mongo-{i}", "print(JSON.stringify({p: db.hello().isWritablePrimary}))", timeout=20).out)
            if d and d.get("p"):
                prim = f"mongo-{i}"
                break
        if prim:
            break
        time.sleep(2)
    if not prim:
        raise RuntimeError("no PRIMARY elected in the Kubernetes replica set")
    pw = k8s_admin_password()
    ok = _last_json(kmongosh(prim, "print(JSON.stringify({ok: db.adminCommand({ping:1}).ok}))", auth=True, timeout=30).out)
    if not ok:
        js = (f"db.getSiblingDB('admin').createUser({{user: 'admin', pwd: {json.dumps(pw)}, roles: [{{role: 'root', db: 'admin'}}]}});"
              " print('created')")
        r = kmongosh(prim, js, timeout=30)
        log(f"   createUser admin on {prim} (localhost exception) -> {'created' if 'created' in r.out else (r.out + r.err)[-160:]}")
    res["primary"] = prim
    keys = []
    for i in range(3):
        r = kubectl("exec", "-n", NS, f"mongo-{i}", "-c", "mongod", "--", "sh", "-c",
                    "stat -c '%a %U:%G %s' /etc/mongodb/keyfile; sha256sum /etc/mongodb/keyfile", timeout=30)
        p = r.out.split()
        keys.append({"pod": f"mongo-{i}", "mode": p[0] if p else None, "owner": p[1] if len(p) > 1 else None,
                     "bytes": int(p[2]) if len(p) > 2 else None, "sha256": p[3] if len(p) > 3 else None})
    res["keyfiles"] = keys
    res["init_log"] = kubectl("logs", "-n", NS, "mongo-0", "-c", "keyfile-init", timeout=20).out.strip()[-300:]
    dns = kubectl("exec", "-n", NS, "mongo-client", "--", "getent", "hosts", "mongo-0.mongo-headless-svc",
                  "mongo-1.mongo-headless-svc", "mongo-2.mongo-headless-svc", timeout=20).out
    res["dns"] = [ln.split() for ln in dns.splitlines() if ln.strip()]
    res["rs"] = k8s_rs_state(prim)
    try:
        res["pvcs"] = [{"name": p["metadata"]["name"], "uid": p["metadata"]["uid"][:8], "status": p["status"].get("phase"),
                        "volume": p["spec"].get("volumeName"), "size": p["spec"]["resources"]["requests"]["storage"],
                        "class": p["spec"].get("storageClassName")} for p in kjson("get", "pvc", "-n", NS)["items"]]
        svc = kjson("get", "svc", "mongo-headless-svc", "-n", NS)
        res["service"] = {"clusterIP": svc["spec"].get("clusterIP"), "selector": svc["spec"].get("selector"),
                          "publishNotReadyAddresses": svc["spec"].get("publishNotReadyAddresses")}
    except Exception as exc:                          # noqa: BLE001
        res["pvcs"], res["service"] = [], {"error": str(exc)[:120]}
    log("   keyfile in every pod: " + ", ".join(f"{k['pod']} {k['mode']} {k['owner']} sha {str(k['sha256'])[:10]}" for k in keys))
    log("   DNS (headless service): " + ", ".join(f"{x[1]} -> {x[0]}" for x in res["dns"] if len(x) > 1))
    log("   rs.status(): " + ", ".join(f"{m['name'].split('.')[0]} {m['state']}" for m in res["rs"].get("members", [])))
    st = Counter(m["state"] for m in res["rs"].get("members", []))
    res["pass"] = (st.get("PRIMARY") == 1 and st.get("SECONDARY") == 2 and len({k["sha256"] for k in keys}) == 1 and
                   all(k["mode"] == "400" for k in keys) and len(res["pvcs"]) == 3 and res["service"].get("clusterIP") == "None")
    return res


def _pod(name: str, ns: str = NS) -> dict:
    try:
        p = kjson("get", "pod", name, "-n", ns, timeout=15)
    except Exception:                                  # noqa: BLE001
        return {"exists": False}
    cs = p["status"].get("containerStatuses") or []
    return {"exists": True, "uid": p["metadata"]["uid"], "phase": p["status"].get("phase"),
            "ready": bool(cs) and all(c.get("ready") for c in cs), "deleting": bool(p["metadata"].get("deletionTimestamp"))}


def _count_on(pod: str, coll: str = "resiliency") -> int | None:
    js = (f"db.getMongo().setReadPref('secondaryPreferred'); print(JSON.stringify({{n: db.getSiblingDB('lab13').{coll}"
          f".countDocuments({{}})}}))")
    d = _last_json(kmongosh(pod, js, auth=True, timeout=30).out)
    return d.get("n") if d else None


def k8s_resiliency(pod: str = "mongo-2", docs: int = 1000, log=print) -> dict:
    """The classroom test: kubectl delete pod mongo-2 -> recreated with the same name, PVC and data; rejoins the set."""
    res = {"pod": pod}
    uri = k8s_uri()
    js = (f"const c = db.getSiblingDB('lab13').resiliency; c.drop(); const d = []; for (let i = 0; i < {docs}; i++) "
          f"d.push({{i: i, note: 'written before the pod was deleted'}}); c.insertMany(d, {{writeConcern: {{w: 3}}}}); "
          f"print(JSON.stringify({{n: c.countDocuments({{}})}}))")
    r = kmongosh("mongo-client", js, uri=uri, container="client", timeout=90)
    res["written"] = (_last_json(r.out) or {}).get("n")
    pvc = kjson("get", "pvc", f"data-{pod}", "-n", NS)
    before = _pod(pod)
    res["before"] = {"pod_uid": before.get("uid", "")[:8], "pvc_uid": pvc["metadata"]["uid"][:8],
                     "volume": pvc["spec"].get("volumeName"), "count_on_pod": _count_on(pod)}
    log(f"   {docs} documents written with w:3 (all members); {pod}: pod uid {res['before']['pod_uid']}, PVC data-{pod} uid "
        f"{res['before']['pvc_uid']}, documents on {pod}: {res['before']['count_on_pod']}")
    t0 = time.time()
    kubectl("delete", "pod", pod, "-n", NS, "--wait=false", timeout=30)
    log(f"   $ kubectl delete pod {pod}")
    seen = {}
    events = []
    while time.time() - t0 < 240:
        p = _pod(pod)
        t = round(time.time() - t0, 1)
        if p.get("exists") and p["uid"] != before.get("uid"):
            seen.setdefault("recreated", t)
            if p["phase"] == "Running":
                seen.setdefault("running", t)
            if p["ready"]:
                seen.setdefault("ready", t)
        if "ready" in seen:
            st = k8s_rs_state("mongo-0" if pod != "mongo-0" else "mongo-1")
            m = next((x for x in st.get("members", []) if x["name"].startswith(pod + ".")), {})
            if m.get("state") == "SECONDARY" and m.get("health") == 1:
                seen["secondary"] = round(time.time() - t0, 1)
                break
        events.append((t, p.get("phase"), p.get("ready")))
        time.sleep(1)
    pvc2 = kjson("get", "pvc", f"data-{pod}", "-n", NS)
    after = _pod(pod)
    res["after"] = {"pod_uid": after.get("uid", "")[:8], "pvc_uid": pvc2["metadata"]["uid"][:8],
                    "volume": pvc2["spec"].get("volumeName"), "count_on_pod": _count_on(pod)}
    res["timeline_s"] = seen
    res["rs_after"] = k8s_rs_state()
    res["pass"] = bool(seen.get("secondary")) and res["after"]["pvc_uid"] == res["before"]["pvc_uid"] and \
        res["after"]["count_on_pod"] == docs and res["after"]["pod_uid"] != res["before"]["pod_uid"]
    log(f"   recreated after {seen.get('recreated')} s, Running {seen.get('running')} s, Ready {seen.get('ready')} s, SECONDARY "
        f"again {seen.get('secondary')} s; new pod uid {res['after']['pod_uid']}, SAME PVC {res['after']['pvc_uid']} "
        f"({res['after']['volume']}), documents on {pod}: {res['after']['count_on_pod']}")
    return res


def k8s_failover_txn(duration_s: int = 50, log=print) -> dict:
    """Lab 13.2 applied to Lab 10: a client pod moves money in multi-document transactions; the PRIMARY pod is deleted."""
    uri = k8s_uri()
    setup = ("const b = db.getSiblingDB('lab13_bank'); b.accounts.drop(); b.ledger.drop(); "
             "b.accounts.insertMany([{_id: 'A', balance: 1000}, {_id: 'B', balance: 1000}], {writeConcern: {w: 'majority'}}); "
             "print(JSON.stringify({ok: 1}))")
    kmongosh("mongo-client", setup, uri=uri, container="client", timeout=60)
    st = k8s_rs_state()
    prim = next((m["name"].split(".")[0] for m in st.get("members", []) if m["state"] == "PRIMARY"), "mongo-0")
    script = (LAB / "mongosh" / "k8s_transfers.js").read_text().replace("__URI__", uri) \
        .replace("__DURATION__", str(duration_s * 1000)).replace("__POD__", "mongo-client")
    holder = {}

    def run():
        holder["r"] = kmongosh("mongo-client", script, container="client", nodb=True, timeout=duration_s + 200)
    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(10)
    before = _pod(prim)
    t0 = time.time()
    kubectl("delete", "pod", prim, "-n", NS, "--wait=false", timeout=30)
    log(f"   t=10 s: $ kubectl delete pod {prim}   (the PRIMARY) while mongo-client runs transfers A -> B")
    seen = {}
    while time.time() - t0 < 240:
        t = round(time.time() - t0, 1)
        if "new_primary" not in seen:
            for i in range(3):
                pn = f"mongo-{i}"
                if pn == prim:
                    continue
                d = _last_json(kmongosh(pn, "print(JSON.stringify({p: db.hello().isWritablePrimary}))", timeout=15).out)
                if d and d.get("p"):
                    seen["new_primary"], seen["new_primary_s"] = pn, round(time.time() - t0, 1)
                    break
        p = _pod(prim)
        if p.get("exists") and p["uid"] != before.get("uid") and p["ready"]:
            seen.setdefault("ready_s", t)
            m = next((x for x in k8s_rs_state(seen.get("new_primary", "mongo-1")).get("members", [])
                      if x["name"].startswith(prim + ".")), {})
            if m.get("state") == "SECONDARY":
                seen["secondary_s"] = round(time.time() - t0, 1)
        if "secondary_s" in seen and not th.is_alive():
            break
        time.sleep(1.5)
    th.join(timeout=duration_s + 220)
    out = _last_json(holder.get("r", R(1, "", "")).out) or {"error": (holder.get("r", R(1, "", "no output")).err or "")[-300:]}
    check = _last_json(kmongosh("mongo-client", "const b = db.getSiblingDB('lab13_bank'); "
                                "const a = b.accounts.find().toArray(); print(JSON.stringify({A: a.find(x => x._id == 'A').balance, "
                                "B: a.find(x => x._id == 'B').balance, ledger: b.ledger.countDocuments({})}))",
                                uri=uri, container="client", timeout=60).out) or {}
    res = {"deleted_primary": prim, **seen, "client": {k: v for k, v in out.items() if k != "per_second"},
           "per_second": out.get("per_second", []), "balances": check,
           "rs_after": k8s_rs_state(seen.get("new_primary", "mongo-1"))}
    a, b = check.get("A"), check.get("B")
    res["sum_ok"] = a is not None and a + b == 2000
    # every committed transfer is in the ledger exactly once and moved exactly Rs 1: A lost what the ledger says
    res["ledger_matches"] = a is not None and check.get("ledger") == out.get("committed") and 1000 - a == out.get("committed")
    res["pass"] = bool(res["sum_ok"] and res["ledger_matches"] and seen.get("new_primary") and seen.get("secondary_s"))
    log(f"   new PRIMARY {seen.get('new_primary')} after {seen.get('new_primary_s')} s; deleted pod Ready again "
        f"{seen.get('ready_s')} s, SECONDARY {seen.get('secondary_s')} s")
    log(f"   client: {out.get('committed')} transfers committed, {out.get('retries')} retries ({out.get('errors')}), "
        f"{out.get('recovered')} found committed after an error, longest pause {out.get('max_gap_ms')} ms; A {a} + B {b} = "
        f"{(a or 0) + (b or 0)}, ledger {check.get('ledger')}")
    return res


def k8s_classroom_variant(log=print) -> dict:
    """The solution text as written: an InitContainer GENERATES the keyfile in each pod's own emptyDir."""
    res = {}
    kubectl("delete", "namespace", NS_BAD, "--wait=true", "--timeout=120s", timeout=140)
    r = kubectl("apply", "-f", "-", input=_manifest("classroom-variant/classroom-statefulset.yaml"))
    log("   $ kubectl apply -f classroom-variant/classroom-statefulset.yaml   (namespace lab13-classroom, 2 pods)")
    r = kubectl("rollout", "status", "statefulset/mongo", "-n", NS_BAD, "--timeout=300s", timeout=320)
    if r.rc != 0:
        res["error"] = (r.err or r.out)[-200:]
        return res
    shas = []
    for i in range(2):
        o = kubectl("exec", "-n", NS_BAD, f"mongo-{i}", "-c", "mongod", "--", "sha256sum", "/etc/mongodb/keyfile", timeout=30).out
        shas.append(o.split()[0] if o.split() else None)
    res["keyfile_sha256"] = {f"mongo-{i}": (s or "")[:16] for i, s in enumerate(shas)}
    js = ("try { printjson(rs.initiate({_id: 'rs0', members: [{_id: 0, host: 'mongo-0.mongo-headless-svc:27017'}, "
          "{_id: 1, host: 'mongo-1.mongo-headless-svc:27017'}]})) } catch (e) { print(JSON.stringify({code: e.code, "
          "codeName: e.codeName, message: e.message})) }")
    out = kmongosh("mongo-0", js, ns=NS_BAD, timeout=60)
    d = _last_json(out.out) or {"message": (out.out + out.err).strip()[-300:]}
    res["initiate"] = d
    res["different_keys"] = len(set(shas)) == 2
    res["refused"] = "uthentication failed" in json.dumps(d) or d.get("code") == 74
    log(f"   keyfile sha256: {res['keyfile_sha256']} -> different in every pod: {res['different_keys']}")
    log(f"   rs.initiate -> {d.get('codeName')} ({d.get('code')}): {str(d.get('message'))[:170]}")
    kubectl("delete", "namespace", NS_BAD, "--wait=false", timeout=60)
    res["pass"] = res["different_keys"] and res["refused"]
    return res


# ============================================================================ Docker Compose vs Kubernetes
def _yaml_lines(path: Path) -> int:
    return sum(1 for ln in path.read_text().splitlines() if ln.strip() and not ln.strip().startswith("#"))


def compare(res: dict) -> list:
    c = (res.get("lab_13_1") or {}).get("failover_crash") or {}
    k = (res.get("lab_13_2") or {})
    kr, kf = k.get("resiliency") or {}, k.get("failover") or {}
    mon = (res.get("activity") or {}).get("restart_test") or {}
    comp_lines = _yaml_lines(LAB / "compose" / "docker-compose.production.yml")
    k8s_lines = sum(_yaml_lines(LAB / "k8s" / f) for f in ("mongo-headless-svc.yaml", "mongo-statefulset.yaml"))
    ms = lambda v: f"{v / 1000:.1f} s" if isinstance(v, (int, float)) else "-"         # noqa: E731
    s = lambda v: f"{v} s" if v is not None else "-"                                 # noqa: E731
    return [
        {"aspect": "Recovery after mongod dies", "compose": f"restart policy restarts the container on the same host: running again "
         f"{ms(c.get('container_running_again_ms'))}, SECONDARY {ms(c.get('rejoined_secondary_ms'))}",
         "k8s": f"StatefulSet controller recreates the pod: Ready {s(kr.get('timeline_s', {}).get('ready'))}, SECONDARY "
         f"{s(kr.get('timeline_s', {}).get('secondary'))}; would also move it to another node if the node died"},
        {"aspect": "New PRIMARY after the primary is lost", "compose": f"{ms(c.get('election_ms'))} (crash, electionTimeout)",
         "k8s": f"{s(kf.get('new_primary_s'))} (pod deleted; graceful shutdown lets it step down first)"},
        {"aspect": "Stable network identity", "compose": "container name = DNS name on the compose network (prod-mongo-N)",
         "k8s": "headless Service + ordinal: mongo-N.mongo-headless-svc, the same after every recreation"},
        {"aspect": "Storage", "compose": "named volume per member (prod-data-N); lost if the host is lost",
         "k8s": f"PVC per pod from volumeClaimTemplates; same PVC after recreation: "
         f"{(kr.get('before') or {}).get('pvc_uid') == (kr.get('after') or {}).get('pvc_uid') if kr else '-'}"},
        {"aspect": "Keyfile distribution", "compose": "one volume written once by an init container, mounted read-only",
         "k8s": "one Secret; an InitContainer copies it into an in-memory emptyDir with owner 999 and mode 400"},
        {"aspect": "Sidecar container", "compose": "network_mode service:prod-mongo-1; after the main container restarted the "
         f"sidecar {'had to be restarted by hand' if mon.get('sidecar_broken') else 'kept working'}",
         "k8s": "containers of one pod share its network and are restarted together"},
        {"aspect": "Scaling to 5 members", "compose": "add two services to the file + rs.add() by hand",
         "k8s": "kubectl scale statefulset mongo --replicas=5 + rs.add() (an operator would automate the rs.add)"},
        {"aspect": "Configuration size", "compose": f"{comp_lines} lines (one file)", "k8s": f"{k8s_lines} lines (Service + StatefulSet) "
         "+ Secret + a cluster to run them"},
        {"aspect": "Operational complexity", "compose": "low: one host, docker compose up -d",
         "k8s": "higher: cluster, storage class, DNS, probes, RBAC - in exchange for self-healing and scheduling"},
    ]


# ============================================================================ Cloud - Atlas + Terraform
def terraform_review() -> list:
    t = (LAB / "terraform" / "reference" / "main.tf").read_text()
    return [
        {"issue": "M0 (free tier) is a shared 'TENANT' cluster", "found": 'provider_name               = "AWS"' in t or
         re.search(r'provider_name\s*=\s*"AWS"', t) is not None and "TENANT" not in t,
         "why": "Atlas creates M0/M2/M5 only with provider_name = TENANT and backing_provider_name = AWS; "
                "provider_name = AWS with M0 is rejected by the Atlas API at apply time (validate cannot see it)",
         "fix": 'provider_name = "TENANT", backing_provider_name = "AWS"'},
        {"issue": "cluster_uri output repeats the scheme", "found": "mongodb+srv://lab_user:" in t and "standard_srv}" in t,
         "why": "standard_srv already starts with mongodb+srv:// - the output becomes 'mongodb+srv://lab_user:...@mongodb+srv://...'",
         "fix": "build the URI from the host part of standard_srv (replace(..., \"mongodb+srv://\", \"\"))"},
        {"issue": "access list 0.0.0.0/0", "found": '"0.0.0.0/0"' in t,
         "why": "every address on the internet can reach the cluster - the password becomes the only control",
         "fix": "variable access_cidr (your public IP /32) with a validation that refuses 0.0.0.0/0"},
        {"issue": "readWriteAnyDatabase for the lab user", "found": "readWriteAnyDatabase" in t,
         "why": "more than the application needs (Lab 12: least privilege)", "fix": "readWrite on the application database only"},
        {"issue": "auto_scaling_disk_gb_enabled / cloud_backup on M0", "found": "auto_scaling_disk_gb_enabled" in t,
         "why": "not available on shared tiers; the settings are ignored or refused", "fix": "removed"},
        {"issue": "legacy resource mongodbatlas_cluster", "found": 'resource "mongodbatlas_cluster"' in t,
         "why": "the provider recommends mongodbatlas_advanced_cluster (and removes mongodbatlas_cluster in 2.x)",
         "fix": "mongodbatlas_advanced_cluster with replication_specs / region_configs"},
        {"issue": "password in the output string is not URL-encoded", "found": "${var.atlas_user_password}@" in t,
         "why": "characters like @ : / in the password break the URI", "fix": "urlencode(var.atlas_user_password)"},
    ]


def _tf(dirpath: Path, log) -> dict:
    tf = shutil.which("terraform")
    if not tf:
        return {"ran": False, "reason": "terraform is not installed in this container"}
    work = Path(tempfile.mkdtemp(prefix="tf-"))
    shutil.copy(dirpath / "main.tf", work / "main.tf")
    cache = Path(os.getenv("TF_PLUGIN_CACHE_DIR", "/tmp/terraform-plugin-cache"))
    cache.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TF_PLUGIN_CACHE_DIR": str(cache), "TF_IN_AUTOMATION": "1", "CHECKPOINT_DISABLE": "1"}
    t0 = time.time()
    p = subprocess.run([tf, "init", "-backend=false", "-input=false", "-no-color"], cwd=work, env=env, capture_output=True,
                       text=True, timeout=300)
    if p.returncode != 0:
        return {"ran": False, "reason": "terraform init failed (needs internet for the provider): " +
                (p.stderr or p.stdout).strip()[-200:]}
    v = subprocess.run([tf, "validate", "-json", "-no-color"], cwd=work, env=env, capture_output=True, text=True, timeout=120)
    try:
        d = json.loads(v.stdout)
    except ValueError:
        d = {"valid": False, "diagnostics": [{"summary": v.stdout[-200:] + v.stderr[-200:]}]}
    ver = subprocess.run([tf, "version", "-json"], cwd=work, env=env, capture_output=True, text=True).stdout
    prov = ""
    try:
        prov = json.loads(ver).get("provider_selections", {}).get("registry.terraform.io/mongodb/mongodbatlas", "")
    except ValueError:
        pass
    return {"ran": True, "valid": d.get("valid"), "errors": d.get("error_count", 0), "warnings": d.get("warning_count", 0),
            "diagnostics": [f"{x.get('severity')}: {x.get('summary')} {x.get('detail', '')[:120]}" for x in d.get("diagnostics", [])][:6],
            "terraform": (json.loads(ver).get("terraform_version") if ver.startswith("{") else ""), "provider": prov,
            "seconds": round(time.time() - t0, 1)}


def cloud(log=print) -> dict:
    res = {"review": terraform_review()}
    for r in res["review"]:
        log(f"   [{'found' if r['found'] else 'ok   '}] {r['issue']}")
    res["validate_reference"] = _tf(LAB / "terraform" / "reference", log)
    res["validate_corrected"] = _tf(LAB / "terraform", log)
    for k in ("validate_reference", "validate_corrected"):
        v = res[k]
        log(f"   terraform validate ({k.split('_')[1]}): " + (f"valid={v['valid']}, {v['errors']} errors, {v['warnings']} warnings "
            f"(terraform {v.get('terraform')}, mongodbatlas {v.get('provider')})" if v.get("ran") else v["reason"]))
    uri = os.getenv("ATLAS_URI", "").strip()
    if uri:
        try:
            t0 = time.perf_counter()
            c = MongoClient(uri, serverSelectionTimeoutMS=10000, tz_aware=True, appname="lab13-atlas")
            c.admin.command("ping")
            conn = round((time.perf_counter() - t0) * 1000)
            lat = []
            for i in range(5):
                t1 = time.perf_counter()
                c.lab13.atlas_probe.insert_one({"i": i, "at": datetime.now(UTC)})
                lat.append(round((time.perf_counter() - t1) * 1000, 1))
            h = c.admin.command("hello")
            res["atlas"] = {"connected": True, "connect_ms": conn, "insert_ms_wmajority": lat, "set": h.get("setName"),
                            "hosts": len(h.get("hosts", [])), "version": c.server_info().get("version")}
            c.lab13.atlas_probe.drop()
            c.close()
            log(f"   Atlas: connected in {conn} ms, replica set {h.get('setName')} ({len(h.get('hosts', []))} members), "
                f"insert w:majority {lat} ms")
        except Exception as exc:                      # noqa: BLE001
            res["atlas"] = {"connected": False, "error": str(exc)[:200]}
    else:
        res["atlas"] = {"connected": False, "skipped": True,
                        "reason": "ATLAS_URI not set - put the cluster_uri output of terraform in .env to run the cloud test"}
    corrected = res["validate_corrected"]
    res["pass"] = (not corrected.get("ran")) or bool(corrected.get("valid"))
    return res


# ============================================================================ website status
_cache = {"at": 0.0, "k8s": None}


def status() -> dict:
    out = {"containers": [], "available": False}
    for cn in PROD_CONTAINERS + [EXPORTER_CONTAINER, "prometheus", K3S]:
        i = inspect(cn)
        if i:
            st = i.get("State", {})
            out["containers"].append({"name": cn, "status": st.get("Status"), "health": (st.get("Health") or {}).get("Status"),
                                      "restarts": i.get("RestartCount", 0), "started": st.get("StartedAt")})
        else:
            out["containers"].append({"name": cn, "status": "not running"})
    out["hello"] = hello_all()
    av = available()
    out.update(av)
    if av["available"]:
        try:
            out["rs"] = rs_summary()
        except PyMongoError as exc:
            out["rs_error"] = str(exc)[:160]
    if time.time() - _cache["at"] > 8:
        k = {"ready": False}
        if K8S_MODE != "off" and any(c["name"] == K3S and c["status"] == "running" for c in out["containers"]):
            r = kubectl("get", "nodes", "-o", "json", timeout=8)
            if r.rc == 0:
                k = {"ready": True, "pods": pods_summary()}
        _cache.update(at=time.time(), k8s=k)
    out["k8s"] = _cache["k8s"]
    return _j(out)


def live_metrics() -> dict:
    s = scrape_exporter()
    q = {"up": prom_value('up{job="mongodb"}'), "ops": prom_value("sum by (type) (rate(mongodb_op_counters_total[1m]))"),
         "repl_ops": prom_value("sum by (type) (rate(mongodb_mongod_op_counters_repl_total[1m]))"),
         "conns": prom_value('mongodb_connections{state="current"}'), "members": prom_value("mongodb_rs_members_state"),
         "health": prom_value("mongodb_rs_members_health")}
    try:
        al = prom("/api/v1/alerts")["data"]["alerts"]
    except Exception:                                  # noqa: BLE001
        al = None
    try:
        rng = prom("/api/v1/query_range", query='sum(rate(mongodb_op_counters_total{type=~"insert|query|update|delete"}[30s])) + '
                   'sum(rate(mongodb_mongod_op_counters_repl_total{type=~"insert|update|delete"}[30s]))',
                   start=time.time() - 600, end=time.time(), step="10")["data"]["result"]
        series = [[float(t), float(v)] for t, v in rng[0]["values"]] if rng else []
    except Exception:                                  # noqa: BLE001
        series = None
    return _j({"exporter": s, "prometheus": q, "alerts": al, "ops_series": series, "prometheus_url": "http://localhost:9090"})

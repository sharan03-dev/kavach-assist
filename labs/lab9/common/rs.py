"""
Shared helpers for Exercise 9 (imported by every activity).

Connection model (same as Exercise 8)
  Replica set rs0 = mongo1:27017 (priority 2), mongo2:27018, mongo3:27019.
  Inside the lab9 container those names resolve through Docker DNS.
  On the host they resolve through the hosts-file entry added in Exercise 8.
"""
from __future__ import annotations

import csv
import os
import platform
import time
from datetime import datetime
from pathlib import Path

import pymongo
from pymongo import MongoClient, ReadPreference
from pymongo import errors as E

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
RS_URI = os.getenv(
    "MONGO_URI",
    "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0",
)
DB_NAME = os.getenv("LAB9_DB", "lab9")


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------
def seed_hosts(uri: str = RS_URI) -> list[str]:
    """'mongodb://a:1,b:2/?replicaSet=rs0' -> ['a:1', 'b:2']"""
    body = uri.split("://", 1)[1].split("/", 1)[0]
    if "@" in body:
        body = body.split("@", 1)[1]
    return [h.strip() for h in body.split(",") if h.strip()]


def replica_set_name(uri: str = RS_URI) -> str:
    if "replicaSet=" in uri:
        return uri.split("replicaSet=", 1)[1].split("&", 1)[0]
    return "rs0"


def results_file(name: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR / name


def clock() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def short(member: str | None) -> str:
    return member.split(":", 1)[0] if member else "-"


def banner(title: str, char: str = "=") -> None:
    line = char * 76
    print(f"\n{line}\n{title}\n{line}", flush=True)


def format_table(headers, rows, right=()) -> str:
    """Plain-text table. `right` = indexes of right-aligned columns."""
    cells = [[str(h) for h in headers]] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]

    def fmt(r):
        return "  ".join(
            r[i].rjust(widths[i]) if i in right else r[i].ljust(widths[i])
            for i in range(len(r))
        )

    sep = "  ".join("-" * w for w in widths)
    return "\n".join([fmt(cells[0]), sep] + [fmt(r) for r in cells[1:]])


# --------------------------------------------------------------------------
# connections
# --------------------------------------------------------------------------
def rs_client(**kw) -> MongoClient:
    """Replica-set connection: the driver discovers the topology itself."""
    kw.setdefault("serverSelectionTimeoutMS", 5000)
    return MongoClient(RS_URI, **kw)


def direct_client(hostport: str, timeout_ms: int = 1500) -> MongoClient:
    """Connection to ONE member, whatever its role (used to ask each node
    for its own view of the replica set)."""
    host, port = hostport.rsplit(":", 1)
    return MongoClient(
        host, int(port),
        directConnection=True,
        read_preference=ReadPreference.SECONDARY_PREFERRED,
        serverSelectionTimeoutMS=timeout_ms,
        connectTimeoutMS=timeout_ms,
        socketTimeoutMS=max(3 * timeout_ms, 3000),
    )


def rs_status(client: MongoClient) -> dict:
    """rs.status(). PRIMARY_PREFERRED so it still works when there is no
    primary (any secondary can answer replSetGetStatus)."""
    return client.admin.command(
        "replSetGetStatus", read_preference=ReadPreference.PRIMARY_PREFERRED
    )


def rs_config(client: MongoClient) -> dict:
    return client.admin.command(
        "replSetGetConfig", read_preference=ReadPreference.PRIMARY_PREFERRED
    )["config"]


def member_rows(status: dict):
    """Turn rs.status() into rows {name, state, health, lag_s, optime, self}
    plus the name of the PRIMARY (None if there is none)."""
    members = status["members"]
    primary = next((m for m in members if m["stateStr"] == "PRIMARY"), None)
    healthy = [m for m in members if m.get("health") == 1 and m.get("optimeDate")]
    if primary is not None:
        ref = primary.get("optimeDate")
    else:
        ref = max((m["optimeDate"] for m in healthy), default=None)
    rows = []
    for m in members:
        lag = None
        if m.get("health") == 1 and ref is not None and m.get("optimeDate") is not None:
            lag = max(0.0, (ref - m["optimeDate"]).total_seconds())
        rows.append({
            "name": m["name"],
            "state": m["stateStr"],
            "health": int(m.get("health", 0)),
            "lag_s": lag,
            "optime": (m.get("optime") or {}).get("ts"),
            "self": bool(m.get("self")),
            "sync": m.get("syncSourceHost") or m.get("syncingTo") or "",
        })
    return rows, (primary["name"] if primary else None)


def data_bearing_members(config: dict) -> list[str]:
    return [m["host"] for m in config["members"] if not m.get("arbiterOnly")]


def environment_snapshot(client: MongoClient) -> dict:
    """Facts about the deployment that the results depend on."""
    cfg = rs_config(client)
    st = rs_status(client)
    rows, primary = member_rows(st)
    settings = cfg.get("settings", {})
    try:
        dflt = client.admin.command("getDefaultRWConcern")
        default_wc = dflt.get("defaultWriteConcern", {})
        default_wc_source = dflt.get("defaultWriteConcernSource", "")
    except E.PyMongoError:
        default_wc, default_wc_source = {}, "unknown"
    return {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "mongodb_version": client.server_info().get("version"),
        "pymongo_version": pymongo.version,
        "python_version": platform.python_version(),
        "runner_platform": f"{platform.system()} {platform.machine()}",
        "replica_set": cfg["_id"],
        "primary": primary,
        "members": [
            {"host": m["host"], "priority": m.get("priority"),
             "votes": m.get("votes"), "arbiterOnly": bool(m.get("arbiterOnly"))}
            for m in cfg["members"]
        ],
        "data_bearing_members": len(data_bearing_members(cfg)),
        "write_majority_count": st.get("writeMajorityCount"),
        "write_concern_majority_journal_default":
            cfg.get("writeConcernMajorityJournalDefault", True),
        "election_timeout_ms": settings.get("electionTimeoutMillis"),
        "heartbeat_interval_ms": settings.get("heartbeatIntervalMillis"),
        "heartbeat_timeout_s": settings.get("heartbeatTimeoutSecs"),
        "default_write_concern": default_wc,
        "default_write_concern_source": default_wc_source,
        "member_states": [
            {"name": r["name"], "state": r["state"], "health": r["health"]} for r in rows
        ],
    }


# --------------------------------------------------------------------------
# error classification (Lab 9.2)
# --------------------------------------------------------------------------
_CODE_NAMES = {
    64: "WTIMEOUT",                       # WriteConcernFailed (wtimeout expired)
    79: "UNKNOWN_WRITE_CONCERN",          # UnknownReplWriteConcern
    100: "UNSATISFIABLE_WRITE_CONCERN",
    189: "PRIMARY_STEPPED_DOWN",
    11602: "INTERRUPTED_REPL_CHANGE",     # InterruptedDueToReplStateChange
    10107: "NOT_WRITABLE_PRIMARY",
    13435: "NOT_PRIMARY_NO_SECONDARY_OK",
    91: "SHUTDOWN_IN_PROGRESS",
}


def classify(exc: BaseException) -> str:
    """Short, report-friendly name for a PyMongo exception."""
    if isinstance(exc, E.WTimeoutError):
        return "WTIMEOUT"
    if isinstance(exc, E.WriteConcernError):
        return _CODE_NAMES.get(exc.code, f"WC_ERROR_{exc.code}")
    if isinstance(exc, E.NotPrimaryError):
        return "NOT_PRIMARY"
    if isinstance(exc, E.ServerSelectionTimeoutError):
        return "NO_PRIMARY"
    if isinstance(exc, (E.NetworkTimeout, E.AutoReconnect)):
        return "NETWORK_ERROR"
    if isinstance(exc, E.OperationFailure):
        return _CODE_NAMES.get(exc.code, f"FAILED_{exc.code}")
    return type(exc).__name__


# --------------------------------------------------------------------------
# shared event log (observer, writer and the shell script all append here,
# so the report can rebuild one timeline with real timestamps)
# --------------------------------------------------------------------------
EVENT_FIELDS = ["time", "epoch", "source", "event", "detail", "value"]


def log_event(source: str, event: str, detail: str = "", value="", echo: bool = True,
              at: float | None = None):
    """Append one event. `at` = epoch seconds when the event really happened
    (e.g. a timestamp read from a mongod log); default = now."""
    path = results_file(f"lab9_2_events_{source}.csv")
    new = not path.exists()
    t = time.time() if at is None else at
    with path.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(EVENT_FIELDS)
        w.writerow([datetime.fromtimestamp(t).isoformat(timespec="milliseconds"),
                    f"{t:.3f}", source, event, detail, value])
    if echo:
        stamp = datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-3]
        print(f"[{stamp}] {event:<22} {detail}", flush=True)


def load_events() -> list[dict]:
    rows = []
    for p in sorted(RESULTS_DIR.glob("lab9_2_events_*.csv")):
        with p.open(newline="") as fh:
            rows += list(csv.DictReader(fh))
    rows.sort(key=lambda r: float(r["epoch"]))
    # mongod repeats "Not starting an election ... cannot see a majority" every
    # few seconds; keep only the first of a run of identical lines per member.
    out, last = [], {}
    for r in rows:
        if r["source"] == "mongod":
            who, _, text = r["detail"].partition(": ")
            if last.get(who) == text:
                continue
            last[who] = text
        out.append(r)
    return out

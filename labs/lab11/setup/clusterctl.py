#!/usr/bin/env python3
"""clusterctl - builds the Lab 11 sharded cluster (run by the shard-init container) and shows its state.

    python labs/lab11/setup/clusterctl.py init            # initiate cfgrs, shardA, shardB, then addShard x2 (idempotent)
    python labs/lab11/setup/clusterctl.py wait-ready      # wait until mongos answers and 2 shards are registered
    python labs/lab11/setup/clusterctl.py status          # topology, like sh.status()

Topology (see docker-compose.yml):
    application -> mongos:27030 -> config server replica set  cfgrs  = cfg1:27101
                                -> shard replica set          shardA = shard-a1:27111 (primary) + shard-a2:27112
                                -> shard replica set          shardB = shard-b1:27121 (primary) + shard-b2:27122

Order matters: mongos only starts listening AFTER the config replica set has been initiated (checked on 7.0),
so this script initiates the replica sets first and then waits for mongos before calling addShard.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from pymongo.errors import OperationFailure, PyMongoError  # noqa: E402

from backend.app import lab11 as L  # noqa: E402


def say(msg: str):
    print(f"[cluster] {msg}", flush=True)


def initiate(name: str, hosts: list[str], configsvr: bool = False, timeout: int = 120) -> bool:
    """rs.initiate() once; safe to call again (an initiated set is left alone)."""
    deadline = time.time() + timeout
    first = hosts[0]
    while True:
        try:
            hello = L.direct(first).admin.command("hello")
            break
        except PyMongoError as exc:
            if time.time() > deadline:
                say(f"!! {first} not reachable: {exc}")
                return False
            time.sleep(2)
    if hello.get("setName"):
        say(f"{name}: already initiated ({', '.join(hello.get('hosts', []))})")
    else:
        conf = {"_id": name, "members": [{"_id": i, "host": h, "priority": 2 if i == 0 else 1} for i, h in enumerate(hosts)]}
        if configsvr:
            conf["configsvr"] = True
        try:
            L.direct(first).admin.command("replSetInitiate", conf)
            say(f"{name}: rs.initiate({{_id: '{name}'{', configsvr: true' if configsvr else ''}, members: {hosts}}})")
        except OperationFailure as exc:
            if exc.code != 23:                            # 23 = AlreadyInitialized
                raise
    while time.time() < deadline:
        p = L._primary_of(hosts)
        if p:
            say(f"{name}: PRIMARY is {p}")
            return True
        time.sleep(1)
    say(f"!! {name}: no primary after {timeout} s")
    return False


def wait_mongos(timeout: int = 180) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            L.client().admin.command("ping")
            say(f"mongos answers at {L.SHARD_URI} after {time.time() - t0:.0f} s")
            return True
        except PyMongoError:
            time.sleep(2)
    say(f"!! mongos not reachable at {L.SHARD_URI}")
    return False


def add_shards(timeout: int = 120) -> bool:
    c = L.client()
    have = {s["_id"] for s in c.admin.command("listShards")["shards"]}
    for name, hosts in L.shard_specs():
        if name in have:
            say(f"shard {name}: already added")
            continue
        spec = f"{name}/{','.join(hosts)}"
        t0 = time.time()
        while True:
            try:
                c.admin.command("addShard", spec, name=name)
                say(f"sh.addShard('{spec}')  ok")
                break
            except PyMongoError as exc:
                if time.time() - t0 > timeout:
                    say(f"!! addShard {spec} failed: {exc}")
                    return False
                time.sleep(2)
    return True


def cmd_init(a) -> int:
    cfg_name, cfg_hosts = L.parse_rs(L.CFG_RS)
    ok = initiate(cfg_name, cfg_hosts, configsvr=True, timeout=a.timeout)
    for name, hosts in L.shard_specs():
        ok &= initiate(name, hosts, timeout=a.timeout)
    if not ok:
        return 1
    if not wait_mongos(a.timeout):
        return 1
    if not add_shards(a.timeout):
        return 1
    cmd_status(a)
    return 0


def cmd_wait(a) -> int:
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        av = L.available()
        if av["available"]:
            say(f"sharded cluster ready: shards {av['shards']}")
            return 0
        time.sleep(2)
    say("!! " + L.available().get("reason", "not ready"))
    return 1


def cmd_status(a) -> int:
    st = L.cluster_status()
    if not st.get("available"):
        say("!! " + st.get("reason", "not available"))
        return 1
    print(f"\n--- Sharded cluster   (mongos {st['mongos_version']} at {st['router']})")
    print(f"config server replica set {st['config']['name']}: "
          + ", ".join(f"{m['name']} {m['state']}" for m in st["config"]["members"]))
    for s in st["shards"]:
        print(f"shard {s['name']:<7} {s['host']}")
        for m in s["members"]:
            print(f"      {m['name']:<18} {m['state']:<10} lag {m['lag_s'] if m['lag_s'] is not None else '-'} s")
    print(f"balancer: mode {st['balancer']['mode']}")
    if st["collections"]:
        print("sharded collections:")
        for c in st["collections"]:
            print(f"   {c['ns']:<34} key {c['key']:<38} chunks {c['chunks']}  docs {c['docs']}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["init", "wait-ready", "status"])
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args()
    sys.exit({"init": cmd_init, "wait-ready": cmd_wait, "status": cmd_status}[a.cmd](a))


if __name__ == "__main__":
    main()

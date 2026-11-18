"""
23CSE528: NoSQL Databases using MongoDB
Exercise 2 - Activity for 2.1 (Try it yourself)
Student : M Nagasai Girisharan (24BTRCL114)

Run both Atlas and Docker MongoDB at the same time. Connect to both and
compare their server version information using server_info().

Set the two connection strings before running:
    export MONGO_URI_DOCKER="mongodb://localhost:27017/"
    export MONGO_URI_ATLAS="mongodb+srv://<user>:<password>@<cluster>.mongodb.net/"
The comparison is exported to server_comparison.json.
"""

import os
import re

from bson import json_util
from pymongo import MongoClient

SERVERS = {
    "Docker": os.getenv("MONGO_URI_DOCKER", "mongodb://localhost:27017/"),
    "Atlas": os.getenv("MONGO_URI_ATLAS", ""),
}
FIELDS = ["version", "gitVersion", "bits", "maxBsonObjectSize", "javascriptEngine",
          "storageEngines", "openssl"]
HERE = os.path.dirname(os.path.abspath(__file__))


def mask(uri):
    """Hide the password when printing a connection string."""
    return re.sub(r"://([^:/]+):([^@]+)@", r"://\1:****@", uri)


def get_info(name, uri):
    if not uri:
        print(f"[SKIP] {name}: connection string not set")
        return None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=8000)
        info = client.server_info()                     # <- the required command
        hello = client.admin.command("hello")
        info["_replica_set"] = hello.get("setName", "standalone")
        info["_host"] = mask(uri)
        print(f"[OK]  Connected to {name:<7} {mask(uri)}")
        if name == "Atlas" and "mongodb.net" not in uri:
            print("      [NOTE] this is NOT a *.mongodb.net host - a local server is "
                  "standing in for Atlas")
        client.close()
        return info
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        return None


def short(v):
    if isinstance(v, dict):
        v = v.get("running", v.get("compiled", str(v)))
    if isinstance(v, list):
        v = ", ".join(map(str, v))
    return str(v)[:28]


def main():
    print("=" * 78)
    print("LAB 2.1 TRY IT YOURSELF : COMPARE ATLAS vs DOCKER server_info()")
    print("=" * 78)
    infos = {n: get_info(n, u) for n, u in SERVERS.items()}
    live = {n: i for n, i in infos.items() if i}

    print("\n" + f"{'Field':<20}" + "".join(f"{n:<30}" for n in live) + "Same?")
    print("-" * 78)
    for fld in FIELDS + ["_replica_set"]:
        vals = [short(live[n].get(fld, "-")) for n in live]
        same = "yes" if len(set(vals)) == 1 else "NO"
        print(f"{fld:<20}" + "".join(f"{v:<30}" for v in vals) + same)

    if len(live) == 2:
        a, b = (live[n]["versionArray"][:2] for n in live)
        print("\nMajor.minor:", " vs ".join(f"{n} {'.'.join(map(str, v))}"
                                          for n, v in zip(live, (a, b))))
        print("Result:", "same release line" if a == b else
              "DIFFERENT release lines - features may differ between the two servers")

    out = {n: {k: i.get(k) for k in FIELDS + ["versionArray", "_replica_set", "_host"]}
           for n, i in live.items()}
    with open(os.path.join(HERE, "server_comparison.json"), "w") as f:
        f.write(json_util.dumps(out, indent=2))
    print("[OK] Exported comparison -> server_comparison.json")


if __name__ == "__main__":
    main()

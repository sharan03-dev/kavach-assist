"""Exercise 12 - Database security & the CIA triad: run every Lab 12 task on the hardened server and save the evidence.

    python labs/lab12/run_lab12.py                 # 12.1 + activity + 12.2 + Lab 12 on Lab 7 (about 30-60 s)
    python labs/lab12/run_lab12.py --quick         # smaller copy of the Kavach data
    python labs/lab12/run_lab12.py --only 12.1     # one part:  12.1 | redaction | 12.2 | kavach
    python labs/lab12/run_lab12.py --report        # also build results/Exercise-12_Report.pdf

Needs the `mongo-secure` service of docker-compose.yml (auth + keyFile + requireTLS, client certificates) and the
certificates made by the `lab12-certs` container (labs/lab12/setup/certs.py).
Console output -> results/console_12.txt, numbers -> results/lab12_results.json and MongoDB
cti_platform.lab_results (_id "lab_12"), where the website's Lab 12 page reads them.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend.app import lab12 as L  # noqa: E402

RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
RESULTS.mkdir(parents=True, exist_ok=True)


class Tee:
    def __init__(self, path):
        self.f, self.o = open(path, "w", encoding="utf-8"), sys.stdout

    def write(self, s):
        self.o.write(s); self.f.write(s)

    def flush(self):
        self.o.flush(); self.f.flush()


def banner(t, ch="="):
    print(f"\n{ch * 76}\n{t}\n{ch * 76}")


def environment():
    st = L.status()
    if not st.get("available"):
        print("!! The secure MongoDB is not available: " + st.get("reason", "") +
              "\n   Start it with:  docker compose up -d lab12   (see README)")
        sys.exit(2)
    print(f"mongo-secure {st['version']} at {st['host']} | replica set {st['set']} | tlsMode {st['tls_mode']} | "
          f"client certificates required: {st['client_certs_required']} | auth: {st['auth']} | mechanisms: {st['mechanisms']}")
    return {"mongodb": st["version"], "host": st["host"], "set": st["set"], "tls_mode": st["tls_mode"],
            "client_certs_required": st["client_certs_required"], "mechanisms": st["mechanisms"],
            "pymongo": __import__("pymongo").version, "at": datetime.now(timezone.utc).isoformat()}


def lab_12_1():
    banner("Lab 12.1 - Configure authentication and RBAC")
    print("Server: mongod --auth (keyFile) --tlsMode requireTLS, first admin created through the localhost exception.")
    print("Create an admin + application users with different roles (least privilege), then verify every user.")
    print("\n$ mongosh (classroom snippet)\n" + "\n".join("  " + x for x in L.CLASSROOM_SNIPPET.splitlines()))
    s = L.setup_121()
    print("\nVERIFICATION MATRIX - every user x every operation, measured")
    m = L.matrix_121()
    print(f"\n  least privilege respected for every user: {m['pass']}")
    return {**s, "matrix": m}


def lab_redaction():
    banner("Activity 12.1 - Field-level redaction with a view (salary hidden from users without the HR role)")
    print("db.createView('employees_public', 'employees', [{ $project: { salary: 0 } }])")
    print("hrManager: find on employees + views     staffReader: find on the views ONLY (nothing on employees)")
    r = L.redaction()
    print(f"\n  HR sees  : {', '.join(r['fields_hr'])}")
    print(f"  staff see: {', '.join(r['fields_staff'])}")
    return r


def lab_12_2():
    banner("Lab 12.2 - TLS / encryption in transit")
    print("mongod --tlsMode requireTLS --tlsCertificateKeyFile server.pem --tlsCAFile ca.crt  (+ client certificates)")
    print("Certificates: labs/lab12/setup/certs.py (cryptography) = labs/lab12/openssl_certs.sh (the 3 OpenSSL steps)\n")
    r = L.tls_tests()
    print("\nCERTIFICATE CHAIN")
    for c in r["chain"]["certs"]:
        print(f"  {c['file']:<13} {c['subject']}\n  {'':<13} issued by {c['issuer_cn']}, RSA {c['key_bits']}, {c['signature']}, "
              f"CA={c['is_ca']}, valid until {c['not_after'][:10]}" + (f", SAN {', '.join(c['san'])}" if c["san"] else ""))
    print(f"\n  connections by TLS version on the server: {r['transport_security']}")
    return r


def lab_kavach(quick):
    banner("Lab 12 on Lab 7 - the Kavach CTI platform on the secure server")
    print("Least-privilege service accounts for the website (analyst_app), the 5-minute updater (ingest_app) and the")
    print("Lab 10 classifier billing (finance_app); masked customer view; schema validation; CSFLE; audit trail.\n")
    s = L.setup_kavach(quick)
    print("\nKAVACH PERMISSION MATRIX")
    m = L.matrix_kavach()
    print("\nINTEGRITY - schema validation also stops users who MAY write")
    i = L.integrity()
    print("\nAUTHENTICATION HARDENING - weak passwords, SHA-1, anonymous, IP allow-list")
    ip = L.ip_allowlist()
    print("\nCONFIDENTIALITY AGAINST INSIDERS - Client-Side Field Level Encryption of UPI ids")
    f = L.csfle_demo()
    print("\nACCOUNTABILITY - change stream (what changed) + profiler (who did it)")
    a = L.audit()
    print("\nLAB 7 AUTO-UPDATER -> secure server (as ingest_app over TLS)")
    from backend.app.db import get_db
    sync = L.sync_secure(get_db(), log=lambda x: print("   " + x))
    feed = L.analyst_feed(5)
    print("\nWebsite read path (analyst_app): newest reports")
    for r in feed["reports"]:
        print(f"   {str(r.get('published_at'))[:10]}  {str(r.get('title', ''))[:90]}")
    return {**s, "matrix": m, "integrity": i, "ip_weak": ip, "csfle": f, "audit": a, "sync": sync, "feed": feed}


def checks(out):
    res = []
    if "lab_12_1" in out:
        r = out["lab_12_1"]
        res += [("12.1: classroom createRole with action 'aggregate' is refused by MongoDB (BadValue)", r["snippet"]["result"] != "allowed"),
                ("12.1: every user can do exactly what its role allows (verification matrix)", r["matrix"]["pass"])]
    if "redaction" in out:
        r = out["redaction"]
        res += [("Activity: staff read employees_public without salary, the collection is not authorized", r["pass"])]
    if "lab_12_2" in out:
        r = out["lab_12_2"]
        res += [("12.2: plain, untrusted, no-certificate and rogue-certificate connections are refused", all(t["ok"] for t in r["tests"][:4])),
                (f"12.2: TLS connection works ({r['handshake']['protocol']}) and find() succeeds", r["tests"][4]["ok"] and r["tests"][5]["ok"]),
                ("12.2: server and client certificates are signed by the Kavach CA, the rogue one is not", r["pass"])]
    if "kavach" in out:
        k = out["kavach"]
        res += [("12 on 7: Kavach data copied to the secure server (verified id checksums)", all(c["verified"] for c in k["copies"])),
                ("12 on 7: analyst / ingest / finance accounts have least privilege", k["matrix"]["pass"]),
                ("12 on 7: schema validation rejects bad IOCs, negative wallets, a 4th free trial", k["integrity"]["pass"]),
                ("12 on 7: weak passwords, SCRAM-SHA-1, anonymous and off-list IPs are refused", k["ip_weak"]["pass"]),
                ("12 on 7: UPI ids are encrypted client-side; root sees only ciphertext", k["csfle"].get("pass", False)),
                ("12 on 7: change stream + profiler record what changed and who did it", k["audit"].get("pass", False))]
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", choices=["12.1", "redaction", "12.2", "kavach"])
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    sys.stdout = Tee(RESULTS / ("console_12.txt" if not a.only else f"console_12_{a.only}.txt"))
    print(f"root@lab12-runner:/app# lab12 {'--only ' + a.only if a.only else 'all'}{' --quick' if a.quick else ''}")
    env = environment()

    path = RESULTS / "lab12_results.json"
    out = {}
    if a.only and path.exists():
        try:
            out = json.loads(path.read_text())
        except Exception:                                 # noqa: BLE001
            out = {}
    out.update({"environment": env, "quick": a.quick})
    if a.only in (None, "12.1"):
        out["lab_12_1"] = lab_12_1()
    if a.only in (None, "redaction"):
        if "lab_12_1" not in out:
            L.setup_121(log=lambda *_: None)
        out["redaction"] = lab_redaction()
    if a.only in (None, "kavach"):
        out["kavach"] = lab_kavach(a.quick)
    if a.only in (None, "12.2"):
        out["lab_12_2"] = lab_12_2()

    banner("THREAT MODEL - attack surface -> MongoDB security controls -> CIA triad (measured)")
    tm = L.threat_model(out)
    for t in tm["threats"]:
        print(f"  {t['threat']:<26} ({t['cause']:<22}) -> {t['control']:<44} {'MITIGATED' if t['mitigated'] else 'open'}")
    print()
    for c in tm["controls"]:
        state = "n/a " if c["ok"] is None else ("PASS" if c["ok"] else "FAIL")
        print(f"  [{state}] {c['control']:<36} {c['cia']:<16} {c['evidence']}")
    out["threat_model"] = tm

    banner("SUMMARY")
    ok = True
    for name, v in checks(out):
        print(f"  [{'PASS' if v else 'FAIL'}] {name}")
        ok &= bool(v)
    out["checks"] = [{"name": n, "ok": bool(v)} for n, v in checks(out)]
    out = json.loads(json.dumps(out, default=str))
    path.write_text(json.dumps(out, indent=2))
    try:
        from backend.app.db import get_db
        get_db().lab_results.replace_one({"_id": "lab_12"}, {"_id": "lab_12", "ran_at": datetime.now(timezone.utc), **out},
                                         upsert=True)
    except Exception as exc:                              # noqa: BLE001
        print(f"(could not store results in MongoDB rs0: {str(exc)[:120]})")
    print("\nSaved results/lab12_results.json, results/console_12.txt and MongoDB cti_platform.lab_results (_id lab_12)")

    if a.report and not a.only:
        sys.stdout.flush()
        from make_report import build
        pdf = build(out, RESULTS, os.getenv("DOCKER_URL", ""))
        print(f"Report written: {pdf}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()

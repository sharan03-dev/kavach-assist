#!/usr/bin/env python3
"""Lab 12.2 automation - generate the TLS certificate chain with the `cryptography` library.

    python labs/lab12/setup/certs.py init  [--out /certs]     # create everything once (kept if it already exists)
    python labs/lab12/setup/certs.py show  [--out /certs]     # print the certificate chain
    python labs/lab12/setup/certs.py rotate [--out /certs]    # new server + client certificates, same CA

What it writes (the `lab12-certs` container runs `init` into the shared Docker volume /certs):

    ca.key / ca.crt          Kavach-MongoDB-CA (RSA 4096, self-signed, CA:TRUE, 365 days)       = openssl genrsa + req -x509
    server.key / server.crt  CN=mongo-secure, SAN DNS:mongo-secure, DNS:localhost, IP:127.0.0.1 = openssl req + x509 -req
    server.pem               server.key + server.crt  (what mongod --tlsCertificateKeyFile wants)
    client.key / client.crt  CN=kavach-app (clientAuth)        client.pem = key + cert  (PyMongo tlsCertificateKeyFile)
    rogue-ca.crt, rogue-client.pem   a second, untrusted CA and a client cert it signed (to prove it is rejected)
    keyfile                  internal authentication of the replica set rs12 (keyFile implies --auth)
    admin.pwd                password of kavach_admin (read by bootstrap.sh inside the mongo:7.0 container)
    secrets.json             passwords of every lab user (random, 24 characters) - mode 600
    master.key               96-byte local master key of Client-Side Field Level Encryption (Kavach UPI ids)
    bootstrap.sh             first boot: rs.initiate + first admin user over the localhost exception, then
                             restart with --auth --keyFile --tlsMode requireTLS
The same chain can be made by hand with labs/lab12/openssl_certs.sh (the three OpenSSL commands of the solution).
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import ipaddress
import json
import os
import secrets
import string
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_OUT = os.getenv("CERTS_DIR", "/certs")
DAYS = 365
# mongod also connects to ITSELF over TLS using its replica-set member name (index builds, the replica-set monitor), so
# the member host (mongo-secure) must be in the SAN - not only the names the clients use. Extra names: CERT_EXTRA_SANS.
SERVER_NAMES = ["mongo-secure", "localhost"] + [n.strip() for n in os.getenv("CERT_EXTRA_SANS", "").split(",") if n.strip()]
SERVER_IPS = ["127.0.0.1"]
USERS = ["kavach_admin", "sec_admin", "app_user_read", "app_user_write", "analytics_app", "hr_user", "staff_user",
         "analyst_app", "ingest_app", "finance_app", "ip_locked"]

BOOTSTRAP = r"""#!/bin/bash
# Written by labs/lab12/setup/certs.py - runs INSIDE the official mongo:7.0 image as the container command.
# First boot : mongod WITHOUT network access (127.0.0.1, no TLS) -> rs.initiate + the first admin user through the
#              localhost exception -> shut down.   Every boot : mongod --auth (keyFile) --tlsMode requireTLS.
set -euo pipefail
PORT="${SECURE_PORT:-27040}"
HOSTNAME_RS="${SECURE_MEMBER:-mongo-secure:27040}"
DATA="${SECURE_DBPATH:-/data/db}"
CERTS="${CERTS_DIR:-/certs}"
cp "$CERTS/keyfile" /tmp/kavach-keyfile && chmod 400 /tmp/kavach-keyfile
COMMON=(--dbpath "$DATA" --port "$PORT" --replSet rs12 --keyFile /tmp/kavach-keyfile --wiredTigerCacheSizeGB 0.25)
if [ ! -f "$DATA/.kavach-secured" ]; then
  echo "[mongo-secure] first boot: initialising rs12 and the admin user (localhost exception, no TLS yet)"
  # bound to localhost AND the member host name: rs.initiate checks that 'mongo-secure' is this node by looking at the
  # bound addresses. --keyFile already enforces authorization, so only a localhost client may create the first user.
  mongod "${COMMON[@]}" --bind_ip "127.0.0.1,${HOSTNAME_RS%%:*}" --fork --logpath /tmp/bootstrap.log
  mongosh --port "$PORT" --quiet --eval "try { rs.initiate({_id: 'rs12', members: [{_id: 0, host: '$HOSTNAME_RS'}]}) } catch (e) { print(e.message) }"
  PRIMARY=no
  for i in $(seq 1 60); do
    if [ "$(mongosh --port "$PORT" --quiet --eval 'db.hello().isWritablePrimary' 2>/dev/null)" = "true" ]; then PRIMARY=yes; break; fi
    sleep 1
  done
  if [ "$PRIMARY" != yes ]; then
    echo "[mongo-secure] rs12 did not become primary - see /tmp/bootstrap.log"; tail -20 /tmp/bootstrap.log
    mongod --dbpath "$DATA" --shutdown || true; exit 1
  fi
  mongosh --port "$PORT" --quiet --eval "db.getSiblingDB('admin').createUser({user: 'kavach_admin', pwd: '$(cat "$CERTS/admin.pwd")', roles: ['root'], mechanisms: ['SCRAM-SHA-256']}); print('kavach_admin created')"
  mongod --dbpath "$DATA" --shutdown || mongosh --port "$PORT" --quiet --eval "db.getSiblingDB('admin').shutdownServer()" || true
  sleep 1
  touch "$DATA/.kavach-secured"
fi
echo "[mongo-secure] starting with --auth (keyFile), --tlsMode requireTLS, client certificates required"
exec mongod "${COMMON[@]}" --bind_ip_all \
  --tlsMode requireTLS --tlsCertificateKeyFile "$CERTS/server.pem" --tlsCAFile "$CERTS/ca.crt" \
  --setParameter authenticationMechanisms=SCRAM-SHA-256
"""


# ----------------------------------------------------------------------------- helpers
def _key(bits: int = 4096):
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def _name(cn: str, ou: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
                      x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Karnataka"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Kavach CTI"),
                      x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ou),
                      x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _pem_key(k) -> bytes:
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                           serialization.NoEncryption())


def _pem_cert(c) -> bytes:
    return c.public_bytes(serialization.Encoding.PEM)


def _write(p: Path, data: bytes | str, mode: int = 0o644):
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    os.chmod(p, mode)


def make_ca(cn: str = "Kavach-MongoDB-CA", bits: int = 4096):
    """= openssl genrsa -out ca.key 4096 && openssl req -new -x509 -days 365 -key ca.key -out ca.crt -subj /CN=..."""
    key = _key(bits)
    now = dt.datetime.now(dt.timezone.utc)
    name = _name(cn, "Certificate Authority")
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    return key, cert


def make_leaf(ca_key, ca_cert, cn: str, ou: str, *, server: bool, bits: int = 4096):
    """= openssl genrsa + openssl req -new (CSR) + openssl x509 -req -CA ca.crt -CAkey ca.key -extfile (SAN)."""
    key = _key(bits)
    now = dt.datetime.now(dt.timezone.utc)
    csr = x509.CertificateSigningRequestBuilder().subject_name(_name(cn, ou)).sign(key, hashes.SHA256())   # server.csr
    b = (x509.CertificateBuilder().subject_name(csr.subject).issuer_name(ca_cert.subject).public_key(csr.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
         .not_valid_after(now + dt.timedelta(days=DAYS))
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, key_cert_sign=False, crl_sign=False,
                                      content_commitment=False, data_encipherment=False, key_agreement=False,
                                      encipher_only=False, decipher_only=False), critical=True)
         .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False))
    if server:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in SERVER_NAMES] +
                                                        [x509.IPAddress(ipaddress.ip_address(i)) for i in SERVER_IPS]),
                            critical=False)
        b = b.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
    else:
        b = b.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
    return key, b.sign(ca_key, hashes.SHA256())


def _password(n: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "-_"     # no quotes: the bootstrap script embeds it in JS
    return "".join(secrets.choice(alphabet) for _ in range(n))


# ----------------------------------------------------------------------------- commands
def init(out: Path, force: bool = False, log=print) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    if (out / "ca.crt").exists() and (out / "secrets.json").exists() and not force:
        _write(out / "bootstrap.sh", BOOTSTRAP, 0o755)          # always refresh the script (code may be newer)
        pw = json.loads((out / "secrets.json").read_text())
        missing = [u for u in USERS if u not in pw]
        if missing:                                              # volume made by an older version of this script
            pw.update({u: _password() for u in missing})
            _write(out / "secrets.json", json.dumps(pw, indent=2), 0o600)
        if not (out / "master.key").exists():
            _write(out / "master.key", secrets.token_bytes(96), 0o600)
        log(f"[certs] certificates already exist in {out} - keeping them (use --force to recreate)")
        return pw
    log("[certs] 1/3 CA key + self-signed CA certificate (RSA 4096, 365 days)")
    ca_key, ca = make_ca()
    log("[certs] 2/3 server key + CSR, signed by the CA (SAN: " + ", ".join(SERVER_NAMES + SERVER_IPS) + ")")
    s_key, s_crt = make_leaf(ca_key, ca, "mongo-secure", "Database", server=True)
    log("[certs] 3/3 client key + certificate for PyMongo / mongosh (clientAuth)")
    c_key, c_crt = make_leaf(ca_key, ca, "kavach-app", "Applications", server=False)
    log("[certs] extra: a second, UNTRUSTED CA and a client certificate signed by it (must be rejected)")
    r_key, r_ca = make_ca("Rogue-Attacker-CA", bits=2048)
    rc_key, rc_crt = make_leaf(r_key, r_ca, "kavach-app", "Applications", server=False, bits=2048)

    _write(out / "ca.key", _pem_key(ca_key), 0o600)
    _write(out / "ca.crt", _pem_cert(ca))
    _write(out / "server.key", _pem_key(s_key), 0o600)
    _write(out / "server.crt", _pem_cert(s_crt))
    _write(out / "server.pem", _pem_key(s_key) + _pem_cert(s_crt), 0o644)       # read by mongod in another container
    _write(out / "client.key", _pem_key(c_key), 0o600)
    _write(out / "client.crt", _pem_cert(c_crt))
    _write(out / "client.pem", _pem_key(c_key) + _pem_cert(c_crt), 0o644)
    _write(out / "rogue-ca.crt", _pem_cert(r_ca))
    _write(out / "rogue-client.pem", _pem_key(rc_key) + _pem_cert(rc_crt), 0o644)
    _write(out / "keyfile", base64.b64encode(secrets.token_bytes(756))[:1000], 0o400)
    pw = {u: _password() for u in USERS}
    _write(out / "admin.pwd", pw["kavach_admin"], 0o600)
    _write(out / "secrets.json", json.dumps(pw, indent=2), 0o600)
    _write(out / "master.key", secrets.token_bytes(96), 0o600)        # CSFLE local master key (96 bytes)
    _write(out / "bootstrap.sh", BOOTSTRAP, 0o755)
    log(f"[certs] written to {out}: ca.crt, server.pem, client.pem, rogue-*.pem, keyfile, secrets.json, bootstrap.sh")
    return pw


def describe(path: Path) -> dict:
    """Readable summary of one certificate (used by the website, the run script and the report)."""
    c = x509.load_pem_x509_certificate(path.read_bytes())
    try:
        san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        sans = [f"DNS:{n}" for n in san.get_values_for_type(x509.DNSName)] + \
               [f"IP:{i}" for i in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        sans = []
    try:
        bc = c.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound:
        bc = False
    try:
        eku = [o._name for o in c.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value]
    except x509.ExtensionNotFound:
        eku = []
    return {"file": path.name, "subject": c.subject.rfc4514_string(), "issuer": c.issuer.rfc4514_string(),
            "cn": c.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
            "issuer_cn": c.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
            "serial": format(c.serial_number, "x")[:16] + "...", "not_before": c.not_valid_before_utc.isoformat(),
            "not_after": c.not_valid_after_utc.isoformat(), "key_bits": c.public_key().key_size,
            "signature": c.signature_hash_algorithm.name.upper() + " with RSA", "is_ca": bc, "san": sans, "eku": eku,
            "sha256": c.fingerprint(hashes.SHA256()).hex(":").upper()[:47] + "...",
            "self_signed": c.subject == c.issuer}


def verify_chain(out: Path) -> list[dict]:
    """Check that every leaf certificate was really signed by the CA (signature + issuer + validity)."""
    from cryptography.hazmat.primitives.asymmetric import padding
    ca = x509.load_pem_x509_certificate((out / "ca.crt").read_bytes())
    res = []
    for name in ("server.crt", "client.crt", "rogue-client.pem"):
        p = out / name
        if not p.exists():
            continue
        c = x509.load_pem_x509_certificate(p.read_bytes())
        try:
            ca.public_key().verify(c.signature, c.tbs_certificate_bytes, padding.PKCS1v15(), c.signature_hash_algorithm)
            sig = True
        except Exception:                                  # noqa: BLE001
            sig = False
        now = dt.datetime.now(dt.timezone.utc)
        res.append({"cert": name, "issuer_matches_ca": c.issuer == ca.subject, "signature_by_ca": sig,
                    "valid_now": c.not_valid_before_utc <= now <= c.not_valid_after_utc,
                    "trusted": sig and c.issuer == ca.subject})
    return res


def show(out: Path):
    for f in ("ca.crt", "server.crt", "client.crt", "rogue-ca.crt"):
        if (out / f).exists():
            d = describe(out / f)
            print(f"{f:<14} subject  {d['subject']}\n{'':<14} issuer   {d['issuer']}\n{'':<14} RSA {d['key_bits']}, "
                  f"{d['signature']}, CA={d['is_ca']}, valid until {d['not_after'][:10]}"
                  + (f"\n{'':<14} SAN      {', '.join(d['san'])}" if d["san"] else "") + "\n")
    for v in verify_chain(out):
        print(f"verify {v['cert']:<17} signed by Kavach-MongoDB-CA: {v['trusted']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["init", "show", "rotate"])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    if a.cmd == "init":
        init(out, a.force)
        show(out)
    elif a.cmd == "rotate":
        if not (out / "ca.key").exists():
            sys.exit("no CA yet - run init first")
        ca_key = serialization.load_pem_private_key((out / "ca.key").read_bytes(), None)
        ca = x509.load_pem_x509_certificate((out / "ca.crt").read_bytes())
        s_key, s_crt = make_leaf(ca_key, ca, "mongo-secure", "Database", server=True)
        c_key, c_crt = make_leaf(ca_key, ca, "kavach-app", "Applications", server=False)
        _write(out / "server.crt", _pem_cert(s_crt)); _write(out / "server.pem", _pem_key(s_key) + _pem_cert(s_crt))
        _write(out / "client.crt", _pem_cert(c_crt)); _write(out / "client.pem", _pem_key(c_key) + _pem_cert(c_crt))
        print("rotated server + client certificates (restart mongo-secure to load the new server certificate)")
        show(out)
    else:
        show(out)


if __name__ == "__main__":
    main()

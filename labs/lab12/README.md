# Exercise 12 - Database Security & the CIA Triad

Everything runs on a **hardened MongoDB**, `mongo-secure` (started by `docker compose up -d lab12`):

```
mongod --replSet rs12 --keyFile ...            # keyFile => authorization on (one-member replica set: change streams work)
       --tlsMode requireTLS --tlsCertificateKeyFile server.pem --tlsCAFile ca.crt   # TLS only, client certificates required
       --setParameter authenticationMechanisms=SCRAM-SHA-256                        # SHA-1 and X.509 log-ins off
```
The first administrator (`kavach_admin`) is created by `bootstrap.sh` while mongod listens only on 127.0.0.1 without
TLS (the localhost exception); then the server restarts in secure mode. Port 27040 on your computer.

| File | What it does |
|---|---|
| `setup/certs.py` | Lab 12.2 automation (Python `cryptography`): CA, server + client certificates, rogue CA, keyfile, random passwords, CSFLE master key, `bootstrap.sh`. Run by the `lab12-certs` container into the volume `/certs` |
| `openssl_certs.sh` | the same chain with the OpenSSL commands of the solution |
| `run_lab12.py` | runs 12.1, the activity, 12.2 and Lab 12 on Lab 7; saves `results/console_12.txt`, `results/lab12_results.json`; `--report` builds the PDF |
| `make_report.py` | `results/Exercise-12_Report.pdf` (Docker Hub URL on page 1) |
| `mongosh/*.js` | the 12.1 and activity commands in mongosh form |
| `lab12` | command menu: `lab12 all / 12.1 / redaction / 12.2 / kavach / shell USER / plain / password USER / certs / report URL` |
| `../../backend/app/lab12.py` | all the logic, shared by the scripts and the website page `#/lab12` |

## Lab 12.1 - authentication and RBAC
Admin (`kavach_admin`, root) + a security officer (`sec_admin`, userAdminAnyDatabase) + `app_user_read` (find on
products/orders) + `app_user_write` (find/insert/update/remove on orders) + `analytics_app` (the classroom role) +
`hr_user` / `staff_user` for the activity. A matrix logs in as every user and tries 10 operations; each result is
compared with the expected least-privilege answer.

## Activity - field-level redaction
`employees_public` = `employees` without `salary`. `staffReader` has find on the view only. Measured: staff cannot read
the collection, cannot filter/sort on salary through the view, `$lookup` back to employees is refused. Weakness found:
an exclusion view leaks a field HR adds later (`bank_account`); the allow-list view `employees_directory` does not.

## Lab 12.2 - TLS
Refused: plain TCP, TLS without our CA, TLS without a client certificate, a certificate from a rogue CA. Works:
TLS 1.3 (TLS_AES_256_GCM_SHA384) with CA + client certificate, then `find()` as `analyst_app`. The chain is verified
(issuer + signature) and documented in the report.

## Lab 12 applied to Lab 7 (mentor's rule)
The Kavach data is copied to `cti_secure` (id checksums verified) and the Lab 10 billing to `kavach_billing`:
- `analyst_app` (website): find on CTI collections + the masked customer view only
- `ingest_app` (5-minute auto-updater): find/insert/update, no delete, no drop - the updater syncs every cycle as this user
- `finance_app` (billing): accounts/payments, allowed only from private networks (`authenticationRestrictions`)
- `$jsonSchema` validators: IOC types, value required, wallet >= 0, at most 3 free trials
- Client-Side Field Level Encryption of UPI ids: even root reads only ciphertext
- audit: profiler (who did what) + change stream with pre-images (what changed)
- weak passwords, SCRAM-SHA-1, anonymous access and an off-list IP are all refused

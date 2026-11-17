# Lab 15 - Designing and Architecting a MongoDB-Backed Product as a Service

**TaskTracker** - a multi-tenant task-management SaaS (FastAPI + MongoDB). Following the mentor's rule it runs on the lower labs:
the **Lab 13 production replica set**, a **Lab 12**-style least-privilege user, a **Lab 10** transaction for signup, **Lab 9** `w: majority`,
**Lab 14** hourly backups / monitoring / per-tenant restores, and the **Lab 7** website (mounted at `/tasktracker`).

| File | What it is |
|---|---|
| `tasktracker/app.py` | **Lab 15.1** the API: JWT (HS256) tenants, CRUD where every query goes through `scoped()` = `{tenant_id from the token, ...}`, rate limiting with a MongoDB counter (`find_one_and_update` + `$inc` + upsert, TTL index), `$jsonSchema` validators, compound indexes. **Activity**: RBAC (`admin` manages every task of the tenant, `member` only their own). |
| `migrate_tenants.py` | **Lab 15.2** (`--uri`): seed 5 x 100, migrate with `$match + $out` (and a Python loop), validate (counts, foreign docs, same `_id`s, sha256, negative control), compare list / create / cross-tenant analytics at 5x100, 5x20,000 and 100x100, one-tenant backup + restore, schema evolution, recommendation. |
| `sample_app.py` | the classroom sample, **unchanged** - tested against the same database. |
| `run_lab15.py` / `lab15` / `make_report.py` | runner, command menu, `results/Exercise-15_Report.pdf`. |
| `../../backend/app/lab15.py`, `lab15_api.py`, `frontend/assets/lab15.js` | tests through the real HTTP layer, website API, the product UI. |

## Run
```bash
docker compose up -d --build lab15          # 7 -> 8 -> ... -> 14 -> 15
open http://localhost:8000/#/lab15          # TaskTracker app (sign in as any demo user) + every test
open http://localhost:8000/tasktracker/docs # Swagger UI (its scripts come from a CDN: needs internet)
docker compose exec lab15 lab15 all         # every task + the PDF
docker compose exec lab15 lab15 users       # demo tenants / users  (password: Lab15-demo!)
TOKEN=$(docker compose exec lab15 lab15 token acme ravi@acme.test | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/tasktracker/tasks/
```
Demo tenants: **acme** (free plan, 60 req/min: Asha admin, Ravi + Meera members), **globex** (pro, 600/min), **initech** (free).

## API
| Method | Path | Who | Notes |
|---|---|---|---|
| POST | /auth/signup | anyone | tenant + first admin in **one transaction** |
| POST | /auth/login | anyone | `{tenant_id, email, password}` -> JWT (8 h) |
| GET | /me, /stats | any user | stats = `$match: {tenant_id}` first |
| POST, GET | /tasks/ | any user | list: `?status=`, `?mine=true`, `limit`, `skip` |
| GET, PATCH, DELETE | /tasks/{id} | read: tenant; write: owner or admin | other tenant -> 404, same tenant not owner -> 403 |
| GET, POST | /users/ | admin | |
Every endpoint is rate limited per tenant (headers `X-RateLimit-*`, `429` + `Retry-After`).

## The sample, tested (measured)
- `POST /tasks/` answers **HTTP 500 after the insert succeeded**: `insert_one` adds an ObjectId `_id` to the dict, FastAPI cannot serialise it -> clients retry -> duplicates.
- `priority: "urgent!!"` is stored (plain `str`, no validator).
- `GET /tasks/?tenant_id=acme` **without any login** returns Acme's tasks: the tenant is a query parameter.
- PyMongo inside `async def` blocks the event loop; TaskTracker uses `def` endpoints (FastAPI thread pool).

## Notes on correctness (measured on MongoDB 7.0.34)
- `readWrite` cannot run `collMod`: the app user cannot change or switch off its validators - schema setup is a deploy-time job (admin).
- The signup transaction rolls back only because the unique `(tenant_id, email)` index makes the second insert fail - create indexes first.
- `$out` does not copy the source's indexes (after `$out`: only `_id_`); `$out` into an existing collection keeps its old indexes.
- With `(tenant_id, created_at)` both layouts examine exactly the documents they return: single-tenant listing is only marginally faster per collection.
- Cross-tenant analytics: one `$group` beats `$unionWith`, about 2x at 100 tenants.
- `mongorestore --drop` of a one-tenant dump on the shared collection **deleted the other 4 tenants** - restore a tenant without `--drop`.
- 200 concurrent requests against a limit of 50 -> exactly 50 allowed: `$inc` in findAndModify is atomic.

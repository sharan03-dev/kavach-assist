"""Lab 10 - multi-document transactions on a MongoDB replica set.

One module, used by the website API (backend/app/labs_api.py) AND by the command-line scripts
(labs/lab10/*.py), so what you demonstrate in the browser is exactly what the scripts run.

Lab 10.1  bank transfer      debit + credit + history record = ONE atomic transaction
Lab 10.2  inventory reserve  stock decrement + order insert = ONE atomic transaction, many threads

Three points where this code is deliberately *different* from the classroom sample text:

1. A filter such as {"balance": {"$gte": amount}} does NOT make update_one fail when nothing
   matches - it just matches 0 documents and returns normally. The sample multi_document_transaction.py
   would therefore still credit the other account (money out of thin air). Here we check
   result.matched_count and raise InsufficientFunds ourselves, so the whole transaction aborts.
2. There is no class pymongo.errors.WriteConflict. A write conflict reaches Python as an
   OperationFailure (code 112) that carries the error label "TransientTransactionError".
   We retry on that label, which is also what MongoDB's own documentation recommends.
3. MongoDB has no lock-wait deadlocks between transactions: the second writer to touch a document
   is aborted immediately with a write conflict. "Deadlock handling" therefore means
   "retry the aborted transaction with a short, growing, randomised delay".
"""
from __future__ import annotations

import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from pymongo import MongoClient, ReadPreference
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

BANK_DB = "lab10_bank"
SHOP_DB = "lab10_shop"
UTC = timezone.utc


# ----------------------------------------------------------------------------- exceptions
class InsufficientFunds(Exception):
    """Debit matched no document: balance too low or unknown account."""


class UnknownAccount(Exception):
    pass


class OutOfStock(Exception):
    pass


class UnknownProduct(Exception):
    pass


class SimulatedCrash(Exception):
    """Raised on purpose after the first write to prove that the transaction rolls back."""


class StaleVersion(Exception):
    """The version number changed between our read and our write (optimistic locking)."""


# ----------------------------------------------------------------------------- transaction runner
def is_transient(exc: BaseException) -> bool:
    return isinstance(exc, PyMongoError) and exc.has_error_label("TransientTransactionError")


def is_write_conflict(exc: BaseException) -> bool:
    return isinstance(exc, OperationFailure) and (exc.code == 112 or exc.has_error_label("TransientTransactionError"))


def run_txn(client: MongoClient, body, *, max_retries: int = 5, base_delay: float = 0.1,
            jitter: bool = True, tag: str = "txn") -> dict:
    """Run body(session) inside a transaction. Returns {"value", "attempts", "conflicts", "delays"}.

    * TransientTransactionError (write conflict, primary change)  -> abort, sleep base*2**n, retry
    * UnknownTransactionCommitResult                              -> retry only the COMMIT
    * any other exception (InsufficientFunds, OutOfStock, ...)    -> abort and re-raise unchanged
    The exception that finally escapes carries .txn_stats = {"attempts", "conflicts", "delays"}.
    """
    stats = {"attempts": 0, "conflicts": 0, "delays": []}
    while True:
        stats["attempts"] += 1
        with client.start_session() as session:
            try:
                session.start_transaction(read_concern=ReadConcern("snapshot"),
                                          write_concern=WriteConcern("majority"))
                value = body(session)
                while True:                                   # commit, retrying only the commit
                    try:
                        session.commit_transaction()
                        break
                    except PyMongoError as exc:
                        if exc.has_error_label("UnknownTransactionCommitResult"):
                            time.sleep(0.05)
                            continue
                        raise
                return {"value": value, **stats}
            except BaseException as exc:                      # noqa: BLE001 - we re-raise below
                try:
                    if session.in_transaction:
                        session.abort_transaction()
                except PyMongoError:
                    pass
                retryable = is_transient(exc) or isinstance(exc, StaleVersion)
                if retryable and stats["attempts"] <= max_retries:
                    stats["conflicts"] += 1
                    delay = base_delay * (2 ** (stats["attempts"] - 1))        # 0.1, 0.2, 0.4, 0.8 ...
                    if jitter:
                        delay *= random.uniform(0.5, 1.0)
                    stats["delays"].append(round(delay, 3))
                    time.sleep(delay)
                    continue
                try:
                    exc.txn_stats = stats                     # type: ignore[attr-defined]
                except Exception:                              # noqa: BLE001
                    pass
                raise


def _stats_of(exc: BaseException) -> dict:
    return getattr(exc, "txn_stats", {"attempts": 1, "conflicts": 0, "delays": []})


# ============================================================================= Lab 10.1 - bank
def seed_bank(client: MongoClient, balances=(1000, 500)) -> dict:
    db = client[BANK_DB]
    db.accounts.drop()
    db.transaction_history.drop()
    db.accounts.create_index("account_id", unique=True)
    db.get_collection("accounts", write_concern=WriteConcern("majority")).insert_many([
        {"account_id": "ACC-001", "owner": "Asha", "balance": balances[0], "version": 0},
        {"account_id": "ACC-002", "owner": "Ravi", "balance": balances[1], "version": 0},
    ])
    return bank_state(client)


def bank_state(client: MongoClient) -> dict:
    db = client[BANK_DB].with_options(read_preference=ReadPreference.PRIMARY,
                                      read_concern=ReadConcern("majority"))
    accounts = list(db.accounts.find({}, {"_id": 0}).sort("account_id", 1))
    history = list(db.transaction_history.find({}, {"_id": 0}).sort("timestamp", -1).limit(12))
    return {"accounts": accounts, "history": history,
            "total": sum(a["balance"] for a in accounts)}


def transfer(client: MongoClient, src: str, dst: str, amount: int, *, retries: int = 5,
             base_delay: float = 0.1, crash_after_debit: bool = False, hold_seconds: float = 0.0,
             sleep_log=None) -> dict:
    """Atomic transfer. Returns a result dict; never raises for business errors."""
    t0 = time.perf_counter()
    db = client[BANK_DB]

    def body(s):
        r1 = db.accounts.update_one({"account_id": src, "balance": {"$gte": amount}},
                                    {"$inc": {"balance": -amount, "version": 1}}, session=s)
        if r1.matched_count == 0:       # <- the check the sample code is missing
            exists = db.accounts.count_documents({"account_id": src}, session=s, limit=1)
            raise InsufficientFunds(f"{src} has less than {amount}" if exists else f"unknown account {src}")
        if hold_seconds:                # keeps the transaction open so another one can collide with it
            time.sleep(hold_seconds)
        if crash_after_debit:
            raise SimulatedCrash("process crashed after the debit, before the credit")
        r2 = db.accounts.update_one({"account_id": dst},
                                    {"$inc": {"balance": amount, "version": 1}}, session=s)
        if r2.matched_count == 0:
            raise UnknownAccount(f"unknown account {dst}")
        db.transaction_history.insert_one({"txn_id": uuid.uuid4().hex[:10], "from": src, "to": dst,
                                          "amount": amount, "timestamp": datetime.now(UTC)}, session=s)

    out = {"ok": False, "src": src, "dst": dst, "amount": amount}
    try:
        res = run_txn(client, body, max_retries=retries, base_delay=base_delay, tag="transfer")
        out.update(ok=True, outcome="committed", **{k: res[k] for k in ("attempts", "conflicts", "delays")})
    except (InsufficientFunds, UnknownAccount, SimulatedCrash) as exc:
        out.update(outcome="rolled_back", reason=str(exc), error=type(exc).__name__, **_stats_of(exc))
    except PyMongoError as exc:
        out.update(outcome="failed", reason=f"{type(exc).__name__}: {str(exc)[:160]}", error=type(exc).__name__,
                   **_stats_of(exc))
    out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def unsafe_transfer_like_sample(client: MongoClient, src: str, dst: str, amount: int) -> dict:
    """The classroom sample's logic WITHOUT the matched_count check - shows the bug it would have."""
    db = client[BANK_DB]
    with client.start_session() as s:
        with s.start_transaction():
            r = db.accounts.update_one({"account_id": src, "balance": {"$gte": amount}},
                                       {"$inc": {"balance": -amount}}, session=s)
            db.accounts.update_one({"account_id": dst}, {"$inc": {"balance": amount}}, session=s)
    return {"debit_matched": r.matched_count}


def conflict_demo(client: MongoClient, hold: float = 0.6) -> dict:
    """Activity 10.1: two transfers touch ACC-001 at the same moment.

    Transfer A opens a transaction, debits ACC-001 and keeps the transaction open for `hold` seconds.
    Transfer B starts 0.15 s later and hits a write conflict -> aborted -> retried with exponential
    back-off (0.1 s, 0.2 s, 0.4 s ...) until A has committed. Both finally succeed; no money is lost.
    """
    before = bank_state(client)
    results: dict = {}
    ta = threading.Thread(target=lambda: results.__setitem__(
        "A", transfer(client, "ACC-001", "ACC-002", 100, hold_seconds=hold)))
    tb = threading.Thread(target=lambda: (time.sleep(0.15), results.__setitem__(
        "B", transfer(client, "ACC-001", "ACC-002", 50, retries=6, base_delay=0.1))))
    ta.start(); tb.start(); ta.join(); tb.join()
    after = bank_state(client)
    return {"A": results["A"], "B": results["B"], "before": before["accounts"], "after": after["accounts"],
            "total_before": before["total"], "total_after": after["total"],
            "money_conserved": before["total"] == after["total"]}


# ============================================================================= Lab 10.2 - inventory
def seed_shop(client: MongoClient, stock: int = 5, price: float = 499.0) -> dict:
    db = client[SHOP_DB]
    db.inventory.drop()
    db.orders.drop()
    db.inventory.create_index("product_id", unique=True)
    db.orders.create_index("order_id", unique=True)
    db.get_collection("inventory", write_concern=WriteConcern("majority")).insert_one(
        {"product_id": "P-100", "name": "Mechanical keyboard", "stock": stock, "price": price, "version": 0})
    return shop_state(client)


def shop_state(client: MongoClient) -> dict:
    db = client[SHOP_DB].with_options(read_preference=ReadPreference.PRIMARY, read_concern=ReadConcern("majority"))
    inv = db.inventory.find_one({"product_id": "P-100"}, {"_id": 0})
    orders = list(db.orders.find({}, {"_id": 0}).sort("created_at", -1).limit(12))
    count = db.orders.count_documents({})
    units = sum(i["qty"] for o in db.orders.find({}, {"items": 1}) for i in o["items"])
    return {"inventory": inv, "orders": orders, "order_count": count, "units_ordered": units}


def reserve_inventory(client: MongoClient, product_id: str, qty: int, customer_id: str, *, retry: bool = True,
                      max_retries: int = 5, base_delay: float = 0.05, fail_after_decrement: bool = False) -> dict:
    """Lab 10.2 solution: read stock, check it, decrement with $inc (+ version) and create a pending order
    - all inside one transaction. retry=False shows the raw write conflicts."""
    db = client[SHOP_DB]
    t0 = time.perf_counter()

    def body(s):
        p = db.inventory.find_one({"product_id": product_id}, session=s)
        if p is None:
            raise UnknownProduct(product_id)
        if p["stock"] < qty:
            raise OutOfStock(f"only {p['stock']} left")
        r = db.inventory.update_one({"product_id": product_id, "version": p["version"], "stock": {"$gte": qty}},
                                    {"$inc": {"stock": -qty, "version": 1}}, session=s)
        if r.matched_count == 0:
            raise StaleVersion(product_id)
        if fail_after_decrement:
            raise SimulatedCrash("order insert failed after the stock was decremented")
        db.orders.insert_one({"order_id": uuid.uuid4().hex[:12], "customer_id": customer_id,
                              "items": [{"product_id": product_id, "qty": qty, "price": p["price"]}],
                              "status": "pending", "total": round(p["price"] * qty, 2),
                              "created_at": datetime.now(UTC)}, session=s)

    out = {"customer": customer_id}
    try:
        res = run_txn(client, body, max_retries=max_retries if retry else 0, base_delay=base_delay, tag="reserve")
        out.update(status="reserved", **{k: res[k] for k in ("attempts", "conflicts")})
    except OutOfStock as exc:
        out.update(status="out_of_stock", reason=str(exc), **_stats_of(exc))
    except SimulatedCrash as exc:
        out.update(status="rolled_back", reason=str(exc), **_stats_of(exc))
    except (PyMongoError, StaleVersion) as exc:
        kind = "write_conflict" if (is_write_conflict(exc) or isinstance(exc, StaleVersion)) else "error"
        out.update(status=kind, reason=f"{type(exc).__name__}: {str(exc)[:120]}", **_stats_of(exc))
    out["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def reserve_non_transactional(client: MongoClient, product_id: str, qty: int, customer_id: str, *,
                              fail_after_decrement: bool = False) -> dict:
    """The findAndModify comparison: atomic on ONE document, but the order insert is a separate step."""
    db = client[SHOP_DB]
    t0 = time.perf_counter()
    doc = db.inventory.find_one_and_update({"product_id": product_id, "stock": {"$gte": qty}},
                                           {"$inc": {"stock": -qty, "version": 1}})
    if doc is None:
        return {"customer": customer_id, "status": "out_of_stock", "ms": round((time.perf_counter() - t0) * 1000, 1)}
    try:
        if fail_after_decrement:
            raise SimulatedCrash("order insert failed after the stock was decremented")
        db.orders.insert_one({"order_id": uuid.uuid4().hex[:12], "customer_id": customer_id,
                              "items": [{"product_id": product_id, "qty": qty, "price": doc["price"]}],
                              "status": "pending", "total": round(doc["price"] * qty, 2),
                              "created_at": datetime.now(UTC)})
    except SimulatedCrash as exc:
        return {"customer": customer_id, "status": "inconsistent", "reason": str(exc),
                "ms": round((time.perf_counter() - t0) * 1000, 1)}
    return {"customer": customer_id, "status": "reserved", "ms": round((time.perf_counter() - t0) * 1000, 1)}


def concurrency_test(client: MongoClient, *, threads: int = 8, stock: int = 5, retry: bool = True) -> dict:
    """8 threads each want 1 unit of a product that has 5. Everybody starts at the same instant."""
    seed_shop(client, stock=stock)
    barrier = threading.Barrier(threads)

    def worker(i):
        barrier.wait()
        return reserve_inventory(client, "P-100", 1, f"cust-{i + 1}", retry=retry)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=threads) as pool:
        results = list(pool.map(worker, range(threads)))
    secs = time.perf_counter() - t0
    st = shop_state(client)
    count = lambda k: sum(1 for r in results if r["status"] == k)        # noqa: E731
    summary = {"reserved": count("reserved"), "out_of_stock": count("out_of_stock"),
               "write_conflict": count("write_conflict"), "other": sum(1 for r in results if r["status"] not in
                                                                         ("reserved", "out_of_stock", "write_conflict")),
               "retries": sum(r.get("conflicts", 0) for r in results)}
    return {"threads": threads, "stock_start": stock, "retry": retry, "seconds": round(secs, 3), "results": results,
            "summary": summary, "final_stock": st["inventory"]["stock"], "orders": st["order_count"],
            "oversold": st["inventory"]["stock"] < 0 or st["order_count"] > stock,
            "consistent": st["inventory"]["stock"] == stock - st["units_ordered"] and st["inventory"]["stock"] >= 0}


def atomicity_demo(client: MongoClient) -> dict:
    """Crash after the stock decrement: the transaction rolls back, findAndModify leaves a ghost decrement."""
    seed_shop(client, stock=5)
    tx = reserve_inventory(client, "P-100", 1, "crash-test", fail_after_decrement=True)
    a = shop_state(client)
    seed_shop(client, stock=5)
    nt = reserve_non_transactional(client, "P-100", 1, "crash-test", fail_after_decrement=True)
    b = shop_state(client)
    return {"transactional": {"result": tx, "stock": a["inventory"]["stock"], "orders": a["order_count"],
                              "consistent": a["inventory"]["stock"] == 5 and a["order_count"] == 0},
            "non_transactional": {"result": nt, "stock": b["inventory"]["stock"], "orders": b["order_count"],
                                  "consistent": b["inventory"]["stock"] == 5 and b["order_count"] == 0}}


def throughput(client: MongoClient, *, thread_counts=(1, 4, 8, 16), ops: int = 120, log=print) -> dict:
    """Successful reservations per second for 1/4/8/16 threads, transactional vs findAndModify.

    The stock is large enough that nobody runs out: we measure contention + transaction overhead, not
    out-of-stock errors. All threads fight over ONE hot product document, the worst case for conflicts."""
    rows = []
    for n in thread_counts:
        row = {"threads": n}
        for mode in ("transactional", "non_transactional"):
            seed_shop(client, stock=ops * 2)
            counters = {"ok": 0, "fail": 0, "retries": 0}
            lock = threading.Lock()

            def one(i, _mode=mode):
                if _mode == "transactional":
                    r = reserve_inventory(client, "P-100", 1, f"c{i}", retry=True, max_retries=12, base_delay=0.01)
                else:
                    r = reserve_non_transactional(client, "P-100", 1, f"c{i}")
                with lock:
                    if r["status"] == "reserved":
                        counters["ok"] += 1
                    else:
                        counters["fail"] += 1
                    counters["retries"] += r.get("conflicts", 0)

            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=n) as pool:
                list(pool.map(one, range(ops)))
            secs = time.perf_counter() - t0
            row[mode] = {"ok": counters["ok"], "failed": counters["fail"], "retries": counters["retries"],
                         "seconds": round(secs, 3), "per_second": round(counters["ok"] / secs, 1)}
            log(f"  {n:>2} threads  {mode:<17} {counters['ok']:>4} ok  {counters['fail']:>2} failed  "
                f"{counters['retries']:>4} retries  {secs:6.2f} s  -> {counters['ok'] / secs:7.1f} reservations/s")
        rows.append(row)
    return {"ops_per_run": ops, "rows": rows, "explanation": explain_throughput(rows)}


def explain_throughput(rows) -> list[str]:
    """Plain-language reading of the measured numbers (text is built from the data, not hard-coded)."""
    t = {r["threads"]: r["transactional"]["per_second"] for r in rows}
    f = {r["threads"]: r["non_transactional"]["per_second"] for r in rows}
    best = max(t, key=t.get)
    out = [f"Transactional best: {t[best]:.0f} reservations/s at {best} thread(s); "
           f"at {max(t)} threads it is {t[max(t)]:.0f}/s ({t[max(t)] / t[best] * 100:.0f}% of the best)."]
    if max(t) > best and t[max(t)] < t[best]:
        out.append("Throughput stops growing and then falls: every thread wants the same document, so extra "
                   "threads only create more write conflicts, aborts and retries (see the retries column).")
    elif t[max(t)] >= t[best]:
        out.append("On this machine throughput kept growing up to the highest thread count; the conflict cost "
                   "was still smaller than the gain from overlapping network round-trips.")
    ratios = [f[k] / t[k] for k in t if t[k]]
    out.append(f"findAndModify is on average {sum(ratios) / len(ratios):.1f}x faster than the transaction: it is one "
               "round trip on one document, no snapshot, no majority-commit of a multi-document transaction.")
    out.append("The price is atomicity: if the order insert fails after the stock decrement, findAndModify leaves "
               "the stock reduced with no order (see the atomicity demo). The transaction rolls both back.")
    return out

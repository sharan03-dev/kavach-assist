# Lab 10 - Multi-document ACID transactions
M NAGASAI GIRISHARAN · 24BTRCL114 · 5 AIML-B · 23CSE528 NoSQL Database using MongoDB

Lab 10 shows that MongoDB can move money safely: several changes happen **together or not at all**. Transactions need a replica set, so
they run on rs0 from Lab 8. The results are saved to `results/lab10_results.json`, `results/console_10.txt`, the PDF
`results/Exercise-10_Report.pdf` and the website's Lab 10 page.

| Part | What it does |
|---|---|
| **Lab 10.1 - bank transfer** | one transaction = debit + credit + history record. Checks a successful transfer, a rollback on insufficient funds, a rollback after a simulated crash between debit and credit, and a retry with back-off. It also shows why the classroom sample needs a `matched_count` check. |
| **Lab 10.2 - concurrent reservations** | 8 threads reserve 5 items of stock at the same time, with and without retry on write conflicts. Stock is never oversold and the orders always match the stock. |
| **Throughput** | 1, 4, 8 and 16 threads, a transaction against a plain atomic update (`findAndModify`): transactions are safer but cost throughput under contention. |
| **Lab 10 on Lab 7 (billing paywall)** | the classifier on the Kavach website gives 3 free uses, then the user tops up a wallet and subscribes (Rs 100/month). Debit, subscription and payment record commit together; concurrency tests show no lost updates and no double spending, and idempotency keys make retries safe. |

## Key ideas
- Snapshot isolation inside a transaction; a **write conflict** is retried (`TransientTransactionError`), a failed commit is rolled back.
- Idempotency keys make a retried request safe (the same payment is not applied twice).
- Keep transactions short; for a single document, an atomic update is faster than a transaction.

## Files
`run_lab10.py` (runs every task and saves the evidence), `make_report.py` (the PDF), `lab10` (command menu); the logic is in
`backend/app/lab10.py` and `backend/app/billing.py`.

## Run
```bash
docker compose up -d --build lab10             # starts Lab 7, 8, 9 and then Lab 10; Lab 10 runs in full
open http://localhost:8000/#/lab10             # the website's Transactions lab 10 page
docker compose exec lab10 lab10 10.1           # bank transfer      (about 10 s)
docker compose exec lab10 lab10 10.2           # concurrent orders  (about 10 s)
docker compose exec lab10 lab10 bench          # throughput         (about 40 s)
docker compose exec lab10 lab10 billing        # ACID checks of the paywall
docker compose exec lab10 lab10 all            # everything + results/Exercise-10_Report.pdf
```

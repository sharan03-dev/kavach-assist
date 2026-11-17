// Lab 13.2 applied to Lab 10 - runs INSIDE the mongo-client pod (lab13.k8s_failover_txn fills in the placeholders).
// Moves Rs 1 from account A to account B in a multi-document transaction, again and again, for __DURATION__ ms.
// Each transfer also inserts ledger {_id: seq}. If an error hides whether a commit happened (the PRIMARY pod was
// deleted), the client looks the ledger entry up: found = it committed (do not repeat), missing = retry the transfer.
// The ledger _id makes a repeated transfer impossible (duplicate key aborts it), so no rupee is moved twice.
const URI = "__URI__";
const DURATION_MS = __DURATION__;
const conn = new Mongo(URI);
const session = conn.startSession();
const bank = session.getDatabase("lab13_bank");
const plain = conn.getDB("lab13_bank");
const t0 = Date.now();
let seq = 0, committed = 0, retries = 0, recovered = 0, maxGap = 0, gapAt = 0, lastOk = t0;
const errors = {}, perSecond = {};

function noteError(e) {
  const k = e.codeName || e.name || "Error";
  errors[k] = (errors[k] || 0) + 1;
}

while (Date.now() - t0 < DURATION_MS) {
  seq += 1;
  let done = false;
  while (!done) {
    try {
      session.startTransaction({ readConcern: { level: "snapshot" }, writeConcern: { w: "majority" } });
      bank.accounts.updateOne({ _id: "A" }, { $inc: { balance: -1 } });
      bank.accounts.updateOne({ _id: "B" }, { $inc: { balance: 1 } });
      bank.ledger.insertOne({ _id: seq, at: new Date(), pod: "__POD__" });
      session.commitTransaction();
      done = true;
    } catch (e) {
      noteError(e);
      retries += 1;
      try { session.abortTransaction(); } catch (ignored) { /* already aborted */ }
      sleep(250);
      try {
        if (plain.ledger.findOne({ _id: seq })) { done = true; recovered += 1; }
      } catch (ignored) { /* no primary yet - retry */ }
      if (Date.now() - t0 > DURATION_MS + 120000) break;
    }
  }
  if (!done) break;
  committed += 1;
  const now = Date.now();
  if (now - lastOk > maxGap) { maxGap = now - lastOk; gapAt = lastOk - t0; }
  lastOk = now;
  const s = Math.floor((now - t0) / 1000);
  perSecond[s] = (perSecond[s] || 0) + 1;
  sleep(50);
}
const secs = Math.ceil((Date.now() - t0) / 1000);
const series = [];
for (let i = 0; i < secs; i++) series.push(perSecond[i] || 0);
print(JSON.stringify({ committed: committed, seq: seq, retries: retries, recovered: recovered, errors: errors,
  max_gap_ms: maxGap, gap_at_ms: gapAt, duration_ms: Date.now() - t0, per_second: series }));

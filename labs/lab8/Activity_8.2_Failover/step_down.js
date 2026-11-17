// Activity 8.2 - Force a failover with rs.stepDown()
// Run (the replica-set URI makes mongosh talk to whichever node is primary):
//   docker exec -i mongo1 mongosh "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0" --quiet < step_down.js

function primaryName() {
  const p = rs.status().members.find(m => m.stateStr === "PRIMARY");
  return p ? p.name : "(none - election in progress)";
}

const before = primaryName();
const termBefore = rs.status().term;
print(`Current PRIMARY: ${before}   (election term ${termBefore})`);

// stepDown(60, 10):
//   60s -> old primary is ineligible to become primary again for 60 seconds
//   10s -> wait up to 10s for a secondary to catch up before stepping down
print("Calling rs.stepDown(60, 10) ...");
try {
  rs.stepDown(60, 10);
} catch (e) {
  // Older shells drop the connection here; that is expected behaviour.
  print("Connection reset during stepDown (expected): " + e.message);
}

// Poll until a NEW primary appears
const t0 = Date.now();
let after = null;
while (Date.now() - t0 < 30000) {
  try {
    const p = primaryName();
    if (!p.startsWith("(") && p !== before) { after = p; break; }
  } catch (e) { /* topology still settling */ }
  sleep(250);
}
const st = rs.status();
print(`New PRIMARY: ${after}   (election term ${st.term}, took ~${Date.now() - t0} ms)`);

print("\n=== rs.status() after the election ===");
st.members.forEach(m =>
  print(`${m.name.padEnd(14)} ${m.stateStr.padEnd(10)} health=${m.health}`)
);

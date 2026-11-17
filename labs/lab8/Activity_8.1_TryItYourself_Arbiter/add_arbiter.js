// Activity 8.1 (Try it yourself) - add an arbiter
// Run:
//   docker exec -i mongo1 mongosh "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0" --quiet < add_arbiter.js

function voters() {
  return rs.conf().members.filter(m => m.votes > 0).map(m => m.host);
}
function show(title) {
  const v = voters();
  const majority = Math.floor(v.length / 2) + 1;
  print(`\n--- ${title} ---`);
  print(`Voting members (${v.length}): ${v.join(", ")}`);
  print(`Votes needed to elect: ${majority}   Failures tolerated: ${v.length - majority}`);
}

show("Before: P-S-S");

// MongoDB 5.0+: set the cluster-wide default write concern explicitly before
// adding an arbiter, so the implicit default cannot silently change.
db.adminCommand({ setDefaultRWConcern: 1, defaultWriteConcern: { w: "majority" } });

// Step 1: add the arbiter (one voting-member change per reconfig)
printjson(rs.addArb("mongo-arb:27020"));
show("After rs.addArb(): P-S-S-A  (4 voters = EVEN!)");
print("NOTE: 4 voters still tolerate only 1 failure - same as 3 voters.");
print("An arbiter only helps when it turns an EVEN number of voters into an ODD one.");

// Step 2: model the classic P-S-A deployment by making mongo3 a
// non-voting, non-electable secondary (it still keeps a full data copy).
const cfg = rs.conf();
const m3 = cfg.members.find(m => m.host === "mongo3:27019");
m3.votes = 0;
m3.priority = 0;          // a non-voting member must have priority 0
printjson(rs.reconfig(cfg));
show("After making mongo3 non-voting: P-S-A (3 voters = ODD)");

print("\n=== rs.status() ===");
rs.status().members.forEach(m =>
  print(`${m.name.padEnd(16)} ${m.stateStr.padEnd(10)} health=${m.health}`)
);

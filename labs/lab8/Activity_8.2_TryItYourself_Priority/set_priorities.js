// Activity 8.2 (Try it yourself) - configure different member priorities
// Run:
//   docker exec -i mongo1 mongosh "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0" --quiet < set_priorities.js
//
// Deliberately NOT in list order: mongo3 outranks mongo2, so a correct
// prediction cannot come from "the next node in the list".

const PRIORITIES = {
  "mongo1:27017": 3,   // preferred primary
  "mongo2:27018": 1,   // lowest  -> last choice
  "mongo3:27019": 2    // middle  -> expected winner when mongo1 steps down
};

const cfg = rs.conf();
cfg.members.forEach(m => { if (m.host in PRIORITIES) m.priority = PRIORITIES[m.host]; });
printjson(rs.reconfig(cfg));

print("\n=== Configured priorities ===");
rs.conf().members.forEach(m =>
  print(`${m.host.padEnd(14)} priority=${m.priority} votes=${m.votes}`)
);

// Highest-priority member performs a "priority takeover" once caught up.
print("\nWaiting for mongo1 (priority 3) to become PRIMARY...");
for (let i = 0; i < 60; i++) {
  const p = rs.status().members.find(m => m.stateStr === "PRIMARY");
  if (p && p.name === "mongo1:27017") { print("mongo1 is PRIMARY - ready for the test."); break; }
  sleep(1000);
}

print("\nPREDICTION: after rs.stepDown() on mongo1, mongo3 (priority 2) will be elected.");

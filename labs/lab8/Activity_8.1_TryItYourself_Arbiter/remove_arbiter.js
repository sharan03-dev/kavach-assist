// Cleanup after the arbiter activity - restore the original P-S-S set
// before starting Activity 8.2.
// Run:
//   docker exec -i mongo1 mongosh "mongodb://mongo1:27017,mongo2:27018,mongo3:27019/?replicaSet=rs0" --quiet < remove_arbiter.js

printjson(rs.remove("mongo-arb:27020"));      // voters 3 -> 2

const cfg = rs.conf();
const m3 = cfg.members.find(m => m.host === "mongo3:27019");
m3.votes = 1;
m3.priority = 1;
printjson(rs.reconfig(cfg));                    // voters 2 -> 3

rs.status().members.forEach(m =>
  print(`${m.name.padEnd(16)} ${m.stateStr.padEnd(10)} health=${m.health}`)
);
print("Restored P-S-S. You can now: docker compose -f docker-compose.arbiter.yml down");

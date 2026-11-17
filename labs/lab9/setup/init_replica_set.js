// Exercise 9 - initialise rs0 (same configuration as Exercise 8, Lab 8.1)
// The lab9 container does this automatically (setup/rsctl.py init).
// Manual alternative from the host:
//   docker exec -i mongo1 mongosh --port 27017 --quiet < setup/init_replica_set.js
//
// Member hosts are the Docker container names, NOT localhost (inside a
// container localhost points back at that same container).

const config = {
  _id: "rs0",
  members: [
    { _id: 0, host: "mongo1:27017", priority: 2 },  // preferred primary
    { _id: 1, host: "mongo2:27018", priority: 1 },
    { _id: 2, host: "mongo3:27019", priority: 1 }
  ]
};

try {
  printjson(rs.initiate(config));
} catch (e) {
  if (e.codeName === "AlreadyInitialized") print("Replica set already initialised - continuing.");
  else throw e;
}

print("Waiting for a PRIMARY ...");
let primary = null;
for (let i = 0; i < 60 && !primary; i++) {
  sleep(1000);
  const p = (rs.status().members || []).find(m => m.stateStr === "PRIMARY");
  if (p) primary = p.name;
}
print("PRIMARY: " + primary);

const st = rs.status();
const cfg = rs.conf();
print(`writeMajorityCount: ${st.writeMajorityCount}   ` +
      `writeConcernMajorityJournalDefault: ${cfg.writeConcernMajorityJournalDefault}`);
st.members.forEach(m =>
  print(`${m.name.padEnd(14)} ${m.stateStr.padEnd(10)} health=${m.health}`));

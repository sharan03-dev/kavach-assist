// Activity 8.1 - Initialise the 3-node replica set
// Run:  docker exec -i mongo1 mongosh --port 27017 --quiet < init_replica_set.js
//
// Member hosts use the Docker service names, NOT "localhost".
// Inside a container, localhost:27018 points back at the same container,
// so a config using localhost would fail the replSetInitiate quorum check.

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
  if (e.codeName === "AlreadyInitialized") {
    print("Replica set already initialised - continuing.");
  } else {
    throw e;
  }
}

// Wait until an election has produced a primary
print("Waiting for a PRIMARY to be elected...");
let primary = null;
for (let i = 0; i < 30 && !primary; i++) {
  sleep(1000);
  const st = rs.status();
  const p = st.members ? st.members.find(m => m.stateStr === "PRIMARY") : null;
  if (p) primary = p.name;
}
print("PRIMARY elected: " + primary);

// Insert a test document on the primary with w:3, so it is acknowledged
// only after ALL three members have written it.
db = db.getSiblingDB("labdb");
printjson(db.replication_test.insertOne(
  { msg: "Hello from Lab 8.1 (mongosh)", createdAt: new Date() },
  { writeConcern: { w: 3, wtimeout: 5000 } }
));

// Verify on each secondary by connecting to it directly
print("\n=== Document as seen on each secondary ===");
rs.status().members
  .filter(m => m.stateStr === "SECONDARY")
  .forEach(m => {
    const conn = new Mongo(m.name + "/?directConnection=true");
    conn.setReadPref("secondaryPreferred");
    const d = conn.getDB("labdb").replication_test.findOne({ msg: /mongosh/ });
    print(`${m.name.padEnd(14)} -> ${d ? "FOUND: " + d.msg : "MISSING"}`);
  });

// Summarise rs.status(): name, state, health
print("\n=== rs.status() summary ===");
rs.status().members.forEach(m => {
  print(`${m.name.padEnd(14)} ${m.stateStr.padEnd(10)} health=${m.health}`);
});

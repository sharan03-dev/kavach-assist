// Lab 12.1 in mongosh - the same steps run_lab12.py performs (connect as kavach_admin first):
//   docker compose exec lab12 lab12 shell kavach_admin      then   load("/app/labs/lab12/mongosh/12_1_rbac.js")
// (the file is also readable in the lab12 container; paste it if you use mongosh from your computer)
use("admin");

// The classroom snippet FAILS as written:  actions: ["find", "aggregate"]  ->  BadValue "Unrecognized action: aggregate".
// An aggregation needs the privilege action "find" on the collections it reads.
db.createRole({
  role: "analyticsReader",
  privileges: [{ resource: { db: "analytics", collection: "" }, actions: ["find", "listCollections"] }],
  roles: []
});
db.createUser({ user: "analytics_app", pwd: passwordPrompt(), roles: [{ role: "analyticsReader", db: "admin" }],
                mechanisms: ["SCRAM-SHA-256"] });

// two application users, readWrite on SPECIFIC collections only ("delete" is called "remove")
db.createRole({ role: "appRead", roles: [], privileges: [
  { resource: { db: "lab12_company", collection: "products" }, actions: ["find"] },
  { resource: { db: "lab12_company", collection: "orders" },   actions: ["find"] } ] });
db.createRole({ role: "appWrite", roles: [], privileges: [
  { resource: { db: "lab12_company", collection: "orders" },   actions: ["find", "insert", "update", "remove"] },
  { resource: { db: "lab12_company", collection: "products" }, actions: ["find"] } ] });
db.createUser({ user: "app_user_read",  pwd: passwordPrompt(), roles: [{ role: "appRead",  db: "admin" }] });
db.createUser({ user: "app_user_write", pwd: passwordPrompt(), roles: [{ role: "appWrite", db: "admin" }] });

// verify (log in as app_user_read):
//   use lab12_company; db.orders.find()          -> ok
//   db.orders.insertOne({x: 1})                  -> MongoServerError: not authorized on lab12_company to execute command { insert ...
db.getRoles({ showPrivileges: true });

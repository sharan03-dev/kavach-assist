// Activity 12.1 - field-level redaction with a view (salary hidden from users without the HR role)
use("lab12_company");
db.createView("employees_public", "employees", [{ $project: { salary: 0 } }]);           // exclusion, as the activity asks
db.createView("employees_directory", "employees",                                        // allow-list: safer (see report)
  [{ $project: { _id: 0, emp_id: 1, name: 1, dept: 1, title: 1, email: 1 } }]);

use("admin");
db.createRole({ role: "hrManager", roles: [], privileges: [
  { resource: { db: "lab12_company", collection: "employees" },        actions: ["find", "insert", "update"] },
  { resource: { db: "lab12_company", collection: "employees_public" }, actions: ["find"] } ] });
db.createRole({ role: "staffReader", roles: [], privileges: [            // NOTHING on employees itself
  { resource: { db: "lab12_company", collection: "employees_public" }, actions: ["find"] } ] });
db.createUser({ user: "hr_user",    pwd: passwordPrompt(), roles: [{ role: "hrManager",   db: "admin" }] });
db.createUser({ user: "staff_user", pwd: passwordPrompt(), roles: [{ role: "staffReader", db: "admin" }] });

// as staff_user:  db.getSiblingDB("lab12_company").employees_public.findOne()   -> no salary field
//                 db.getSiblingDB("lab12_company").employees.findOne()          -> not authorized
// as hr_user:     db.getSiblingDB("lab12_company").employees.findOne()          -> salary visible

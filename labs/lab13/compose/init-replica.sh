#!/usr/bin/env bash
# Lab 13.1 step 3 - initialise the replica set and verify all three members are healthy (standalone compose file).
#   rs.initiate() and the first user are created through the LOCALHOST EXCEPTION: with --keyFile, authorization is on,
#   but while no user exists a client on 127.0.0.1 INSIDE the container may initiate the set and create one admin.
set -euo pipefail
cd "$(dirname "$0")"
MEMBERS='{_id: "rs0", members: [{_id: 0, host: "prod-mongo-1:27017"}, {_id: 1, host: "prod-mongo-2:27017"}, {_id: 2, host: "prod-mongo-3:27017"}]}'
if [ "$(docker exec prod-mongo-1 mongosh --quiet --eval 'db.hello().setName || ""')" = "" ]; then
  echo "\$ rs.initiate($MEMBERS)"
  docker exec prod-mongo-1 mongosh --quiet --eval "rs.initiate($MEMBERS)"
fi
echo -n "waiting for a PRIMARY"
until P=$(docker exec prod-mongo-1 mongosh --quiet --eval 'db.hello().primary || ""') && [ -n "$P" ]; do echo -n "."; sleep 1; done
PC="${P%%:*}"; echo " -> $P"
if [ ! -s admin-password.txt ]; then
  openssl rand -base64 18 | tr -d '/+=' > admin-password.txt; chmod 600 admin-password.txt
  docker exec "$PC" mongosh --quiet --eval "db.getSiblingDB('admin').createUser({user: 'admin', pwd: '$(cat admin-password.txt)', roles: ['root']})"
  echo "admin created on $PC (password in admin-password.txt)"
fi
docker exec "$PC" mongosh --quiet -u admin -p "$(cat admin-password.txt)" --authenticationDatabase admin \
  --eval 'rs.status().members.forEach(m => print(m.name.padEnd(20), m.stateStr.padEnd(10), "health", m.health))'
echo "Without credentials:"; docker exec "$PC" mongosh --quiet --eval 'try { db.getSiblingDB("x").c.findOne() } catch (e) { print(e.codeName) }'

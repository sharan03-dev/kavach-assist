#!/usr/bin/env bash
# Lab 13.1 step 2 - the classroom commands, for a keyfile on YOUR computer (e.g. for the Kubernetes Secret):
#     openssl rand -base64 756 > keyfile
#     chmod 400 keyfile
# The compose files do NOT bind-mount this file: mongod runs as uid 999 inside the container and cannot read a
# 0400 file owned by your user. They run the same two commands inside a volume and add `chown 999:999`.
#
#   ./generate_keyfile.sh            -> ./keyfile
#   ./generate_keyfile.sh --k8s      -> also: kubectl -n lab13 create secret generic mongo-keyfile --from-file=keyfile
set -euo pipefail
cd "$(dirname "$0")"
if [ -s keyfile ]; then echo "keyfile exists - not replaced (members using it would lose each other)"; else
  openssl rand -base64 756 > keyfile
  chmod 400 keyfile
fi
ls -l keyfile
echo "base64 characters: $(tr -d '\n' < keyfile | wc -c)  (MongoDB accepts 6-1024; 756 random bytes -> 1008)"
if [ "${1:-}" = "--k8s" ]; then
  kubectl apply -f ../k8s/namespace.yaml
  kubectl -n lab13 create secret generic mongo-keyfile --from-file=keyfile=keyfile --dry-run=client -o yaml | kubectl apply -f -
fi

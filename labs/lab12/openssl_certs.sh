#!/usr/bin/env bash
# Lab 12.2 by hand - the same certificate chain as setup/certs.py, with the OpenSSL commands of the solution.
#   bash labs/lab12/openssl_certs.sh [out-dir]          (needs bash for the <(...) process substitution)
set -euo pipefail
OUT="${1:-./certs-openssl}"; mkdir -p "$OUT"; cd "$OUT"
# 1. CA key + self-signed CA certificate
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 365 -key ca.key -out ca.crt -subj '/CN=MongoDB-CA'
# 2. server key + certificate signing request
openssl genrsa -out server.key 4096
openssl req -new -key server.key -out server.csr -subj '/CN=mongodb'
# 3. the CA signs the server certificate; the SAN is what PyMongo checks against the host name
openssl x509 -req -days 365 -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt \
  -extfile <(printf 'subjectAltName=DNS:localhost,DNS:mongo-secure,IP:127.0.0.1\nextendedKeyUsage=serverAuth')
cat server.key server.crt > server.pem              # mongod --tlsCertificateKeyFile server.pem
# client certificate for PyMongo / mongosh (needed because mongod has --tlsCAFile: client certs are required)
openssl genrsa -out client.key 4096
openssl req -new -key client.key -out client.csr -subj '/CN=kavach-app/OU=Applications'
openssl x509 -req -days 365 -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out client.crt \
  -extfile <(printf 'extendedKeyUsage=clientAuth')
cat client.key client.crt > client.pem
# document the chain
openssl verify -CAfile ca.crt server.crt client.crt
openssl x509 -in server.crt -noout -subject -issuer -dates -ext subjectAltName
echo "mongod --tlsMode requireTLS --tlsCertificateKeyFile $PWD/server.pem --tlsCAFile $PWD/ca.crt"

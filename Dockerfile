# One image for the whole project: website (Lab 7), replica-set tools (Lab 8), write-concern + partition
# scripts (Lab 9), transaction labs (Lab 10), sharding labs + cluster setup + report (Lab 11), security labs +
# certificate generator + report (Lab 12), Lab 13 (keyfile generator, exporter sidecar binary, kubectl, terraform)
# Lab 16-18 (Cassandra + Neo4j drivers, embeddings model, vector search, capstone), Lab 14 (monitoring dashboard, webhook alerts, mongodump/mongorestore backups, point-in-time recovery) and the Docker CLI
# (Lab 8/9 failover + partition demos).
# MongoDB itself comes from the official mongo:7.0 image (see docker-compose.yml).
FROM docker:28-cli AS dockercli
# Lab 14: mongodump + mongorestore (MongoDB Database Tools) come from the official image
FROM mongo:7.0 AS mongotools

FROM python:3.12-slim
ARG IMAGE_NAME=your-dockerhub-username/nosql-lab18:latest
LABEL org.opencontainers.image.title="23CSE528 Labs 7-18 - Kavach CTI + replica set + write concern + transactions + sharding + security + containers/cloud + operations + SaaS + Cassandra/Neo4j + vector search/RAG + capstone" \
      org.opencontainers.image.authors="M Nagasai Girisharan (24BTRCL114)"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 RESULTS_DIR=/app/results
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=mongotools /usr/bin/mongodump /usr/bin/mongorestore /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core curl openssl unzip libgssapi-krb5-2 \
 && rm -rf /var/lib/apt/lists/* && mongodump --version && mongorestore --version
# Lab 13: Prometheus exporter for the sidecar (required), kubectl and terraform (optional: lab13 falls back to
# `docker exec k3s kubectl`, and skips `terraform validate`, if a download fails). Versions pinned.
ARG EXPORTER_VERSION=0.47.2
ARG KUBECTL_VERSION=v1.30.6
ARG TERRAFORM_VERSION=1.9.8
RUN set -eu; ARCH="$(dpkg --print-architecture)"; \
    curl -fsSL "https://github.com/percona/mongodb_exporter/releases/download/v${EXPORTER_VERSION}/mongodb_exporter-${EXPORTER_VERSION}.linux-${ARCH}.tar.gz" \
      | tar -xz -C /tmp && mv /tmp/mongodb_exporter-*/mongodb_exporter /usr/local/bin/ && rm -rf /tmp/mongodb_exporter-*; \
    mongodb_exporter --version | head -2; \
    if curl -fsSLo /usr/local/bin/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl"; then \
      chmod +x /usr/local/bin/kubectl; else rm -f /usr/local/bin/kubectl; echo "kubectl not downloaded - using docker exec k3s kubectl"; fi; \
    if curl -fsSLo /tmp/tf.zip "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${ARCH}.zip"; then \
      unzip -o -q /tmp/tf.zip terraform -d /usr/local/bin; else echo "terraform not downloaded - terraform validate is skipped"; fi; \
    rm -f /tmp/tf.zip
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Lab 17: bake the embedding model (sentence-transformers/all-MiniLM-L6-v2, ONNX, 384 dimensions) into the image so the
# vector-search labs work offline. If the download fails, Lab 17 falls back to its built-in hashing embedder.
ENV FASTEMBED_CACHE=/opt/models
RUN python -c "from fastembed import TextEmbedding; m = TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/opt/models'); print('embedding dims', len(next(iter(m.embed(['ok'])))))" \
 || echo "embedding model not downloaded - Lab 17 will use the hashing embedder"
ENV HF_HUB_OFFLINE=1
COPY . .
RUN sed -i "s#__LAB_IMAGE__#${IMAGE_NAME}#g" docker-compose.hub.yml \
 && chmod +x docker-entrypoint.sh labs/lab9/lab9 labs/lab10/lab10 labs/lab11/lab11 labs/lab12/lab12 labs/lab13/lab13 labs/lab14/lab14 labs/lab15/lab15 labs/lab16/lab16 labs/lab17/lab17 labs/lab18/lab18 labs/lab13/compose/*.sh labs/lab12/openssl_certs.sh labs/lab9/Activity_9.2_Network_Partition/simulate_partition.sh \
 && ln -sf /app/labs/lab9 /lab9 && ln -sf /app/labs/lab9/lab9 /usr/local/bin/lab9 \
 && ln -sf /app/labs/lab10/lab10 /usr/local/bin/lab10 \
 && ln -sf /app/labs/lab11/lab11 /usr/local/bin/lab11 \
 && ln -sf /app/labs/lab12/lab12 /usr/local/bin/lab12 \
 && ln -sf /app/labs/lab13/lab13 /usr/local/bin/lab13 \
 && ln -sf /app/labs/lab14/lab14 /usr/local/bin/lab14 \
 && ln -sf /app/labs/lab15/lab15 /usr/local/bin/lab15 \
 && ln -sf /app/labs/lab16/lab16 /usr/local/bin/lab16 \
 && ln -sf /app/labs/lab17/lab17 /usr/local/bin/lab17 \
 && ln -sf /app/labs/lab18/lab18 /usr/local/bin/lab18 \
 && printf '#!/bin/sh\nexec python /app/orchestrator/labctl.py "$@"\n' > /usr/local/bin/labctl && chmod +x /usr/local/bin/labctl \
 && mkdir -p /app/results
EXPOSE 8000 8443
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["lab7"]

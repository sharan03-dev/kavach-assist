"""Lab 17 - AI-native MongoDB: vector search and RAG foundations.

    Lab 17.1   RAG pipeline: 5 threat-intelligence documents (labs/lab17/docs) -> 100-word chunks (sliding window) ->
               embeddings -> MongoDB rag_demo.documents {text, source, embedding} + a vectorSearch index -> retrieve the
               top 3 chunks with $vectorSearch (numCandidates 20, limit 3) -> prompt -> LLM (or a local model).
    Activity   conversation memory: rag_demo.conversations {user_id, timestamp, role, content}; the last 5 exchanges go
               into the prompt, and a follow-up question is rewritten with the previous one before retrieval.
    Lab 17.2   HNSW vs IVF on 5,000 x 384-dimension embeddings: Atlas Vector Search (HNSW, mongot) with different
               hnswOptions and numCandidates, an IVF index built inside MongoDB (k-means lists + an indexed list id) with
               different nlist / nprobe, exact brute force as ground truth: build time, latency, recall@20, the
               latency-recall curve and a recommendation.
    Applied    (mentor's rule) "Ask Kavach": the Lab 7 threat reports are embedded and indexed, the website answers
               questions about the platform's own data and cites the reports.
Vector search runs on the MongoDB Atlas Local container (mongodb/mongodb-atlas-local: mongod + mongot), the same search
engine as Atlas - no cloud account needed. If it is not running, every function falls back to exact search in NumPy and
says so ("mode": "numpy-exact").
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from pymongo import UpdateMany
from pymongo.errors import OperationFailure, PyMongoError
from pymongo.operations import SearchIndexModel

from . import ai
from . import polyglot as PG

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "labs" / "lab17"
DOCS = LAB / "docs"
RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
HEAVY = threading.Lock()
RAG_DB, VEC_DB, KAV_DB = "rag_demo", "lab17_vectors", "kavach_rag"
TOPK = 20


def _jd(x):
    return json.loads(json.dumps(x, default=str))


# ============================================================================ vector search plumbing
def vclient():
    return PG.vector_client()


def search_ok() -> dict:
    try:
        c = vclient()
        v = c.server_info()["version"]
        probe = c["lab17_probe"]["probe"]
        probe.update_one({"_id": 1}, {"$set": {"x": 1}}, upsert=True)
        list(probe.list_search_indexes())             # fails on a server without mongot (the search engine)
        return {"ok": True, "version": v}
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _index_state(coll, name):
    for d in coll.list_search_indexes(name):
        return d
    return None


def drop_index(coll, name, timeout=120):
    try:
        if _index_state(coll, name):
            coll.drop_search_index(name)
    except OperationFailure:
        pass
    t0 = time.time()
    while _index_state(coll, name) and time.time() - t0 < timeout:
        time.sleep(1)


def create_vector_index(coll, name: str, dims: int, *, hnsw: dict | None = None, filters=(), timeout: int = 600) -> dict:
    """Create a vectorSearch index and wait until it is queryable. Returns {build_s, definition, status}."""
    drop_index(coll, name)
    field = {"type": "vector", "path": "embedding", "numDimensions": dims, "similarity": "cosine"}
    if hnsw:
        field["hnswOptions"] = hnsw
    definition = {"fields": [field] + [{"type": "filter", "path": f} for f in filters]}
    t0 = time.time()
    coll.create_search_index(SearchIndexModel(definition=definition, name=name, type="vectorSearch"))
    while time.time() - t0 < timeout:
        st = _index_state(coll, name) or {}
        if st.get("queryable") and st.get("status", "READY") == "READY":
            return {"build_s": round(time.time() - t0, 2), "definition": definition, "status": st.get("status")}
        if st.get("status") == "FAILED":
            raise RuntimeError(f"index {name} FAILED: {st.get('message') or st}")
        time.sleep(0.5)
    raise RuntimeError(f"index {name} not queryable after {timeout} s")


def try_index(coll, name, dims, **kw) -> dict:
    """create_vector_index, or a clear note when the server has no search engine (numpy fallback is used then)."""
    if not search_ok()["ok"]:
        return {"build_s": None, "status": "NO_SEARCH", "note": "server without mongot - exact NumPy search is used instead"}
    return create_vector_index(coll, name, dims, **kw)


def vector_search(coll, qvec, *, index="vector_index", limit=3, num_candidates=20, exact=False, filt=None, project=None):
    vs = {"index": index, "path": "embedding", "queryVector": [float(x) for x in qvec], "limit": limit}
    if exact:
        vs["exact"] = True
    else:
        vs["numCandidates"] = num_candidates
    if filt:
        vs["filter"] = filt
    proj = {"_id": 1, "score": {"$meta": "vectorSearchScore"}, **(project or {})}
    return list(coll.aggregate([{"$vectorSearch": vs}, {"$project": proj}]))


# ============================================================================ 17.1 RAG pipeline
def chunk_words(text: str, size: int = 100, overlap: int = 20) -> list[str]:
    """Sliding window over words: size 100, step 80 (20 words overlap so a sentence cut at a border is in both chunks)."""
    words = text.split()
    if len(words) <= size:
        return [" ".join(words)]
    out, step = [], size - overlap
    for i in range(0, len(words), step):
        out.append(" ".join(words[i:i + size]))
        if i + size >= len(words):
            break
    return out


def load_docs() -> list[dict]:
    out = []
    for p in sorted(DOCS.glob("*.md")):
        lines = p.read_text().strip().splitlines()
        title = lines[0].lstrip("# ").strip()
        body = " ".join(l.strip() for l in lines[1:] if l.strip())
        out.append({"source": p.name, "title": title, "text": body, "words": len(body.split())})
    return out


def build_rag(log=print) -> dict:
    emb = ai.embedder()
    docs = load_docs()
    chunks = []
    for d in docs:
        for i, c in enumerate(chunk_words(d["text"])):
            chunks.append({"text": c, "source": d["source"], "title": d["title"], "chunk": i, "words": len(c.split())})
    t0 = time.perf_counter()
    vecs = emb.embed([c["text"] for c in chunks])
    t_emb = time.perf_counter() - t0
    for c, v in zip(chunks, vecs):
        c["embedding"] = [float(x) for x in v]
    coll = vclient()[RAG_DB]["documents"]
    coll.delete_many({})
    coll.insert_many([dict(c) for c in chunks])
    idx = try_index(coll, "vector_index", emb.dims, filters=("source",))
    log(f"   {len(docs)} documents -> {len(chunks)} chunks of <= 100 words (20-word overlap); embeddings {emb.name} "
        f"{emb.dims}-d in {t_emb:.2f} s; vector_index queryable after {idx['build_s']} s")
    return {"documents": [{k: d[k] for k in ("source", "title", "words")} for d in docs], "chunks": len(chunks),
            "chunk_sizes": [c["words"] for c in chunks], "embedder": ai.embedder_info(), "embed_s": round(t_emb, 2), "index": idx,
            "sample_chunk": {k: chunks[0][k] for k in ("source", "chunk", "text")} | {"embedding_head": chunks[0]["embedding"][:6]}}


def retrieve(query: str, k: int = 3, num_candidates: int = 20, source: str | None = None) -> dict:
    emb = ai.embedder()
    t0 = time.perf_counter()
    q = emb.embed([query], kind="query")[0]
    t_emb = (time.perf_counter() - t0) * 1000
    coll = vclient()[RAG_DB]["documents"]
    t1 = time.perf_counter()
    try:
        hits = vector_search(coll, q, limit=k, num_candidates=num_candidates, filt={"source": source} if source else None,
                             project={"text": 1, "source": 1, "title": 1, "chunk": 1, "_id": 0})
        mode = "$vectorSearch"
    except (OperationFailure, PyMongoError) as exc:
        docs = list(coll.find({"source": source} if source else {}, {"text": 1, "source": 1, "title": 1, "chunk": 1, "embedding": 1, "_id": 0}))
        if not docs:
            raise RuntimeError(f"no documents stored and no vector search ({str(exc)[:120]})")
        m = np.array([d["embedding"] for d in docs], dtype=np.float32)
        s = m @ q
        hits = [{**{k_: docs[i][k_] for k_ in ("text", "source", "title", "chunk")}, "score": float((s[i] + 1) / 2)} for i in np.argsort(-s)[:k]]
        mode = "numpy-exact (vector search unavailable)"
    return {"query": query, "hits": hits, "mode": mode, "embed_ms": round(t_emb, 2), "search_ms": round((time.perf_counter() - t1) * 1000, 2)}


FOLLOW = re.compile(r"\b(it|its|they|them|their|this|that|these|those|he|she|such|there)\b|^(and|also|what about|how about)\b", re.I)


def condense(question: str, history: list[dict]) -> str:
    """Follow-up questions ('how do I stop them?') are rewritten with the previous user question before retrieval."""
    prev = [h["content"] for h in history if h["role"] == "user"]
    if prev and (FOLLOW.search(question.strip()) or len(question.split()) <= 3):
        return f"{prev[-1]} {question}"
    return question


def history(user_id: str, exchanges: int = 5) -> list[dict]:
    rows = list(vclient()[RAG_DB]["conversations"].find({"user_id": user_id}, {"_id": 0, "role": 1, "content": 1, "timestamp": 1})
                .sort("timestamp", -1).limit(2 * exchanges))
    return list(reversed(rows))


def ask(user_id: str, question: str, k: int = 3) -> dict:
    conv = vclient()[RAG_DB]["conversations"]
    conv.create_index([("user_id", 1), ("timestamp", -1)])
    hist = history(user_id)
    rq = condense(question, hist)
    r = retrieve(rq, k=k)
    g = ai.generate(question, r["hits"], hist)
    now = datetime.now(UTC)
    conv.insert_one({"user_id": user_id, "timestamp": now, "role": "user", "content": question})
    conv.insert_one({"user_id": user_id, "timestamp": datetime.now(UTC), "role": "assistant", "content": g["answer"],
                     "sources": [f"{h['source']}#{h['chunk']}" for h in r["hits"]], "provider": g["provider"]})
    return {"user_id": user_id, "question": question, "retrieval_query": rq, "history_used": len(hist), "retrieval": r,
            "answer": g["answer"], "provider": g["provider"], "model": g["model"], "prompt": g["prompt"], "generate_ms": g["ms"]}


EVAL = [("What are the three levels of threat intelligence?", "01_what_is_threat_intelligence.md"),
        ("Why are hash values and IP addresses weak indicators according to the Pyramid of Pain?", "02_indicators_of_compromise.md"),
        ("Why do public DNS resolvers like 8.8.8.8 show up as false positives?", "03_false_positives_in_feeds.md"),
        ("What is double extortion ransomware?", "04_threat_actors_and_ttps.md"),
        ("How does a SOAR playbook use threat intelligence?", "05_operationalising_cti.md"),
        ("Which standards are used to share indicators between organisations?", "02_indicators_of_compromise.md"),
        ("How can corroboration and source reputation reduce false alarms?", "03_false_positives_in_feeds.md"),
        ("What does MITRE ATT&CK organise techniques under?", "04_threat_actors_and_ttps.md")]


def run_171(log=print) -> dict:
    b = build_rag(log)
    ev = []
    for q, src in EVAL:
        r = retrieve(q)
        srcs = [h["source"] for h in r["hits"]]
        ev.append({"question": q, "expected": src, "top3": srcs, "scores": [round(h["score"], 4) for h in r["hits"]],
                   "hit_at_1": srcs[:1] == [src], "hit_at_3": src in srcs, "search_ms": r["search_ms"]})
    h1, h3 = sum(e["hit_at_1"] for e in ev), sum(e["hit_at_3"] for e in ev)
    log(f"   retrieval evaluation: hit@1 {h1}/{len(ev)}, hit@3 {h3}/{len(ev)} (mode {r['mode']})")
    demo = ask("demo-analyst", "What causes false positives in threat intelligence feeds?")
    log(f"   ask(): {demo['answer'][:160]}...  [{demo['provider']}]")
    return {"build": b, "evaluation": ev, "hit_at_1": h1, "hit_at_3": h3, "questions": len(ev), "mode": r["mode"],
            "generator": ai.generator_info(), "demo": demo, "pass": h3 >= len(ev) - 1}


def run_memory(log=print) -> dict:
    """Activity: multi-turn conversation; the follow-up only works because the history is stored in MongoDB."""
    user = f"analyst-{int(time.time())}"
    turns = ["Why do threat feeds produce false positives?", "How can we reduce them?",
             "Which sharing standard describes indicators?", "And which one transports them?",
             "What is the Pyramid of Pain?", "Which level of that pyramid hurts attackers the most?"]
    out = []
    for q in turns:
        a = ask(user, q)
        out.append({"question": q, "retrieval_query": a["retrieval_query"], "history_used": a["history_used"],
                    "top_source": a["retrieval"]["hits"][0]["source"] if a["retrieval"]["hits"] else None, "answer": a["answer"]})
        log(f"   [{a['history_used']:>2} msgs of history] {q}  ->  {out[-1]['top_source']}")
    stored = vclient()[RAG_DB]["conversations"].count_documents({"user_id": user})
    window = history(user)
    follow_ok = (out[1]["top_source"] == "03_false_positives_in_feeds.md" and out[3]["top_source"] == "02_indicators_of_compromise.md"
                 and out[1]["retrieval_query"] != turns[1] and out[3]["retrieval_query"] != turns[3])
    return {"user_id": user, "turns": out, "stored_messages": stored, "window": len(window), "window_limit": 10,
            "schema": {"collection": "rag_demo.conversations", "fields": ["user_id", "timestamp", "role (user|assistant)", "content", "sources"],
                       "index": "(user_id 1, timestamp -1)"},
            "pass": stored == 2 * len(turns) and len(window) == 10 and follow_ok, "follow_up_resolved": follow_ok}


# ============================================================================ 17.2 corpus
TEMPL = ["{actor} used {tech} to gain {goal} in {sector} networks", "Analysts observed {malware} beaconing to {infra} over {proto}",
         "The {sector} sector saw a rise in {tech} attempts linked to {actor}", "{malware} spreads through {vector} and steals {data}",
         "Defenders blocked {infra} after {malware} was seen in {sector} environments", "A patch for {cve} closes a {bug} in {product}",
         "{actor} abused {product} to move laterally and deploy {malware}", "Phishing emails impersonating {brand} delivered {malware}",
         "The {product} flaw {cve} is exploited in the wild for {goal}", "Threat hunters searched {logs} for signs of {tech}"]
VOCAB = {"actor": ["a ransomware affiliate", "an espionage group", "a financially motivated crew", "a hacktivist collective", "an initial access broker"],
         "tech": ["credential stuffing", "password spraying", "DLL side-loading", "living-off-the-land binaries", "token theft", "SQL injection",
                  "remote desktop brute force", "web shell deployment"],
         "goal": ["initial access", "persistence", "domain admin rights", "data exfiltration", "remote code execution"],
         "sector": ["healthcare", "banking", "telecom", "energy", "education", "government", "manufacturing", "retail"],
         "malware": ["a loader", "an infostealer", "a remote access trojan", "a botnet agent", "a wiper", "a cryptominer"],
         "infra": ["a bulletproof host", "a fast-flux domain", "a compromised router", "a cloud storage bucket", "a Tor exit node"],
         "proto": ["HTTPS", "DNS tunnelling", "ICMP", "a custom TCP protocol"], "vector": ["malicious macros", "fake updates", "cracked software", "USB drives"],
         "data": ["browser passwords", "session cookies", "crypto wallets", "VPN credentials"], "cve": [f"CVE-2026-{n}" for n in range(1001, 1040)],
         "bug": ["buffer overflow", "authentication bypass", "path traversal", "deserialisation bug", "privilege escalation"],
         "product": ["a VPN gateway", "a file transfer appliance", "an email server", "a firewall", "a content management system", "a hypervisor"],
         "brand": ["a courier company", "a tax office", "a bank", "a cloud storage provider"], "logs": ["DNS logs", "proxy logs", "EDR telemetry", "sign-in logs"]}


def corpus(n: int = 5000, seed: int = 172) -> dict:
    """Sentences from the Kavach platform (Lab 7 reports + vulnerabilities), topped up with generated CTI sentences."""
    seen, real = set(), []
    try:
        db = PG.mongo()["cti_platform"]
        for r in db.reports.find({}, {"title": 1, "summary": 1}).limit(20000):
            for t in [r.get("title", "")] + re.split(r"(?<=[.!?])\s+", r.get("summary") or ""):
                t = " ".join(t.split())
                if 6 <= len(t.split()) <= 60 and t.lower() not in seen:
                    seen.add(t.lower()); real.append(t)
        for v in db.vulnerabilities.find({}, {"title": 1, "description": 1}).limit(20000):
            for t in re.split(r"(?<=[.!?])\s+", (v.get("description") or v.get("title") or "")):
                t = " ".join(t.split())
                if 6 <= len(t.split()) <= 60 and t.lower() not in seen:
                    seen.add(t.lower()); real.append(t)
            if len(real) >= n:
                break
    except PyMongoError:
        pass
    rnd = random.Random(seed)
    rnd.shuffle(real)
    real = real[:n]
    gen, tries = [], 0
    when = ["", " this week", " overnight", " since Monday", " in October", " last month", " during the holidays", " in a new wave",
            " according to several vendors", " with high confidence", " in Asia", " in Europe", " in India"]
    while len(real) + len(gen) < n and tries < 50 * n:
        tries += 1
        t = rnd.choice(TEMPL)
        s = re.sub(r"\{(\w+)\}", lambda m: rnd.choice(VOCAB[m.group(1)]), t) + rnd.choice(when) + "."
        s = s[0].upper() + s[1:]
        if s.lower() not in seen:
            seen.add(s.lower()); gen.append(s)
    return {"sentences": real + gen, "real": len(real), "generated": len(gen)}


# ============================================================================ 17.2 IVF inside MongoDB
def kmeans(x: np.ndarray, k: int, iters: int = 20, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Spherical k-means (cosine): centroids are re-normalised every round."""
    rnd = np.random.default_rng(seed)
    c = x[rnd.choice(len(x), k, replace=False)].copy()
    for _ in range(iters):
        a = np.argmax(x @ c.T, axis=1)
        for j in range(k):
            m = x[a == j]
            c[j] = m.mean(axis=0) if len(m) else x[rnd.integers(len(x))]
        c /= np.maximum(np.linalg.norm(c, axis=1, keepdims=True), 1e-9)
    return c, np.argmax(x @ c.T, axis=1)


def build_ivf(coll, x: np.ndarray, ids: list, nlist: int) -> dict:
    t0 = time.perf_counter()
    cents, assign = kmeans(x, nlist)
    field = f"ivf_{nlist}"
    ops = [UpdateMany({"_id": {"$in": [ids[i] for i in np.where(assign == j)[0].tolist()]}}, {"$set": {field: j}}) for j in range(nlist)]
    coll.bulk_write(ops, ordered=False)
    coll.create_index(field)
    cc = coll.database["ivf_centroids"]
    cc.delete_many({"nlist": nlist})
    cc.insert_many([{"_id": f"{nlist}:{j}", "nlist": nlist, "list": j, "centroid": [float(v) for v in cents[j]],
                     "size": int((assign == j).sum())} for j in range(nlist)])
    sizes = np.bincount(assign, minlength=nlist)
    return {"build_s": round(time.perf_counter() - t0, 2), "field": field, "centroids": cents,
            "list_sizes": {"min": int(sizes.min()), "max": int(sizes.max()), "mean": round(float(sizes.mean()), 1)}}


def ivf_search(coll, cents: np.ndarray, field: str, q: np.ndarray, nprobe: int, k: int = TOPK) -> list:
    probes = np.argsort(-(cents @ q))[:nprobe].tolist()
    docs = list(coll.find({field: {"$in": probes}}, {"embedding": 1}))
    if not docs:
        return []
    m = np.array([d["embedding"] for d in docs], dtype=np.float32)
    s = m @ q
    top = np.argsort(-s)[:k]
    return [docs[i]["_id"] for i in top]


# ============================================================================ 17.2 the comparison
HNSW_CONFIGS = [{"maxEdges": 16, "numEdgeCandidates": 100}, {"maxEdges": 16, "numEdgeCandidates": 400}, {"maxEdges": 32, "numEdgeCandidates": 400}]
NUM_CANDIDATES = [20, 40, 100, 200, 400]
IVF_NLISTS = [50, 100]
NPROBES = [1, 2, 5, 10, 20]


def _measure(fn, qids, gt):
    """gt[qi] = (x, similarity of the true 20th neighbour). A result counts as correct when it is at least as similar as the
    20th exact neighbour (ties - identical sentences - would otherwise make even an exact search look < 100 %)."""
    lat, rec = [], []
    for qi in qids:
        t0 = time.perf_counter()
        got = fn(qi)
        lat.append((time.perf_counter() - t0) * 1000)
        x, thr = gt[qi]
        got = [int(g) for g in got][:TOPK]
        rec.append(float(np.sum(x[got] @ x[qi] >= thr - 1e-5)) / TOPK if got else 0.0)
    lat.sort()
    return {"avg_ms": round(float(np.mean(lat)), 3), "p50_ms": round(lat[len(lat) // 2], 3), "p95_ms": round(lat[int(0.95 * (len(lat) - 1))], 3),
            "recall": round(100 * float(np.mean(rec)), 2)}


def run_172(quick: bool = False, log=print) -> dict:
    emb = ai.embedder()
    n = 1500 if quick else 5000
    nq = 40 if quick else 100
    cp = corpus(n)
    t0 = time.perf_counter()
    x = emb.embed(cp["sentences"])
    t_emb = time.perf_counter() - t0
    log(f"   corpus: {n} sentences ({cp['real']} from the Kavach reports/vulnerabilities, {cp['generated']} generated); "
        f"{emb.name} {emb.dims}-d embeddings in {t_emb:.1f} s")
    coll = vclient()[VEC_DB]["sentences"]
    coll.drop()
    ids = list(range(n))
    t0 = time.perf_counter()
    for i in range(0, n, 500):                         # the solution: insert in batches of 500 with insert_many
        coll.insert_many([{"_id": j, "text": cp["sentences"][j], "embedding": [float(v) for v in x[j]]} for j in range(i, min(n, i + 500))])
    t_ins = time.perf_counter() - t0
    rnd = random.Random(17)
    qids = rnd.sample(ids, nq)
    t0 = time.perf_counter()
    sims = x[qids] @ x.T                              # exact brute force: scan all documents, cosine similarity
    gt = {qi: (x, float(np.sort(sims[k])[-TOPK])) for k, qi in enumerate(qids)}
    t_bf = (time.perf_counter() - t0) * 1000 / nq
    rows, curve, notes = [], {"HNSW": {}, "IVF": {}}, []
    # ---------------------------------------------------------------- brute force
    bf = _measure(lambda qi: np.argsort(-(x @ x[qi]))[:TOPK].tolist(), qids, gt)
    rows.append({"index": "Brute force (NumPy, in memory)", "params": f"scan {n} vectors", "build_s": 0.0, **bf})
    bfm = _measure(lambda qi: [d["_id"] for d in _bf_mongo(coll, x[qi])], qids[:20], gt)
    rows.append({"index": "Brute force (read every document from MongoDB)", "params": f"find() {n} docs + NumPy", "build_s": 0.0, **bfm})
    # ---------------------------------------------------------------- HNSW (Atlas Vector Search / mongot)
    hnsw_ok = search_ok()["ok"]
    keep = None
    if hnsw_ok:
        cfgs = HNSW_CONFIGS[:1] if quick else HNSW_CONFIGS
        for j, cfg in enumerate(cfgs):
            name = f"hnsw_m{cfg['maxEdges']}_c{cfg['numEdgeCandidates']}"
            try:
                b = create_vector_index(coll, name, emb.dims, hnsw=cfg)
                params = f"maxEdges (M) {cfg['maxEdges']}, numEdgeCandidates (efConstruction) {cfg['numEdgeCandidates']}"
            except (OperationFailure, RuntimeError) as exc:
                notes.append(f"hnswOptions {cfg} refused by this server: {str(exc)[:160]} - default HNSW used")
                if j:
                    continue
                name = "hnsw_default"
                b = create_vector_index(coll, name, emb.dims)
                params = "server defaults (maxEdges 16, numEdgeCandidates 100)"
            keep, keep_build = name, b["build_s"]
            log(f"   HNSW {name}: built in {b['build_s']} s")
            for nc in (NUM_CANDIDATES[:3] if quick else NUM_CANDIDATES):
                m = _measure(lambda qi, nc=nc, name=name: [d["_id"] for d in vector_search(coll, x[qi], index=name, limit=TOPK, num_candidates=nc)], qids, gt)
                rows.append({"index": "HNSW (Atlas Vector Search)", "params": f"{params}; numCandidates {nc}", "build_s": b["build_s"], **m})
                curve["HNSW"].setdefault(name, []).append({"param": f"numCandidates {nc}", "latency_ms": m["avg_ms"], "recall": m["recall"]})
                log(f"      numCandidates {nc:>4}: {m['avg_ms']:>7.2f} ms  recall@20 {m['recall']:>6.2f} %")
        ex = _measure(lambda qi: [d["_id"] for d in vector_search(coll, x[qi], index=keep, limit=TOPK, exact=True)], qids, gt)
        rows.append({"index": "ENN exact ($vectorSearch exact: true)", "params": "full scan inside mongot", "build_s": keep_build, **ex})
        log(f"   exact $vectorSearch: {ex['avg_ms']} ms, recall {ex['recall']} %")
    else:
        notes.append("Atlas Local (mongot) not reachable - HNSW rows skipped. Start it: docker compose up -d atlas-local")
    # ---------------------------------------------------------------- IVF (inside MongoDB)
    for nl in IVF_NLISTS[:1] if quick else IVF_NLISTS:
        b = build_ivf(coll, x, ids, nl)
        log(f"   IVF nlist {nl}: k-means + list ids + index in {b['build_s']} s (list sizes {b['list_sizes']})")
        for npb in NPROBES:
            m = _measure(lambda qi, npb=npb, b=b: ivf_search(coll, b["centroids"], b["field"], x[qi], npb), qids, gt)
            rows.append({"index": "IVF (k-means lists in MongoDB)", "params": f"nlist {nl}; nprobe {npb}", "build_s": b["build_s"], **m})
            curve["IVF"].setdefault(f"ivf_nlist{nl}", []).append({"param": f"nprobe {npb}", "latency_ms": m["avg_ms"], "recall": m["recall"]})
            log(f"      nprobe {npb:>3}: {m['avg_ms']:>7.2f} ms  recall@20 {m['recall']:>6.2f} %")
    rec = recommend(rows)
    sample = sample_checks(coll, emb) if hnsw_ok and not quick else {}
    if keep:                                          # keep one index for the live search box on the website
        for nm in [i["name"] for i in coll.list_search_indexes()]:
            if nm != keep:
                drop_index(coll, nm)
    return {"n": n, "queries": nq, "k": TOPK, "dims": emb.dims, "embedder": ai.embedder_info(), "corpus": {k: cp[k] for k in ("real", "generated")},
            "embed_s": round(t_emb, 1), "insert_s": round(t_ins, 2), "bruteforce_numpy_ms": round(t_bf, 3), "rows": rows, "curve": curve,
            "recommendation": rec, "notes": notes, "classroom_sample": sample, "search_available": hnsw_ok, "live_index": keep,
            "pass": bool(rows) and (not hnsw_ok or any(r["index"].startswith("HNSW") and r["recall"] >= 90 for r in rows))}


def _bf_mongo(coll, q):
    docs = list(coll.find({}, {"embedding": 1}))
    m = np.array([d["embedding"] for d in docs], dtype=np.float32)
    s = m @ q
    return [docs[i] for i in np.argsort(-s)[:TOPK]]


def recommend(rows: list[dict]) -> dict:
    def best(prefix, min_recall):
        c = [r for r in rows if r["index"].startswith(prefix) and r["recall"] >= min_recall]
        return min(c, key=lambda r: r["avg_ms"]) if c else None
    out = {}
    for target in (95, 90, 80):
        h, i = best("HNSW", target), best("IVF", target)
        out[f"recall_{target}"] = {"hnsw": h, "ivf": i,
                                   "faster": None if not (h and i) else ("HNSW" if h["avg_ms"] <= i["avg_ms"] else "IVF")}
    hmax = max((r["recall"] for r in rows if r["index"].startswith("HNSW")), default=None)
    imax = max((r["recall"] for r in rows if r["index"].startswith("IVF")), default=None)
    out["max_recall"] = {"hnsw": hmax, "ivf": imax}
    text = []
    if hmax is not None:
        r95 = out["recall_95"]
        text.append(f"For recall-critical uses (customer-facing search, RAG) use HNSW: it reached {hmax:.1f} % recall@20"
                    + (f"; {r95['hnsw']['params'].split(';')[-1].strip()} gave >= 95 % at {r95['hnsw']['avg_ms']:.2f} ms." if r95["hnsw"] else "."))
    if imax is not None:
        r90 = out["recall_90"]["ivf"]
        text.append(f"IVF trades recall for scanned data: nprobe controls the trade-off (max {imax:.1f} % here)"
                    + (f", >= 90 % with {r90['params']} at {r90['avg_ms']:.2f} ms." if r90 else ".")
                    + " Its lists are plain indexed documents, so it needs little memory and suits very large, cost-sensitive"
                      " collections, or databases without an ANN engine; in Atlas, HNSW is the only ANN index type.")
    text.append("numCandidates (HNSW) and nprobe (IVF) are query-time knobs: raise them until recall is good enough, then stop.")
    out["text"] = text
    return out


def sample_checks(coll, emb) -> dict:
    """The classroom sample tested as written: a 1536-dimension index, and the solution's index options."""
    out = {}
    probe = coll.database["sample_1536"]
    probe.drop()
    probe.insert_many([{"_id": i, "text": f"doc {i}", "embedding": [float(v) for v in emb.embed([f"MongoDB sample document {i}"])[0]]}
                       for i in range(20)])
    try:
        create_vector_index(probe, "vector_index", 1536, timeout=180)
        try:
            r = vector_search(probe, emb.embed(["MongoDB"])[0], limit=5, num_candidates=50)
            out["dims_mismatch"] = {"index_dims": 1536, "vector_dims": emb.dims, "result": f"{len(r)} results (no error)"}
        except OperationFailure as exc:
            out["dims_mismatch"] = {"index_dims": 1536, "vector_dims": emb.dims, "result": f"error {exc.code}: {str(exc.details.get('errmsg', exc))[:200]}"}
    except Exception as exc:                          # noqa: BLE001
        out["dims_mismatch"] = {"result": f"index creation: {str(exc)[:200]}"}
    sol = {"fields": [{"type": "vector", "path": "embedding", "numDimensions": emb.dims, "similarity": "cosine",
                       "indexOption": {"type": "ivf", "nlist": 100}}]}
    try:
        drop_index(probe, "ivf_attempt")
        probe.create_search_index(SearchIndexModel(definition=sol, name="ivf_attempt", type="vectorSearch"))
        time.sleep(3)
        st = _index_state(probe, "ivf_attempt") or {}
        out["solution_ivf_option"] = {"definition": sol, "result": f"accepted, status {st.get('status')} {st.get('message', '')}"[:240]}
    except OperationFailure as exc:
        out["solution_ivf_option"] = {"definition": sol, "result": f"refused: {str(exc.details.get('errmsg', exc))[:220]}"}
    for nm in ("vector_index", "ivf_attempt"):
        drop_index(probe, nm, timeout=30)
    probe.drop()
    return out


# ============================================================================ Applied: Ask Kavach (Lab 17 on Lab 7)
def build_kavach(limit: int = 3000, log=print) -> dict:
    emb = ai.embedder()
    reps = list(PG.mongo()["cti_platform"].reports.find({}, {"title": 1, "summary": 1, "url": 1, "published_at": 1, "source_id": 1})
                .sort("published_at", -1).limit(limit))
    if not reps:
        raise RuntimeError("the Lab 7 platform has no reports yet")
    texts = [f"{r.get('title', '')}. {r.get('summary', '')}"[:1200] for r in reps]
    t0 = time.perf_counter()
    vec = emb.embed(texts)
    coll = vclient()[KAV_DB]["reports"]
    coll.delete_many({})
    coll.insert_many([{"_id": str(r["_id"]), "text": texts[i], "title": r.get("title", ""), "url": r.get("url", ""),
                       "published": r.get("published_at"), "source": r.get("source_id", ""), "embedding": [float(v) for v in vec[i]]}
                      for i, r in enumerate(reps)])
    idx = try_index(coll, "vector_index", emb.dims)
    log(f"   Ask Kavach: {len(reps)} Lab 7 reports embedded + indexed in {time.perf_counter() - t0:.1f} s")
    return {"reports": len(reps), "index": idx, "seconds": round(time.perf_counter() - t0, 1)}


def ask_kavach(question: str, k: int = 5) -> dict:
    emb = ai.embedder()
    coll = vclient()[KAV_DB]["reports"]
    q = emb.embed([question], kind="query")[0]
    t0 = time.perf_counter()
    try:
        hits = vector_search(coll, q, limit=k, num_candidates=100, project={"text": 1, "title": 1, "url": 1, "published": 1, "source": 1})
        mode = "$vectorSearch"
    except (OperationFailure, PyMongoError):
        docs = list(coll.find({}, {"text": 1, "title": 1, "url": 1, "published": 1, "source": 1, "embedding": 1}))
        m = np.array([d["embedding"] for d in docs], dtype=np.float32)
        s = m @ q
        hits = [{**{kk: docs[i].get(kk) for kk in ("_id", "text", "title", "url", "published", "source")}, "score": float((s[i] + 1) / 2)}
                for i in np.argsort(-s)[:k]]
        mode = "numpy-exact"
    ms = (time.perf_counter() - t0) * 1000
    g = ai.generate(question, [{"text": h["text"], "source": h.get("title", "")} for h in hits])
    return {"question": question, "hits": hits, "answer": g["answer"], "provider": g["provider"], "mode": mode, "search_ms": round(ms, 2)}


def run_applied(log=print) -> dict:
    b = build_kavach(log=log)
    qs = ["Which ransomware attacks hit hospitals?", "Is any VPN vulnerability being exploited?", "phishing campaigns stealing credentials"]
    out = []
    for q in qs:
        a = ask_kavach(q)
        out.append({"question": q, "top": [h.get("title", "")[:120] for h in a["hits"][:3]], "answer": a["answer"][:500], "ms": a["search_ms"]})
        log(f"   Ask Kavach: {q}  ->  {out[-1]['top'][0] if out[-1]['top'] else '-'}")
    return {"build": b, "questions": out, "pass": b["reports"] > 0}


# ============================================================================ status / results
def search_live(text: str, k: int = 10) -> dict:
    emb = ai.embedder()
    coll = vclient()[VEC_DB]["sentences"]
    q = emb.embed([text], kind="query")[0]
    names = [i["name"] for i in coll.list_search_indexes()]
    if not names:
        raise RuntimeError("no vector index yet - run Lab 17.2 first")
    t0 = time.perf_counter()
    hits = vector_search(coll, q, index=names[0], limit=k, num_candidates=10 * k, project={"text": 1})
    return {"query": text, "index": names[0], "hits": hits, "ms": round((time.perf_counter() - t0) * 1000, 2)}


def status() -> dict:
    s = search_ok()
    out = {"available": s["ok"], "vector": s, "embedder": ai.embedder_info(), "generator": ai.generator_info(), "uri": PG.VECTOR_URI.split("?")[0]}
    if not s["ok"]:
        out["reason"] = s.get("error")
        return out
    c = vclient()
    out["counts"] = {"rag_chunks": c[RAG_DB].documents.estimated_document_count(), "conversations": c[RAG_DB].conversations.estimated_document_count(),
                     "sentences": c[VEC_DB].sentences.estimated_document_count(), "kavach_reports": c[KAV_DB].reports.estimated_document_count()}
    try:
        out["indexes"] = [{"collection": f"{d}.{col}", "name": i["name"], "status": i.get("status"), "queryable": i.get("queryable")}
                          for d, col in ((RAG_DB, "documents"), (VEC_DB, "sentences"), (KAV_DB, "reports")) for i in c[d][col].list_search_indexes()]
    except PyMongoError:
        out["indexes"] = []
    return out


def load_results() -> dict:
    p = RESULTS / "lab17_results.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return {}


def save_part(name: str, value: dict):
    out = load_results()
    out[name] = _jd(value)
    out.setdefault("environment", {})["updated"] = datetime.now(UTC).isoformat()
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "lab17_results.json").write_text(json.dumps(out, indent=2, default=str))

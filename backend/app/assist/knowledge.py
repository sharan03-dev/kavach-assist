"""A.2 - embedding and ingestion pipeline, and the retrieval half of RAG.

Sources -> chunks -> embeddings -> MongoDB collection `knowledge` {text, embedding, kind, platforms, status, ...} with an
Atlas Vector Search index (cosine, filter fields kind / platforms / status):

    threat     the Kavach threat catalog (vulnerabilities + threat reports with platform and status)
    playbook   mitigation procedures per platform and per threat type (knowledge_base/playbook_*.md)
    lab        the course labs 7-18: guide, READMEs and the latest PASS / FAIL results
    faq        NoSQL / MongoDB concepts            about   what Kavach and this assistant are
    cti        the threat-intelligence primers of Lab 17

Ingestion is incremental: a chunk is embedded again only when its text (or the embedding model) changed.
Retrieval uses $vectorSearch; when the search engine is not available it falls back to exact cosine search in NumPy.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from pathlib import Path

import numpy as np
from pymongo import UpdateOne
from pymongo.errors import PyMongoError
from pymongo.operations import SearchIndexModel

from .. import ai
from . import settings as S
from . import store

ROOT = Path(__file__).resolve().parents[3]
KB = Path(__file__).resolve().parent / "knowledge_base"
RESULTS = Path(os.getenv("RESULTS_DIR", str(ROOT / "results")))
INDEX = "vector_index"
MAX_THREAT_CHUNKS = int(os.getenv("ASSIST_MAX_THREAT_CHUNKS", "5000"))
PLAYBOOK_PLATFORM = {"linux": "Linux", "windows": "Windows", "macos": "macOS", "android": "Android", "ios": "iOS",
                     "network": "Network devices", "web_server": "Web & server software", "cloud": "Cloud & SaaS"}
_lock = threading.Lock()
_mem: dict = {"key": None, "ids": [], "m": None, "docs": {}}
_ingest_lock = threading.Lock()


def _words(text: str, size: int = 150, overlap: int = 25) -> list[str]:
    w = text.split()
    if len(w) <= size:
        return [" ".join(w)] if w else []
    out, step = [], size - overlap
    for i in range(0, len(w), step):
        out.append(" ".join(w[i:i + size]))
        if i + size >= len(w):
            break
    return out


def _sections(md: str) -> list[tuple[str, str]]:
    """[(heading, body)] of a markdown file, split at ## / ### headings; code fences are kept as text."""
    out, head, buf = [], "", []
    for line in md.splitlines():
        m = re.match(r"^#{1,3}\s+(.*)", line)
        if m:
            if " ".join(buf).strip():
                out.append((head, "\n".join(buf).strip()))
            head, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if " ".join(buf).strip():
        out.append((head, "\n".join(buf).strip()))
    return out


def _clean(md: str, light: bool = False) -> str:
    """Plain text of a markdown fragment. light=True keeps | _ > (they are part of the shell commands in the playbooks)."""
    t = re.sub(r"```.*?```", lambda m: " " + m.group(0).strip("`").strip() + " ", md, flags=re.S)
    t = re.sub(r"\*+", "", t) if light else re.sub(r"[|*_>#]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _static_chunks() -> list[dict]:
    out = []
    for f in sorted(KB.glob("*.md")):
        stem = f.stem
        if stem.startswith("playbook_"):
            kind, plat = "playbook", [PLAYBOOK_PLATFORM.get(stem[len("playbook_"):], "All")]
        else:
            kind, plat = {"labs_guide": "lab", "nosql_faq": "faq", "kavach_about": "about"}.get(stem, "faq"), ["All"]
        for i, (head, body) in enumerate(_sections(f.read_text(encoding="utf-8"))):
            m = re.search(r"\(Lab (\d+)\)", head)
            for j, part in enumerate(_words(_clean(body, light=True), 190, 20)):
                out.append({"_id": f"kb:{stem}:{i}:{j}", "kind": kind, "title": head, "text": f"{head}. {part}", "source": f"knowledge_base/{f.name}",
                            "platforms": plat, "status": None, "lab": int(m.group(1)) if m else None})
    return out


def _lab_chunks() -> list[dict]:
    out = []
    files = [(ROOT / "README.md", None)] + [(p, int(re.search(r"lab(\d+)", p.parent.name).group(1))) for p in sorted((ROOT / "labs").glob("lab*/README.md"))
                                             if re.search(r"lab(\d+)", p.parent.name)]
    for f, lab in files:
        if not f.exists():
            continue
        rel = str(f.relative_to(ROOT))
        for i, (head, body) in enumerate(_sections(f.read_text(encoding="utf-8", errors="replace"))):
            for j, part in enumerate(_words(_clean(body), 150, 25)):
                if len(part.split()) < 12:
                    continue
                label = f"Lab {lab}" if lab else "Project README"
                out.append({"_id": f"readme:{rel}:{i}:{j}", "kind": "lab", "title": f"{label}: {head}"[:160], "text": f"{label} - {head}. {part}",
                            "source": rel, "platforms": ["All"], "status": None, "lab": lab})
    docs = ROOT / "labs" / "lab17" / "docs"
    for f in sorted(docs.glob("*.md")) if docs.exists() else []:
        for i, (head, body) in enumerate(_sections(f.read_text(encoding="utf-8", errors="replace"))):
            for j, part in enumerate(_words(_clean(body), 150, 25)):
                out.append({"_id": f"cti:{f.name}:{i}:{j}", "kind": "cti", "title": head or f.stem, "text": f"{head}. {part}",
                            "source": f"labs/lab17/docs/{f.name}", "platforms": ["All"], "status": None, "lab": None})
    # the latest results of every lab: the SUMMARY block of results/console_<n>.txt ([PASS] / [FAIL] lines)
    for n in range(7, 19):
        f = RESULTS / f"console_{n}.txt"
        if not f.exists():
            continue
        txt = f.read_text(encoding="utf-8", errors="replace")
        checks = re.findall(r"\[(PASS|FAIL)\]\s+(.+)", txt.split("SUMMARY")[-1] if "SUMMARY" in txt else txt)
        if not checks:
            continue
        passed = sum(1 for s, _ in checks if s == "PASS")
        lines = [f"{'passed' if s == 'PASS' else 'FAILED'}: {t.strip()}" for s, t in checks]
        head = f"Lab {n} latest results: {passed} of {len(checks)} checks passed"
        for j, part in enumerate(_words(". ".join(lines), 170, 0)):
            out.append({"_id": f"result:{n}:{j}", "kind": "lab", "title": head, "text": f"{head}. {part}", "source": f"results/console_{n}.txt",
                        "platforms": ["All"], "status": None, "lab": n})
    return out


def _threat_chunks() -> list[dict]:
    out = []
    cur = store.adb().threats.find({}, {"title": 1, "description": 1, "platforms": 1, "status": 1, "status_reason": 1, "severity": 1, "vendor": 1,
                                        "product": 1, "kind": 1, "url": 1, "cve_ids": 1}).sort([("priority", -1), ("_id", 1)]).limit(MAX_THREAT_CHUNKS)
    for t in cur:
        ident = t["_id"] if t["kind"] == "vulnerability" else "Threat report"
        prod = f" in {t.get('vendor', '')} {t.get('product', '')}".rstrip() if (t.get("vendor") or t.get("product")) else ""
        cves = f" Related: {', '.join(t['cve_ids'][:5])}." if t.get("cve_ids") else ""
        text = (f"{ident}: {t.get('title', '')}{prod}. Platform: {', '.join(t.get('platforms') or [])}. Severity: {t.get('severity')}. "
                f"Status: {t.get('status')} - {t.get('status_reason', '')}. {(t.get('description') or '')[:700]}{cves}")
        out.append({"_id": f"threat:{t['_id']}", "kind": "threat", "title": f"{ident}: {t.get('title', '')}"[:200], "text": text, "source": t["_id"],
                    "url": t.get("url"), "platforms": t.get("platforms") or ["Other"], "status": t.get("status"), "lab": None})
    return out


def _hash(text: str, model: str) -> str:
    return hashlib.blake2b(f"{model}\n{text}".encode(), digest_size=10).hexdigest()


def index_state() -> dict:
    try:
        for d in store.knowledge().list_search_indexes(INDEX):
            dims = None
            for f in ((d.get("latestDefinition") or d.get("definition") or {}).get("fields") or []):
                if f.get("type") == "vector":
                    dims = f.get("numDimensions")
            return {"exists": True, "queryable": bool(d.get("queryable")), "status": d.get("status"), "dims": dims}
    except Exception as exc:                           # noqa: BLE001 - a server without mongot has no search indexes
        return {"exists": False, "queryable": False, "status": "NO_SEARCH_ENGINE", "error": f"{type(exc).__name__}"}
    return {"exists": False, "queryable": False, "status": "MISSING"}


def ensure_index(dims: int, wait_s: int = 150, log=print) -> dict:
    """Create the vectorSearch index once (re-created only when the embedding size changes)."""
    st = index_state()
    if st.get("status") == "NO_SEARCH_ENGINE":
        return st
    coll = store.knowledge()
    try:
        if st["exists"] and st.get("dims") != dims:
            coll.drop_search_index(INDEX)
            t0 = time.time()
            while index_state()["exists"] and time.time() - t0 < 60:
                time.sleep(1)
            st = {"exists": False}
        if not st["exists"]:
            definition = {"fields": [{"type": "vector", "path": "embedding", "numDimensions": dims, "similarity": "cosine"},
                                     {"type": "filter", "path": "kind"}, {"type": "filter", "path": "platforms"}, {"type": "filter", "path": "status"}]}
            coll.create_search_index(SearchIndexModel(definition=definition, name=INDEX, type="vectorSearch"))
            log(f"[assist] vectorSearch index '{INDEX}' created ({dims} dimensions, cosine, filters: kind, platforms, status)")
        t0 = time.time()
        while time.time() - t0 < wait_s:
            st = index_state()
            if st.get("queryable"):
                break
            time.sleep(1)
        return st
    except Exception as exc:                           # noqa: BLE001
        return {"exists": False, "queryable": False, "status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def ingest(log=print, rebuild_threats: bool = True) -> dict:
    """The whole pipeline. Returns counts; safe to run repeatedly (only changed chunks are embedded again)."""
    from . import threats
    if not _ingest_lock.acquire(blocking=False):
        return {"skipped": "an ingestion is already running"}
    try:
        t0 = time.time()
        store.set_setting("ingest_state", {"running": True, "started_at": store.now()})
        tr = threats.rebuild(log=log) if rebuild_threats else None
        emb = ai.embedder()
        model = f"{emb.name}:{getattr(emb, 'model', '')}:{emb.dims}"
        chunks = _static_chunks() + _lab_chunks() + _threat_chunks()
        coll = store.knowledge()
        have = {d["_id"]: d.get("h") for d in coll.find({}, {"h": 1})}
        todo = []
        for c in chunks:
            c["h"] = _hash(c["text"], model)
            if have.get(c["_id"]) != c["h"]:
                todo.append(c)
        embedded = 0
        for i in range(0, len(todo), 256):
            batch = todo[i:i + 256]
            vecs = emb.embed([c["text"] for c in batch])
            now = store.now()
            coll.bulk_write([UpdateOne({"_id": c["_id"]}, {"$set": {**c, "embedding": [float(x) for x in vecs[k]], "updated_at": now}}, upsert=True)
                             for k, c in enumerate(batch)], ordered=False)
            embedded += len(batch)
            store.set_setting("ingest_state", {"running": True, "started_at": store.now(), "progress": f"{embedded}/{len(todo)} chunks embedded"})
        keep = {c["_id"] for c in chunks}
        stale = [i for i in have if i not in keep]
        for i in range(0, len(stale), 2000):
            coll.delete_many({"_id": {"$in": stale[i:i + 2000]}})
        idx = ensure_index(emb.dims, log=log)
        by_kind: dict = {}
        for c in chunks:
            by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
        res = {"chunks": len(chunks), "embedded": embedded, "removed": len(stale), "by_kind": by_kind, "seconds": round(time.time() - t0, 1),
               "embedder": {"provider": emb.name, "model": getattr(emb, "model", emb.name), "dims": emb.dims}, "index": idx,
               "vector_store": store.vector_where(), "threats": tr, "at": store.now()}
        store.set_setting("ingest_last", res)
        with _lock:
            _mem["key"] = None
        log(f"[assist] knowledge base: {len(chunks):,} chunks ({embedded:,} embedded, {len(stale):,} removed) in {res['seconds']} s; "
            f"vector store {res['vector_store']}, index {idx.get('status')}")
        return res
    finally:
        store.set_setting("ingest_state", {"running": False, "finished_at": store.now()})
        _ingest_lock.release()


# ---------------------------------------------------------------------------- retrieval
def _filter(kinds, platform, status, strict: bool = False) -> dict | None:
    f = []
    if kinds:
        f.append({"kind": {"$in": list(kinds)}})
    if platform:
        f.append({"platforms": {"$in": [platform] if strict else [platform, "All"]}})
    if status:
        f.append({"$or": [{"status": status}, {"kind": {"$in": ["playbook", "lab", "faq", "about", "cti"]}}]})
    return {"$and": f} if len(f) > 1 else (f[0] if f else None)


PROJ = {"text": 1, "title": 1, "kind": 1, "source": 1, "url": 1, "platforms": 1, "status": 1, "lab": 1}


def _numpy_search(q: np.ndarray, kinds, platform, status, k: int, strict: bool = False) -> list[dict]:
    coll = store.knowledge()
    key = (store.vector_where(), (store.setting("ingest_last") or {}).get("at"), coll.estimated_document_count())
    with _lock:
        if _mem["key"] != key:
            docs = list(coll.find({}, {**PROJ, "embedding": 1}))
            _mem.update(key=key, ids=[d["_id"] for d in docs],
                        m=np.array([d.pop("embedding") for d in docs], dtype=np.float32) if docs else np.zeros((0, len(q)), np.float32),
                        docs={d["_id"]: d for d in docs})
        ids, m, docs = _mem["ids"], _mem["m"], _mem["docs"]
    if not ids or m.shape[1] != len(q):
        return []
    s = m @ q
    out = []
    for i in np.argsort(-s):
        d = docs[ids[i]]
        if kinds and d.get("kind") not in kinds:
            continue
        if platform and not (({platform} if strict else {platform, "All"}) & set(d.get("platforms") or [])):
            continue
        if status and d.get("kind") == "threat" and d.get("status") != status:
            continue
        out.append({**d, "score": float((s[i] + 1) / 2)})
        if len(out) >= k:
            break
    return out


def retrieve(query: str, kinds=None, platform: str | None = None, status: str | None = None, k: int = 6, strict_platform: bool = False) -> dict:
    """Top-k chunks for a question. {hits, mode, ms}. strict_platform: only chunks of that platform (not the general ones)."""
    emb = ai.embedder()
    q = emb.embed([query], kind="query")[0]
    t0 = time.perf_counter()
    mode, hits = "$vectorSearch", []
    try:
        if not store.vector_ok():
            raise PyMongoError("vector server not reachable")
        vs = {"index": INDEX, "path": "embedding", "queryVector": [float(x) for x in q], "numCandidates": max(120, 20 * k), "limit": k}
        f = _filter(kinds, platform, status, strict_platform)
        if f:
            vs["filter"] = f
        hits = list(store.knowledge().aggregate([{"$vectorSearch": vs}, {"$project": {**PROJ, "score": {"$meta": "vectorSearchScore"}}}]))
        if not hits and store.knowledge().estimated_document_count() and not index_state().get("queryable"):
            raise PyMongoError("index not queryable yet")
    except Exception:                                  # noqa: BLE001 - no mongot / index still building -> exact search
        mode = "numpy-exact"
        try:
            hits = _numpy_search(np.asarray(q, dtype=np.float32), set(kinds) if kinds else None, platform, status, k, strict_platform)
        except PyMongoError:
            hits = []
    # hybrid retrieval: add what MongoDB's text index finds for the exact words (names, acronyms such as "HNSW")
    try:
        have = {h["_id"] for h in hits}
        q2: dict = {"$text": {"$search": query[:300]}}
        if kinds:
            q2["kind"] = {"$in": list(kinds)}
        if platform:
            q2["platforms"] = {"$in": [platform] if strict_platform else [platform, "All"]}
        if status:
            q2["$or"] = [{"status": status}, {"kind": {"$ne": "threat"}}]
        lex = list(store.knowledge().find(q2, {**PROJ, "ts": {"$meta": "textScore"}}).sort([("ts", {"$meta": "textScore"})]).limit(max(2, k // 2)))
        for d in lex:
            if d["_id"] not in have:
                hits.append({**{kk: vv for kk, vv in d.items() if kk != "ts"}, "score": 0.5, "lexical": round(float(d.get("ts", 0)), 2)})
        if lex and mode == "$vectorSearch":
            mode = "$vectorSearch + $text"
        elif lex:
            mode += " + $text"
    except PyMongoError:
        pass
    return {"hits": hits, "mode": mode, "ms": round((time.perf_counter() - t0) * 1000, 1)}


def stats() -> dict:
    try:
        coll = store.knowledge()
        by_kind = {x["_id"]: x["n"] for x in coll.aggregate([{"$group": {"_id": "$kind", "n": {"$sum": 1}}}])}
        total = sum(by_kind.values())
    except PyMongoError as exc:
        return {"error": str(exc)[:120]}
    return {"chunks": total, "by_kind": by_kind, "vector_store": store.vector_where(), "index": index_state(),
            "last": store.setting("ingest_last"), "state": store.setting("ingest_state")}


def by_title(title: str) -> dict | None:
    """One knowledge chunk by its exact heading (used for the fixed 'what to do about an Active threat' procedures)."""
    try:
        d = store.knowledge().find_one({"kind": "playbook", "title": title}, PROJ)
        return {**d, "score": 1.0} if d else None
    except PyMongoError:
        return None


def of_lab(n: int) -> list[dict]:
    """The guide entry and the latest results of one lab (exact MongoDB query, no vector search needed)."""
    try:
        docs = list(store.knowledge().find({"lab": n, "_id": {"$regex": "^(kb:|result:)"}}, PROJ).sort("_id", 1).limit(4))
        return [{**d, "score": 1.0} for d in docs]
    except PyMongoError:
        return []


def lab_results() -> list[dict]:
    """[{lab, title}] - the 'Lab N latest results: x of y checks passed' headline of every lab that has results."""
    try:
        rows = list(store.knowledge().find({"_id": {"$regex": "^result:\\d+:0$"}}, {"lab": 1, "title": 1}))
    except PyMongoError:
        return []
    return sorted(({"lab": r.get("lab"), "title": r.get("title")} for r in rows), key=lambda r: r["lab"] or 0)

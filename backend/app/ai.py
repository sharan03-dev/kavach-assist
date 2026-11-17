"""Lab 17 - embedding models and the answer generator (LLM) behind the RAG pipeline.

Embeddings (EMBEDDER=auto picks the first that works):
    voyage      Voyage AI REST API (VOYAGE_API_KEY, VOYAGE_MODEL=voyage-3 -> 1024 dimensions)          - the course sample
    fastembed   sentence-transformers/all-MiniLM-L6-v2 run locally with ONNX Runtime (384 dimensions) - the 17.2 solution's
                model; baked into the Docker image at build time, so it works offline
    hashing     a deterministic 384-dimension feature-hashing embedder (words + word pairs) - last resort, no download
Generator (LLM_PROVIDER=auto):
    anthropic   ANTHROPIC_API_KEY (LLM_MODEL, default claude-haiku-4-5)
    ollama      OLLAMA_URL, e.g. http://host.docker.internal:11434 (OLLAMA_MODEL, default llama3.2:1b) - "a local model"
    extractive  built in: picks the sentences of the retrieved chunks that best match the question and cites them [1]..[3]
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import threading
import time

import numpy as np

STOP = set("""a an the and or of to in on for with by from as at is are was were be been being this that these those it its into
over under than then there here what which who whom whose how why when where can could should would may might will shall do does did
not no yes if else about between after before during while through per via our your their his her they we you i me my mine
also such any each other more most some many much very just only own same so too up down out off again further once""".split())
_lock = threading.Lock()
_cache: dict = {}


def _tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9\-\.]*[a-z0-9]|[a-z0-9]", text.lower()):
        if w in STOP or len(w) < 2:
            continue
        for suf in ("ing", "ers", "ies", "ed", "es", "er", "ly", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.append(w)
    return out


class HashingEmbedder:
    name, dims, model = "hashing", 384, "feature-hashing-384 (words + word pairs, signed)"

    def embed(self, texts: list[str], kind: str = "document") -> np.ndarray:
        m = np.zeros((len(texts), self.dims), dtype=np.float32)
        for i, t in enumerate(texts):
            toks = _tokens(t)
            tf: dict = {}
            for f in toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]:
                tf[f] = tf.get(f, 0) + 1
            for f, c in tf.items():                      # sub-linear term frequency: 1 + log(tf)
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                m[i, h % self.dims] += (1.0 if (h >> 63) & 1 else -1.0) * (1.0 + math.log(c)) * (1.0 if "_" not in f else 0.5)
        n = np.linalg.norm(m, axis=1, keepdims=True)
        n[n == 0] = 1
        return m / n


class FastEmbedder:
    name, dims = "fastembed", 384

    def __init__(self):
        from fastembed import TextEmbedding
        self.model = os.getenv("FASTEMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        self._m = TextEmbedding(self.model, cache_dir=os.getenv("FASTEMBED_CACHE", "/opt/models"))
        self.dims = len(next(iter(self._m.embed(["probe"]))))

    def embed(self, texts: list[str], kind: str = "document") -> np.ndarray:
        m = np.array(list(self._m.embed(list(texts), batch_size=64)), dtype=np.float32)
        n = np.linalg.norm(m, axis=1, keepdims=True)
        n[n == 0] = 1
        return m / n


class VoyageEmbedder:
    name = "voyage"

    def __init__(self):
        import httpx
        self.key = os.environ["VOYAGE_API_KEY"]
        self.model = os.getenv("VOYAGE_MODEL", "voyage-3")
        self.http = httpx.Client(timeout=60)
        self.dims = len(self._call(["probe"], "document")[0])

    def _call(self, texts, kind):
        r = self.http.post("https://api.voyageai.com/v1/embeddings", headers={"Authorization": f"Bearer {self.key}"},
                           json={"input": texts, "model": self.model, "input_type": "query" if kind == "query" else "document"})
        r.raise_for_status()
        return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]

    def embed(self, texts: list[str], kind: str = "document") -> np.ndarray:
        out = []
        for i in range(0, len(texts), 128):
            out += self._call(list(texts[i:i + 128]), kind)
        m = np.array(out, dtype=np.float32)
        return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)


def embedder():
    """The embedder chosen once per process (EMBEDDER = auto | voyage | fastembed | hashing)."""
    with _lock:
        if "emb" in _cache:
            return _cache["emb"]
        want = os.getenv("EMBEDDER", "auto").lower()
        errors = {}
        order = [want] if want != "auto" else (["voyage"] if os.getenv("VOYAGE_API_KEY") else []) + ["fastembed", "hashing"]
        emb = None
        for name in order:
            try:
                emb = {"voyage": VoyageEmbedder, "fastembed": FastEmbedder, "hashing": HashingEmbedder}[name]()
                break
            except Exception as exc:                  # noqa: BLE001
                errors[name] = f"{type(exc).__name__}: {str(exc)[:160]}"
        emb = emb or HashingEmbedder()
        emb.fallback_errors = errors
        _cache["emb"] = emb
        return emb


def embedder_info() -> dict:
    e = embedder()
    return {"provider": e.name, "model": getattr(e, "model", e.name), "dims": e.dims, "skipped": getattr(e, "fallback_errors", {})}


# ============================================================================ generator
def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.split()) >= 5]


def extractive_answer(question: str, chunks: list[dict], history: list[dict] | None = None) -> str:
    q = set(_tokens(question))
    for h in (history or [])[-2:]:
        if h.get("role") == "user":
            q |= set(_tokens(h["content"])) if len(q) < 4 else set()
    scored = []
    for i, c in enumerate(chunks):
        for j, s in enumerate(_sentences(c["text"])):
            st = set(_tokens(s))
            overlap = len(q & st)
            if overlap:
                scored.append((overlap / math.sqrt(len(st) + 1) + 0.15 * (len(chunks) - i) - 0.01 * j, i, s))
    if not scored:
        return "I could not find this in the stored documents."
    scored.sort(reverse=True)
    picked, seen = [], set()
    for _, i, s in scored:
        if s not in seen:
            picked.append((i, s)); seen.add(s)
        if len(picked) == 3:
            break
    picked.sort(key=lambda x: x[0])
    return " ".join(f"{s} [{i + 1}]" for i, s in picked)


def generate(question: str, chunks: list[dict], history: list[dict] | None = None) -> dict:
    """Answer with the retrieved chunks as the only context. Returns {answer, provider, model, prompt, ms}."""
    ctx = "\n\n".join(f"[{i + 1}] ({c.get('source', '')}) {c['text']}" for i, c in enumerate(chunks))
    hist = "\n".join(f"{h['role']}: {h['content']}" for h in (history or []))
    prompt = (f"Answer based on these documents:\n{ctx}\n\n" + (f"Conversation so far:\n{hist}\n\n" if hist else "") +
              f"Question: {question}\nAnswer in 2-4 sentences, cite documents as [1], [2], [3]. If the documents do not contain "
              "the answer, say so.")
    t0 = time.perf_counter()
    want = os.getenv("LLM_PROVIDER", "auto").lower()
    errors = {}
    if want in ("auto", "anthropic") and os.getenv("ANTHROPIC_API_KEY"):
        try:
            import httpx
            model = os.getenv("LLM_MODEL", "claude-haiku-4-5")
            r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60,
                           headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                           json={"model": model, "max_tokens": 400, "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json()["content"])
            return {"answer": text.strip(), "provider": "anthropic", "model": model, "prompt": prompt, "ms": round((time.perf_counter() - t0) * 1000)}
        except Exception as exc:                      # noqa: BLE001
            errors["anthropic"] = str(exc)[:160]
    if want in ("auto", "ollama") and os.getenv("OLLAMA_URL"):
        try:
            import httpx
            model = os.getenv("OLLAMA_MODEL", "llama3.2:1b")
            r = httpx.post(os.environ["OLLAMA_URL"].rstrip("/") + "/api/generate", timeout=120,
                           json={"model": model, "prompt": prompt, "stream": False})
            r.raise_for_status()
            return {"answer": r.json()["response"].strip(), "provider": "ollama", "model": model, "prompt": prompt,
                    "ms": round((time.perf_counter() - t0) * 1000)}
        except Exception as exc:                      # noqa: BLE001
            errors["ollama"] = str(exc)[:160]
    return {"answer": extractive_answer(question, chunks, history), "provider": "extractive",
            "model": "built-in extractive generator (no API key needed)", "prompt": prompt,
            "ms": round((time.perf_counter() - t0) * 1000, 2), "skipped": errors}


def generator_info() -> dict:
    if os.getenv("ANTHROPIC_API_KEY") and os.getenv("LLM_PROVIDER", "auto") in ("auto", "anthropic"):
        return {"provider": "anthropic", "model": os.getenv("LLM_MODEL", "claude-haiku-4-5")}
    if os.getenv("OLLAMA_URL") and os.getenv("LLM_PROVIDER", "auto") in ("auto", "ollama"):
        return {"provider": "ollama", "model": os.getenv("OLLAMA_MODEL", "llama3.2:1b")}
    return {"provider": "extractive", "model": "built-in extractive generator"}

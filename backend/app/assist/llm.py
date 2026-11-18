"""A.1 - the language model behind the chat.

    ollama      a small open model running locally (recommended in the assignment): no API key, no per-query cost, works offline.
                OLLAMA_BASE_URL points to the `ollama` container or to Ollama on the host (http://host.docker.internal:11434).
                The model is pulled automatically the first time (ASSIST_OLLAMA_MODEL, default llama3.2:3b; llama3.2:1b on small machines).
    openai      OPENAI_API_KEY  (gpt-4o-mini, the assignment's Option 1)
    anthropic   ANTHROPIC_API_KEY
    builtin     no model at all: the answer is composed from the MongoDB results and retrieved passages (always available,
                so the application keeps working while a model downloads or when the machine is too small)

LLM_BACKEND=auto tries ollama -> openai -> anthropic and falls back to builtin. Tokens are streamed to the browser.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time

import httpx

from . import settings as S

_lock = threading.Lock()
_state: dict = {"checked": 0.0, "ollama": {"ok": False, "reason": "not checked yet"}, "pulling": False, "pull_error": None, "model": None}
_http = httpx.Client(timeout=httpx.Timeout(10.0, read=300.0))


def close() -> None:
    try:
        _http.close()
    except Exception:                                  # noqa: BLE001
        pass


def _auto_model() -> str:
    """llama3.2:3b needs about 4 GB of free memory; on a small Docker VM use the 1B model."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            kb = int(next(line for line in f if line.startswith("MemTotal")).split()[1])
        return "llama3.2:3b" if kb >= 7_300_000 else "llama3.2:1b"
    except Exception:                                  # noqa: BLE001
        return "llama3.2:3b"


def ollama_model() -> str:
    if _state["model"] is None:
        _state["model"] = _auto_model() if S.OLLAMA_MODEL in ("", "auto") else S.OLLAMA_MODEL
    return _state["model"]


def _pull(model: str) -> None:
    try:
        with _http.stream("POST", f"{S.OLLAMA_URL}/api/pull", json={"model": model, "stream": True}, timeout=httpx.Timeout(10.0, read=3600.0)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise RuntimeError(d["error"])
                if d.get("total"):
                    _state["pull_progress"] = f"{d.get('status', '')} {100 * d.get('completed', 0) // d['total']}%"
        _state["pull_error"] = None
    except Exception as exc:                           # noqa: BLE001
        _state["pull_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        _state["pulling"] = False
        _state["checked"] = 0.0


def ollama_status(force: bool = False) -> dict:
    """Is Ollama reachable and is the model there? Starts the download once if it is missing. Cached for 15 s."""
    if not S.OLLAMA_URL:
        return {"ok": False, "reason": "OLLAMA_BASE_URL is not set"}
    with _lock:
        if not force and time.time() - _state["checked"] < 15:
            return _state["ollama"]
        model = ollama_model()
        try:
            r = _http.get(f"{S.OLLAMA_URL}/api/tags", timeout=4)
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
            if any(n == model or n.split(":")[0] == model or n == f"{model}:latest" for n in names):
                st = {"ok": True, "model": model}
            elif _state["pulling"]:
                st = {"ok": False, "model": model, "reason": f"downloading {model}: {_state.get('pull_progress', 'starting')}"}
            elif _state["pull_error"] and time.time() - _state.get("pull_at", 0) < 300:
                st = {"ok": False, "model": model, "reason": f"could not download {model}: {_state['pull_error']}"}
            else:
                _state["pulling"], _state["pull_at"], _state["pull_progress"] = True, time.time(), "starting"
                threading.Thread(target=_pull, args=(model,), daemon=True).start()
                st = {"ok": False, "model": model, "reason": f"downloading {model} (first start only)"}
        except Exception as exc:                       # noqa: BLE001
            st = {"ok": False, "model": model, "reason": f"Ollama not reachable at {S.OLLAMA_URL} ({type(exc).__name__})"}
        _state["ollama"], _state["checked"] = st, time.time()
        return st


# ---------------------------------------------------------------------------- the "brain": base model + built-in Kavach knowledge
BRAIN_RULES = ("You are Kavach Assist, an expert AI assistant for cyber security and for the Kavach cyber threat intelligence platform "
               "(a MongoDB course project: FastAPI + MongoDB, labs 7 to 18). Answer EVERY question yourself, directly, from your own knowledge: "
               "threats on any platform (Linux, Windows, macOS, Android, iOS, cloud, network), the four threat states "
               "(Active = exploited now, Evolving = changing or spreading, Anticipated = likely soon, Resolved = fixed), precise numbered mitigation "
               "steps with exact commands, questions about the labs, NoSQL and MongoDB, and any general question. Never invent CVE ids, dates or "
               "version numbers: if you are not sure, say so. Be concise: for mitigation give at most 5 numbered steps, one short line each, with the exact command in a code block, and finish every step you start. Short markdown, no preamble.")


def brain_text() -> str:
    """BRAIN_RULES + a digest of the Kavach knowledge files (what the model 'knows' about the project)."""
    kb = pathlib.Path(__file__).parent / "knowledge_base"
    parts = [BRAIN_RULES, "KAVACH KNOWLEDGE:"]
    try:
        for name, limit in (("kavach_about.md", 1300), ("labs_guide.md", 1300)):
            parts.append((kb / name).read_text(encoding="utf-8")[:limit])
        for f in sorted(kb.glob("playbook_*.md")):
            parts.append(f.read_text(encoding="utf-8")[:380])
    except OSError:
        pass
    return "\n\n".join(parts)


def _build_brain(base: str) -> None:
    try:
        r = _http.post(f"{S.OLLAMA_URL}/api/create", json={"model": S.BRAIN_MODEL, "from": base, "system": brain_text(), "stream": False,
                                                           "parameters": {"temperature": 0.2, "num_ctx": 4096}}, timeout=httpx.Timeout(10.0, read=600.0))
        r.raise_for_status()
        _state["brain"], _state["brain_error"] = base, None
    except Exception as exc:                           # noqa: BLE001
        _state["brain_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        _state["brain_building"] = False


def ensure_brain(base: str) -> bool:
    """True when the custom model is ready on top of `base`; builds it once in the background otherwise (until then the base model gets the same text as system prompt)."""
    if _state.get("brain") == base:
        return True
    if not _state.get("brain_building") and time.time() - _state.get("brain_at", 0) > 120:
        _state["brain_building"], _state["brain_at"] = True, time.time()
        threading.Thread(target=_build_brain, args=(base,), daemon=True).start()
    return False


def report_failure(error: str) -> None:
    """Called when a generation failed. Out of memory with the 3B model -> switch to the 1B model (when the choice was automatic)."""
    _state["last_error"], _state["last_error_at"] = error[:200], time.time()
    if "memory" in error.lower() and S.OLLAMA_MODEL in ("", "auto") and _state.get("model") == "llama3.2:3b":
        _state["model"], _state["checked"] = "llama3.2:1b", 0.0
        _state["downgraded"] = "llama3.2:3b did not fit into memory; using llama3.2:1b"


def report_success() -> None:
    _state["last_error"] = None


def backend() -> dict:
    """Which generator answers the next question."""
    want = S.LLM_BACKEND
    if want in ("auto", "ollama") and S.OLLAMA_URL:
        st = ollama_status()
        if st["ok"]:
            out = {"provider": "ollama", "model": st["model"], "local": True}
            if _state.get("last_error") and time.time() - _state.get("last_error_at", 0) < 600:
                out["note"] = f"last answer fell back to the built-in writer: {_state['last_error']}"
            elif _state.get("downgraded"):
                out["note"] = _state["downgraded"]
            return out
        if want == "ollama":
            return {"provider": "builtin", "model": "built-in answer composer", "note": st.get("reason")}
        note = st.get("reason")
    else:
        note = None
    if want in ("auto", "openai") and S.OPENAI_API_KEY:
        return {"provider": "openai", "model": S.OPENAI_MODEL, "local": False}
    if want in ("auto", "anthropic") and S.ANTHROPIC_API_KEY:
        return {"provider": "anthropic", "model": S.ANTHROPIC_MODEL, "local": False}
    return {"provider": "builtin", "model": "built-in answer composer", "note": note or "no language model configured"}


def _est_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


def stream(system: str, messages: list[dict], usage: dict, max_tokens: int | None = None, direct: bool = False):
    """Yield the answer piece by piece. `usage` is filled with provider, model, prompt_tokens, completion_tokens.
    Raises on failure BEFORE the first piece, so the caller can fall back to the built-in composer."""
    b = backend()
    usage.update(provider=b["provider"], model=b["model"])
    max_tokens = max_tokens or S.LLM_MAX_TOKENS
    if b["provider"] == "ollama":
        model, sys_msgs = b["model"], [{"role": "system", "content": system}]
        if direct:                                     # straight to the Kavach model: its knowledge is built in, nothing is fetched from MongoDB
            if ensure_brain(b["model"]):
                model, sys_msgs = S.BRAIN_MODEL, []
                usage.update(model=f"{S.BRAIN_MODEL} ({b['model']})")
            else:
                sys_msgs = [{"role": "system", "content": brain_text()}]
        payload = {"model": model, "stream": True, "keep_alive": "30m",
                   "messages": sys_msgs + messages,
                   "options": {"temperature": 0.2, "num_predict": max_tokens, "num_ctx": 4096}}
        with _http.stream("POST", f"{S.OLLAMA_URL}/api/chat", json=payload,
                          timeout=httpx.Timeout(10.0, read=float(S.LLM_FIRST_TOKEN_S))) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"Ollama HTTP {r.status_code}: {r.read().decode('utf-8', 'replace')[:200]}")
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise RuntimeError(d["error"])
                piece = (d.get("message") or {}).get("content") or ""
                if piece:
                    yield piece
                if d.get("done"):
                    usage.update(prompt_tokens=d.get("prompt_eval_count"), completion_tokens=d.get("eval_count"))
        return
    if b["provider"] == "openai":
        r = _http.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {S.OPENAI_API_KEY}"},
                       json={"model": b["model"], "temperature": 0.2, "max_tokens": max_tokens,
                             "messages": [{"role": "system", "content": system}] + messages}, timeout=90)
        r.raise_for_status()
        d = r.json()
        usage.update(prompt_tokens=(d.get("usage") or {}).get("prompt_tokens"), completion_tokens=(d.get("usage") or {}).get("completion_tokens"))
        yield d["choices"][0]["message"]["content"]
        return
    if b["provider"] == "anthropic":
        r = _http.post("https://api.anthropic.com/v1/messages", headers={"x-api-key": S.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
                       json={"model": b["model"], "max_tokens": max_tokens, "system": system, "messages": messages}, timeout=90)
        r.raise_for_status()
        d = r.json()
        usage.update(prompt_tokens=(d.get("usage") or {}).get("input_tokens"), completion_tokens=(d.get("usage") or {}).get("output_tokens"))
        yield "".join(x.get("text", "") for x in d.get("content", []))
        return
    raise RuntimeError("no language model available")


def finish_usage(usage: dict, prompt_text: str, answer: str) -> dict:
    """Token counts for the metrics: the provider's own numbers, or an estimate (about 4 characters per token)."""
    if not usage.get("prompt_tokens"):
        usage["prompt_tokens"], usage["estimated"] = _est_tokens(prompt_text), True
    if not usage.get("completion_tokens"):
        usage["completion_tokens"] = _est_tokens(answer)
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return usage

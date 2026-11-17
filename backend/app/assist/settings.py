"""Configuration of Kavach Assist. Every secret comes from the environment (.env) - never from code (Exercise 12)."""
from __future__ import annotations

import os

from .. import config  # noqa: F401  (loads the project .env)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


ASSIST_DB = os.getenv("ASSIST_DB", "kavach_assist")          # operational data + conversation memory (replica set rs0)
VECTOR_URI = os.getenv("VECTOR_URI", "")                      # MongoDB Atlas Local (mongod + mongot) = the vector store
CTI_DB = config.DB_NAME                                       # the Kavach threat-intelligence platform (Lab 7)

# ---- login
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@kavach.local").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")              # empty -> a random one is generated and printed once
ADMIN_NAME = os.getenv("ADMIN_NAME", "Kavach Admin")
JWT_SECRET = os.getenv("JWT_SECRET", "")                      # empty -> generated once and kept in MongoDB
TOKEN_HOURS = _int("TOKEN_HOURS", 12)
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()  # empty -> the Google button explains how to enable it
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "1") not in ("0", "false", "no")
COOKIE = "kavach_token"

# ---- LLM (A.1): ollama (recommended, local) | openai | anthropic | builtin
LLM_BACKEND = os.getenv("LLM_BACKEND", "auto").lower()
OLLAMA_URL = (os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_URL") or "").rstrip("/")
OLLAMA_MODEL = os.getenv("ASSIST_OLLAMA_MODEL", "auto")       # auto = llama3.2:3b, or llama3.2:1b on a small machine
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5")
LLM_MAX_TOKENS = _int("LLM_MAX_TOKENS", 420)
LLM_FIRST_TOKEN_S = _int("LLM_FIRST_TOKEN_S", 120)

# ---- hardening (A.7)
CHAT_RATE_PER_MIN = _int("CHAT_RATE_PER_MIN", 20)
LOGIN_RATE_PER_10MIN = _int("LOGIN_RATE_PER_10MIN", 8)
HISTORY_EXCHANGES = _int("HISTORY_EXCHANGES", 5)

# ---- scanner bot
SCAN_TARGET = os.getenv("SCAN_TARGET", "/scan")
SCAN_INTERVAL = _int("SCAN_INTERVAL", 300)
SCAN_SYSTEM = os.getenv("SCAN_SYSTEM", "1") not in ("0", "false", "no")
OSV_URL = os.getenv("OSV_URL", "https://api.osv.dev")

PLATFORMS = ["Linux", "Windows", "macOS", "Android", "iOS", "Network devices", "Web & server software", "Cloud & SaaS", "Other"]
STATUSES = ["Active", "Evolving", "Anticipated", "Resolved"]

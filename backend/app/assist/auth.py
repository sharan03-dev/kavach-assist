"""Login for Kavach Assist: one administrator + users who sign in with a password or with their Google account.

    passwords   scrypt (salted, slow) - never stored or logged in clear text
    session     a signed JWT (HS256) in an HttpOnly cookie; the user document is re-read on every request, so a role
                change, a disabled account or a "sign out everywhere" takes effect immediately (as in Lab 15)
    Google      the browser gets an ID token from Google Identity Services; the server verifies its signature against
                Google's public keys, the audience (our client id), the issuer and email_verified
    lockout     too many failed sign-ins for one email are refused for 10 minutes (atomic counter in MongoDB)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
from datetime import timedelta

import jwt
from fastapi import HTTPException, Request
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from . import settings as S
from . import store

EMAIL_RX = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")
_lock = threading.Lock()
_cache: dict = {}


# ---------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def check_password(password: str, stored: str | None) -> bool:
    try:
        algo, salt, dk = (stored or "").split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:                                  # noqa: BLE001
        return False


def password_problem(password: str) -> str | None:
    if len(password or "") < 8:
        return "The password needs at least 8 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "The password needs at least one letter and one number."
    if len(password) > 128:
        return "The password is too long."
    return None


# ---------------------------------------------------------------------------- tokens
def _secret() -> str:
    with _lock:
        if "secret" not in _cache:
            if S.JWT_SECRET:
                _cache["secret"] = S.JWT_SECRET
            else:                                       # generated once, shared by every web process through MongoDB
                doc = store.adb().settings.find_one_and_update(
                    {"_id": "jwt_secret"}, {"$setOnInsert": {"value": secrets.token_urlsafe(48), "created_at": store.now()}},
                    upsert=True, return_document=ReturnDocument.AFTER)
                _cache["secret"] = doc["value"]
        return _cache["secret"]


def make_token(user: dict) -> str:
    n = store.now()
    return jwt.encode({"sub": user["_id"], "sv": user.get("session_version", 0), "iat": n,
                       "exp": n + timedelta(hours=S.TOKEN_HOURS)}, _secret(), algorithm="HS256")


def public(user: dict) -> dict:
    return {"id": user["_id"], "email": user["email"], "name": user.get("name") or user["email"].split("@")[0],
            "role": user.get("role", "user"), "provider": user.get("provider", "password"), "picture": user.get("picture"),
            "created_at": user.get("created_at"), "last_login": user.get("last_login"), "active": user.get("active", True),
            "approved": user.get("approved", True)}


def user_from_request(request: Request) -> dict | None:
    token = request.cookies.get(S.COOKIE)
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        token = h[7:].strip()
    if not token:
        return None
    try:
        claims = jwt.decode(token, _secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    user = store.adb().users.find_one({"_id": claims.get("sub")})
    if not user or not user.get("active", True) or not user.get("approved", True):
        return None
    if user.get("session_version", 0) != claims.get("sv", 0):
        return None
    return user


def current_user(request: Request) -> dict:
    user = user_from_request(request)
    if not user:
        raise HTTPException(401, "Please sign in.")
    return user


def admin_user(request: Request) -> dict:
    user = current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(403, "Only the administrator can do this.")
    return user


# ---------------------------------------------------------------------------- accounts
def _new_id() -> str:
    return "u_" + secrets.token_hex(8)


def ensure_admin(log=print) -> dict:
    """Exactly one administrator, created from ADMIN_EMAIL / ADMIN_PASSWORD (.env). Safe to call on every start."""
    d = store.adb()
    admin = d.users.find_one({"role": "admin"})
    if admin:
        if S.ADMIN_PASSWORD and not check_password(S.ADMIN_PASSWORD, admin.get("password")) and admin.get("password_source") == "env":
            d.users.update_one({"_id": admin["_id"]}, {"$set": {"password": hash_password(S.ADMIN_PASSWORD)}})
            log("[assist] administrator password updated from ADMIN_PASSWORD")
        return admin
    password = S.ADMIN_PASSWORD or secrets.token_urlsafe(9) + "7a"
    doc = {"_id": _new_id(), "email": S.ADMIN_EMAIL, "name": S.ADMIN_NAME, "role": "admin", "provider": "password",
           "password": hash_password(password), "password_source": "env" if S.ADMIN_PASSWORD else "generated",
           "active": True, "approved": True, "session_version": 0, "created_at": store.now(), "last_login": None}
    try:
        d.users.insert_one(doc)
    except DuplicateKeyError:                           # another process created it, or the email is taken by a user
        d.users.update_one({"email": S.ADMIN_EMAIL}, {"$set": {"role": "admin", "active": True, "approved": True}})
        return d.users.find_one({"email": S.ADMIN_EMAIL})
    if S.ADMIN_PASSWORD:
        log(f"[assist] administrator created: {S.ADMIN_EMAIL} (password from ADMIN_PASSWORD in .env)")
    else:
        log(f"[assist] administrator created: {S.ADMIN_EMAIL}  password: {password}   <- shown once; set ADMIN_PASSWORD in .env")
    store.audit("admin.created", actor="system", email=S.ADMIN_EMAIL)
    return doc


def _throttle(key: str, limit: int, seconds: int) -> None:
    from .ratelimit import hit
    r = hit(key, limit, seconds)
    if not r["allowed"]:
        raise HTTPException(429, f"Too many attempts. Try again in {r['retry_after']} seconds.",
                            headers={"Retry-After": str(r["retry_after"])})


def register(email: str, password: str, name: str = "") -> dict:
    email = (email or "").strip().lower()
    if not EMAIL_RX.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    problem = password_problem(password)
    if problem:
        raise HTTPException(422, problem)
    if not store.setting("signup_open", True):
        raise HTTPException(403, "New accounts are closed. Ask the administrator.")
    approved = not store.setting("signup_needs_approval", False)
    doc = {"_id": _new_id(), "email": email, "name": (name or "").strip()[:80] or email.split("@")[0], "role": "user",
           "provider": "password", "password": hash_password(password), "active": True, "approved": approved,
           "session_version": 0, "created_at": store.now(), "last_login": None}
    try:
        store.adb().users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "An account with this email already exists. Sign in instead.") from None
    store.audit("user.registered", actor=email, provider="password", approved=approved)
    return doc


def login(email: str, password: str, ip: str = "") -> dict:
    email = (email or "").strip().lower()
    _throttle(f"login:{email}", S.LOGIN_RATE_PER_10MIN, 600)
    user = store.adb().users.find_one({"email": email})
    if not user or not user.get("password") or not check_password(password or "", user["password"]):
        check_password("x", "scrypt$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")   # same cost for unknown users
        store.audit("login.failed", actor=email, ip=ip)
        raise HTTPException(401, "Wrong email or password.")
    return _finish_login(user, ip)


def _finish_login(user: dict, ip: str) -> dict:
    if not user.get("active", True):
        raise HTTPException(403, "This account is disabled. Ask the administrator.")
    if not user.get("approved", True):
        raise HTTPException(403, "Your account is waiting for the administrator's approval.")
    store.adb().users.update_one({"_id": user["_id"]}, {"$set": {"last_login": store.now()}, "$inc": {"logins": 1}})
    store.audit("login.ok", actor=user["email"], provider=user.get("provider"), ip=ip)
    return user


_jwks = {}


def verify_google(credential: str) -> dict:
    """Verify a Google ID token (RS256) against Google's published keys. Returns its claims."""
    if not S.GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google sign-in is not configured. Set GOOGLE_CLIENT_ID in .env (see the README).")
    try:
        if "client" not in _jwks:
            _jwks["client"] = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs", cache_keys=True, lifespan=3600)
        key = _jwks["client"].get_signing_key_from_jwt(credential)
        claims = jwt.decode(credential, key.key, algorithms=["RS256"], audience=S.GOOGLE_CLIENT_ID,
                            options={"require": ["exp", "iat", "sub", "aud", "iss"]}, leeway=30)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"Google did not confirm this sign-in ({type(exc).__name__}).") from None
    except Exception as exc:                           # noqa: BLE001 - e.g. no internet to fetch Google's keys
        raise HTTPException(503, f"Could not reach Google to verify the sign-in ({type(exc).__name__}).") from None
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(401, "This token was not issued by Google.")
    if not claims.get("email") or not claims.get("email_verified", False):
        raise HTTPException(401, "Google has not verified this email address.")
    return claims


def login_google(credential: str, ip: str = "") -> dict:
    claims = verify_google(credential)
    email = claims["email"].lower()
    d = store.adb()
    user = d.users.find_one({"email": email})
    if user:
        d.users.update_one({"_id": user["_id"]}, {"$set": {"google_sub": claims["sub"], "picture": claims.get("picture"),
                                                           "name": user.get("name") or claims.get("name")}})
        user = d.users.find_one({"_id": user["_id"]})
    else:
        if not store.setting("signup_open", True):
            raise HTTPException(403, "New accounts are closed. Ask the administrator.")
        user = {"_id": _new_id(), "email": email, "name": claims.get("name") or email.split("@")[0], "role": "user",
                "provider": "google", "google_sub": claims["sub"], "picture": claims.get("picture"), "password": None,
                "active": True, "approved": not store.setting("signup_needs_approval", False), "session_version": 0,
                "created_at": store.now(), "last_login": None}
        try:
            d.users.insert_one(user)
        except DuplicateKeyError:
            user = d.users.find_one({"email": email})
        store.audit("user.registered", actor=email, provider="google", approved=user.get("approved", True))
    return _finish_login(user, ip)


def change_password(user: dict, current: str, new: str) -> None:
    if user.get("password") and not check_password(current or "", user["password"]):
        raise HTTPException(401, "The current password is wrong.")
    problem = password_problem(new)
    if problem:
        raise HTTPException(422, problem)
    store.adb().users.update_one({"_id": user["_id"]}, {"$set": {"password": hash_password(new), "password_source": "user"},
                                                        "$inc": {"session_version": 1}})
    store.audit("password.changed", actor=user["email"])

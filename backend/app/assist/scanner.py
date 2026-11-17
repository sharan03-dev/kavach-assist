"""The vulnerability scanner used by the background bot (scanner_bot.py).

Scopes
    project   a folder (SCAN_TARGET, /scan in Docker): vulnerable dependencies in requirements.txt and package.json
              (OSV.dev when online, a bundled advisory list offline, cross-checked against Kavach's exploited list),
              hard-coded secrets, secrets files readable by everyone or missing from .gitignore, debug mode,
              Dockerfile problems (latest tag, running as root)
    system    the Linux system the bot runs on: operating-system packages with a pending (security) update

Every finding says whether it can be fixed automatically. NOTHING is changed by a scan. apply() is only called by the bot
for a job that a signed-in user confirmed in the chat; every changed file is copied to .kavach_backups/<job>/ first, so
rollback() can restore it.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
from pathlib import Path

import httpx

from . import settings as S

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "demo_template"
BACKUPS = ".kavach_backups"
SEV_RANK = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", BACKUPS, "dist", "build"}
TEXT_EXT = {".py", ".js", ".ts", ".json", ".yml", ".yaml", ".ini", ".cfg", ".conf", ".sh", ".toml", ".properties", ".env", ".txt"}
SECRET_RX = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("hard-coded credential", re.compile(r"""(?i)\b([a-z_]*(?:password|passwd|secret|api[_-]?key|token)[a-z_]*)\s*[:=]\s*(['"])([^'"\s]{6,})\2""")),
]
PLACEHOLDER = re.compile(r"(?i)^(changeme|change-me|example|your[-_ ]|xxx+|<.*>|\$\{.*\}|none|null|true|false)")


def _fid(*parts) -> str:
    return "f_" + hashlib.blake2b("|".join(str(p) for p in parts).encode(), digest_size=8).hexdigest()


def _ver(v: str):
    try:
        from packaging.version import Version
        return Version(v)
    except Exception:                                  # noqa: BLE001
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", v)[:4])


def _lt(a: str, b: str) -> bool:
    try:
        return _ver(a) < _ver(b)
    except TypeError:
        return False


def _sev(text: str | None) -> str:
    t = (text or "").upper()
    return {"CRITICAL": "Critical", "HIGH": "High", "MODERATE": "Medium", "MEDIUM": "Medium", "LOW": "Low"}.get(t, "Medium")


# ---------------------------------------------------------------------------- dependencies
def parse_requirements(path: Path) -> list[dict]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        m = re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*==\s*([0-9][A-Za-z0-9.\-+!]*)", line)
        if m:
            out.append({"name": m.group(1).lower().replace("_", "-"), "version": m.group(2), "line": n, "ecosystem": "PyPI"})
    return out


def parse_package_json(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return []
    out = []
    for section in ("dependencies", "devDependencies"):
        for name, spec in (data.get(section) or {}).items():
            m = re.match(r"^[\^~=v]*\s*(\d+\.\d+\.\d+[A-Za-z0-9.\-+]*)$", str(spec).strip())
            if m:
                out.append({"name": name, "version": m.group(1), "section": section, "spec": str(spec), "ecosystem": "npm"})
    return out


_offline = None


def offline_advisories(pkg: dict) -> list[dict]:
    global _offline
    if _offline is None:
        _offline = json.loads((HERE / "advisories.json").read_text(encoding="utf-8"))
    return [{"id": a["id"], "aliases": [a["id"]], "summary": a["summary"], "severity": a["severity"], "fixed": a["below"]}
            for a in _offline if a["ecosystem"] == pkg["ecosystem"] and a["name"] == pkg["name"].lower() and _lt(pkg["version"], a["below"])]


def osv_advisories(pkg: dict, http: httpx.Client) -> list[dict]:
    """Ask OSV.dev which known vulnerabilities affect exactly this package version."""
    r = http.post(f"{S.OSV_URL}/v1/query", json={"version": pkg["version"], "package": {"name": pkg["name"], "ecosystem": pkg["ecosystem"]}})
    r.raise_for_status()
    out = []
    for v in r.json().get("vulns") or []:
        fixed = []
        for a in v.get("affected") or []:
            if (a.get("package") or {}).get("name", "").lower() != pkg["name"].lower():
                continue
            for rng in a.get("ranges") or []:
                if rng.get("type") not in ("ECOSYSTEM", "SEMVER"):
                    continue
                for ev in rng.get("events") or []:
                    if ev.get("fixed") and _lt(pkg["version"], ev["fixed"]):
                        fixed.append(ev["fixed"])
        fixed.sort(key=_ver)
        cves = [a for a in (v.get("aliases") or []) if a.startswith("CVE-")]
        out.append({"id": (cves[0] if cves else v.get("id")), "aliases": [v.get("id")] + (v.get("aliases") or []),
                    "summary": (v.get("summary") or v.get("details") or "")[:220], "severity": _sev((v.get("database_specific") or {}).get("severity")),
                    "fixed": fixed[0] if fixed else None})
    return out


def _same_major(a: str, b: str) -> bool:
    return re.split(r"[.]", a)[0] == re.split(r"[.]", b)[0]


def scan_dependencies(root: Path, exploited: dict, notes: list) -> list[dict]:
    pkgs = []
    for f in _walk(root, names={"requirements.txt", "package.json"}):
        for p in (parse_requirements(f) if f.name == "requirements.txt" else parse_package_json(f)):
            pkgs.append({**p, "file": str(f.relative_to(root))})
    findings, online = [], True
    http = httpx.Client(timeout=8)
    try:
        for p in pkgs:
            adv = None
            if online:
                try:
                    adv = osv_advisories(p, http)
                except Exception as exc:               # noqa: BLE001 - offline: use the bundled list for the rest of the scan
                    online = False
                    notes.append(f"OSV.dev not reachable ({type(exc).__name__}); used the bundled advisory list")
            source = "OSV.dev" if adv is not None else "bundled advisory list"
            if adv is None:
                adv = offline_advisories(p)
            if not adv:
                continue
            fixes = sorted({a["fixed"] for a in adv if a.get("fixed")}, key=_ver)
            target = fixes[-1] if fixes else None      # the smallest version that fixes ALL of them
            ids = sorted({a["id"] for a in adv if a.get("id")})
            hot = [i for i in ids if i in exploited]
            sev = max((a["severity"] for a in adv), key=lambda s: SEV_RANK.get(s, 2))
            if hot and SEV_RANK[sev] < 4:
                sev = "Critical" if exploited[hot[0]] == "Active" else "High" if SEV_RANK[sev] < 3 else sev
            major = bool(target) and not _same_major(p["version"], target)
            findings.append({
                "_id": _fid("project", p["file"], "dependency", p["name"]), "scope": "project", "kind": "dependency", "file": p["file"],
                "title": f"{p['name']} {p['version']} has {len(ids)} known vulnerabilit{'y' if len(ids) == 1 else 'ies'}",
                "detail": "; ".join(f"{a['id']}: {a['summary']}" for a in adv[:4]) + (f" (+{len(adv) - 4} more)" if len(adv) > 4 else ""),
                "severity": sev, "cves": ids[:12], "exploited": hot, "package": p["name"], "ecosystem": p["ecosystem"], "current": p["version"],
                "fixed_version": target, "source": source, "fixable": bool(target),
                "fix": ({"type": "bump", "file": p["file"], "package": p["name"], "ecosystem": p["ecosystem"], "from": p["version"], "to": target}
                        if target else None),
                "fix_text": (f"change {p['name']} {p['version']} -> {target} in {p['file']}" + (" (major version change: test the application)" if major else ""))
                            if target else None,
                "manual": None if target else "No fixed version is published yet: remove the package, replace it, or limit its exposure.",
            })
    finally:
        http.close()
    return findings


# ---------------------------------------------------------------------------- secrets, permissions, configuration
def _walk(root: Path, names: set | None = None):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if names is None or fn in names:
                yield p


def _mask(value: str) -> str:
    return value[:2] + "*" * max(4, min(len(value) - 2, 10))


def scan_files(root: Path) -> list[dict]:
    out = []
    gitignore = root / ".gitignore"
    ignored = gitignore.read_text(encoding="utf-8", errors="replace").splitlines() if gitignore.exists() else []
    for f in _walk(root):
        rel = str(f.relative_to(root))
        is_env = f.name == ".env" or f.name.startswith(".env.") and not f.name.endswith(".example")
        try:
            st = f.stat()
        except OSError:
            continue
        if is_env or f.suffix in (".pem", ".key"):
            if stat.S_IMODE(st.st_mode) & 0o077:
                out.append({"_id": _fid("project", rel, "permission"), "scope": "project", "kind": "permission", "file": rel,
                            "title": f"{rel} can be read by other users (mode {stat.S_IMODE(st.st_mode):o})",
                            "detail": "A secrets file should be readable by its owner only.", "severity": "Medium", "fixable": True,
                            "fix": {"type": "chmod", "file": rel, "mode": "600"}, "fix_text": f"chmod 600 {rel}"})
            if is_env and not any(line.strip() in (".env", "*.env", ".env*", rel) for line in ignored):
                out.append({"_id": _fid("project", rel, "gitignore"), "scope": "project", "kind": "config", "file": ".gitignore",
                            "title": f"{rel} is not listed in .gitignore", "detail": "Secrets files must never be committed to git.",
                            "severity": "Medium", "fixable": True, "fix": {"type": "gitignore", "file": ".gitignore", "entry": ".env"},
                            "fix_text": "add .env to .gitignore"})
            continue
        if f.name == "Dockerfile" or f.name.startswith("Dockerfile."):
            text = f.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"(?im)^FROM\s+(\S+)", text)
            if m and (":" not in m.group(1).split("/")[-1] or m.group(1).endswith(":latest")):
                out.append({"_id": _fid("project", rel, "docker-tag"), "scope": "project", "kind": "config", "file": rel,
                            "title": f"{rel} builds from an unpinned image ({m.group(1)})", "severity": "Low", "fixable": False,
                            "detail": "The 'latest' tag changes without notice, so builds are not repeatable and can pull an unpatched or broken image.",
                            "manual": "Pin an explicit, supported tag (for example python:3.12-slim) and rebuild regularly to pick up security fixes."})
            if not re.search(r"(?im)^USER\s+(?!root\b|0\b)\S+", text):
                out.append({"_id": _fid("project", rel, "docker-root"), "scope": "project", "kind": "config", "file": rel,
                            "title": f"{rel} runs the application as root", "severity": "Medium", "fixable": False,
                            "detail": "A bug in the application then gives an attacker root inside the container.",
                            "manual": "Add a non-root user: `RUN useradd -m app` and `USER app` before CMD."})
            continue
        if f.suffix not in TEXT_EXT or st.st_size > 300_000 or f.name in ("package-lock.json", "requirements.txt", "package.json"):
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            for label, rx in SECRET_RX:
                m = rx.search(line)
                if not m:
                    continue
                if label == "hard-coded credential":
                    if PLACEHOLDER.match(m.group(3)) or "getenv" in line or "environ" in line:
                        continue
                    shown = f"{m.group(1)} = {_mask(m.group(3))}"
                else:
                    shown = label
                out.append({"_id": _fid("project", rel, "secret", label, hashlib.md5(line.encode()).hexdigest()[:8]), "scope": "project",
                            "kind": "secret", "file": rel, "line": n, "title": f"{label} in {rel} line {n}", "detail": shown,
                            "severity": "High", "fixable": False,
                            "manual": "Move the value to an environment variable (.env, not committed) and read it with os.getenv(); then CHANGE the "
                                      "secret, because anyone who saw the code already knows the old one."})
                break
            m = re.search(r"\bDEBUG\s*=\s*True\b", line) or re.search(r"\.run\([^)]*\bdebug\s*=\s*True", line)
            if m and f.suffix == ".py":
                new = re.sub(r"(\bDEBUG\s*=\s*)True\b", r"\1False", line)
                new = re.sub(r"(\bdebug\s*=\s*)True", r"\1False", new)
                out.append({"_id": _fid("project", rel, "debug", n), "scope": "project", "kind": "config", "file": rel, "line": n,
                            "title": f"debug mode is switched on in {rel} line {n}", "severity": "High", "fixable": True,
                            "detail": "Debug mode shows stack traces and (in Flask) an interactive console that runs code for anyone who reaches it.",
                            "fix": {"type": "replace", "file": rel, "line": n, "from": line, "to": new}, "fix_text": f"set debug to False in {rel} line {n}"})
    return out


# ---------------------------------------------------------------------------- system (the Linux host of the bot)
def scan_system(notes: list) -> list[dict]:
    if platform.system() != "Linux" or not shutil.which("apt-get"):
        notes.append("system scan skipped: not a Debian-based Linux system")
        return []
    try:
        subprocess.run(["apt-get", "update", "-qq"], capture_output=True, timeout=90, check=False)
        r = subprocess.run(["apt-get", "-s", "-o", "Debug::NoLocking=true", "upgrade"], capture_output=True, text=True, timeout=60, check=False)
    except (subprocess.SubprocessError, OSError) as exc:
        notes.append(f"system scan skipped: {type(exc).__name__}")
        return []
    out = []
    for m in re.finditer(r"^Inst (\S+) \[([^\]]+)\] \((\S+) ([^)]*)\)", r.stdout, re.M):
        name, old, new, origin = m.groups()
        security = "security" in origin.lower()
        out.append({"_id": _fid("system", "os-package", name), "scope": "system", "kind": "os-package", "file": "(operating system)",
                    "title": f"OS package {name} {old} has a{' security' if security else 'n'} update ({new})",
                    "detail": f"pending update from {origin.strip()}", "severity": "High" if security else "Low", "package": name, "current": old,
                    "fixed_version": new, "fixable": True, "fix": {"type": "apt", "package": name}, "fix_text": f"apt-get install --only-upgrade {name}"})
    out.sort(key=lambda f: -SEV_RANK[f["severity"]])
    return out[:40]


def scan(target: str | None = None, exploited: dict | None = None, system: bool | None = None) -> dict:
    """Run every check. Returns {findings, notes, target, platform}. Read-only."""
    root = Path(target or S.SCAN_TARGET)
    notes: list = []
    findings: list = []
    if root.is_dir():
        findings += scan_dependencies(root, exploited or {}, notes)
        findings += scan_files(root)
    else:
        notes.append(f"project folder {root} does not exist")
    if S.SCAN_SYSTEM if system is None else system:
        findings += scan_system(notes)
    for f in findings:
        f["severity_rank"] = SEV_RANK.get(f["severity"], 2)
        f["platform"] = platform.system() or "Linux"
    findings.sort(key=lambda f: (-f["severity_rank"], f["scope"], f["file"], f["title"]))
    return {"findings": findings, "notes": notes, "target": str(root), "platform": f"{platform.system()} {platform.release()}"}


# ---------------------------------------------------------------------------- fixing (only after confirmation in chat)
def _backup(root: Path, rel: str, job_id: str) -> str:
    dst = root / BACKUPS / job_id / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():                               # keep the ORIGINAL when one job touches a file twice
        shutil.copy2(root / rel, dst)
    return str(dst.relative_to(root))


def _safe(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if root.resolve() not in p.parents and p != root.resolve():
        raise ValueError("path outside the scanned folder")
    return p


def apply(finding: dict, job_id: str, target: str | None = None) -> dict:
    """Apply the fix of ONE finding. Returns {ok, message, backup}."""
    root = Path(target or S.SCAN_TARGET)
    fix = finding.get("fix") or {}
    try:
        if fix.get("type") == "apt":
            if not re.fullmatch(r"[a-z0-9][a-z0-9+.\-]*(:[a-z0-9]+)?", fix["package"]):
                raise ValueError("unexpected package name")
            r = subprocess.run(["apt-get", "install", "-y", "--only-upgrade", "-qq", fix["package"]], capture_output=True, text=True, timeout=300,
                               check=False, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
            if r.returncode != 0:
                return {"ok": False, "message": (r.stderr or r.stdout).strip()[-200:] or f"apt-get exit code {r.returncode}"}
            return {"ok": True, "message": f"upgraded OS package {fix['package']}", "backup": None}
        path = _safe(root, fix["file"])
        if fix.get("type") == "gitignore":
            if path.exists():
                bak = _backup(root, fix["file"], job_id)
                text = path.read_text(encoding="utf-8", errors="replace")
            else:
                bak, text = "(new file)", ""
                (root / BACKUPS / job_id).mkdir(parents=True, exist_ok=True)
                (root / BACKUPS / job_id / (fix["file"] + ".created")).write_text("", encoding="utf-8")
            path.write_text(text + ("" if text.endswith("\n") or not text else "\n") + fix["entry"] + "\n", encoding="utf-8")
            return {"ok": True, "message": f"added {fix['entry']} to {fix['file']}", "backup": bak}
        bak = _backup(root, fix["file"], job_id)
        if fix["type"] == "chmod":
            os.chmod(path, int(fix["mode"], 8))
            if stat.S_IMODE(path.stat().st_mode) & 0o077:
                return {"ok": False, "message": "the file system did not accept the new permissions", "backup": bak}
            return {"ok": True, "message": f"permissions of {fix['file']} set to {fix['mode']}", "backup": bak}
        text = path.read_text(encoding="utf-8", errors="replace")
        if fix["type"] == "bump" and fix["ecosystem"] == "PyPI":
            rx = re.compile(rf"(?im)^(\s*{re.escape(fix['package'])}\s*(?:\[[^\]]*\])?\s*==\s*){re.escape(fix['from'])}\b".replace(r"\-", "[-_]"))
            new, n = rx.subn(rf"\g<1>{fix['to']}", text)
        elif fix["type"] == "bump":
            rx = re.compile(rf'("{re.escape(fix["package"])}"\s*:\s*"[\^~=v]*){re.escape(fix["from"])}(")')
            new, n = rx.subn(rf"\g<1>{fix['to']}\g<2>", text)
        elif fix["type"] == "replace":
            lines = text.splitlines(keepends=True)
            i = fix["line"] - 1
            if i < len(lines) and lines[i].rstrip("\r\n") == fix["from"]:
                lines[i] = fix["to"] + lines[i][len(lines[i].rstrip("\r\n")):]
                new, n = "".join(lines), 1
            else:
                new, n = text, 0
        else:
            return {"ok": False, "message": f"unknown fix type {fix.get('type')}"}
        if n == 0:
            return {"ok": False, "message": "the file changed since the scan - scan again", "backup": bak}
        path.write_text(new, encoding="utf-8")
        return {"ok": True, "message": finding.get("fix_text") or "fixed", "backup": bak}
    except Exception as exc:                           # noqa: BLE001
        return {"ok": False, "message": f"{type(exc).__name__}: {str(exc)[:160]}"}


def rollback(job_id: str, target: str | None = None) -> dict:
    """Restore every file a fix job changed from its backup."""
    root = Path(target or S.SCAN_TARGET)
    src = root / BACKUPS / job_id
    if not src.is_dir():
        return {"ok": False, "restored": [], "message": "no backup found for that fix"}
    restored = []
    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = str(f.relative_to(src))
        if rel.endswith(".created"):
            (root / rel[:-len(".created")]).unlink(missing_ok=True)
            restored.append(rel[:-len(".created")] + " (removed)")
            continue
        shutil.copy2(f, root / rel)
        restored.append(rel)
    return {"ok": True, "restored": restored, "message": f"restored {len(restored)} file(s)"}


def seed_demo(target: str | None = None, force: bool = False) -> bool:
    """Copy the demo project into an empty scan folder (or reset it). dot_env -> .env, dot_gitignore -> .gitignore."""
    root = Path(target or S.SCAN_TARGET)
    root.mkdir(parents=True, exist_ok=True)
    if not force and any(p.name not in (".gitkeep", BACKUPS) for p in root.iterdir()):
        return False
    for f in TEMPLATE.iterdir():
        name = {"dot_env": ".env", "dot_gitignore": ".gitignore"}.get(f.name, f.name)
        shutil.copyfile(f, root / name)
        os.chmod(root / name, 0o644)
    return True

#!/usr/bin/env python3
"""Offline security scan of the source tree (no network, no installs). It looks for committed secrets, risky code patterns and unsafe configuration
templates. It is a screening aid, NOT a vulnerability audit: it cannot see known-CVE dependencies (run `pip-audit` and `npm audit` on a machine with
network access; see docs/phase11/SECURITY_REVIEW.md) and it does not prove the absence of defects.

    python scripts/security_scan.py [--json OUT] [--root DIR]      exit 1 when a HIGH finding exists

Findings are suppressed only through the reviewed ALLOW list below, each entry with a reason."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", ".venv", "venv", "data"}
TEXT_EXT = {".py", ".js", ".jsx", ".json", ".md", ".txt", ".sql", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".example", ".html", ".css", ".sh", ".env"}

SECRET_RULES = [
    ("private key block", "HIGH", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("AWS access key id", "HIGH", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", "HIGH", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Google API key", "HIGH", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Slack token", "HIGH", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("OpenAI-style key", "HIGH", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("hard-coded credential assignment", "MEDIUM", re.compile(r"""(?i)\b(?:password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*["'][^"'\s]{12,}["']""")),
]
CODE_RULES = [  # (name, severity, pattern, applies to suffixes)
    ("eval()", "HIGH", re.compile(r"(?<![\w.])eval\("), {".py", ".js", ".jsx"}),
    ("exec()", "HIGH", re.compile(r"(?<![\w.])exec\("), {".py"}),
    ("pickle load", "HIGH", re.compile(r"\bpickle\.loads?\("), {".py"}),
    ("yaml.load without SafeLoader", "HIGH", re.compile(r"\byaml\.load\((?![^)]*Safe)"), {".py"}),
    ("shell=True", "HIGH", re.compile(r"shell\s*=\s*True"), {".py"}),
    ("os.system / os.popen", "HIGH", re.compile(r"\bos\.(?:system|popen)\("), {".py"}),
    ("TLS verification disabled", "HIGH", re.compile(r"verify\s*=\s*False|CERT_NONE|_create_unverified_context"), {".py"}),
    ("dangerouslySetInnerHTML / innerHTML", "HIGH", re.compile(r"dangerouslySetInnerHTML|\.innerHTML\s*="), {".js", ".jsx"}),
    ("token kept in browser storage", "MEDIUM", re.compile(r"(?:localStorage|sessionStorage)\.setItem\([^)]*(?:token|session|password)", re.I), {".js", ".jsx"}),
    ("random module for secrets", "MEDIUM", re.compile(r"\brandom\.(?:random|randint|choice|choices)\(.*(?:token|secret|password|nonce)", re.I), {".py"}),
    ("weak hash (md5/sha1)", "LOW", re.compile(r"hashlib\.(?:md5|sha1)\("), {".py"}),
    ("bare except-pass", "LOW", re.compile(r"except\s*(?:Exception)?\s*:\s*\n\s*pass\b"), {".py"}),
    ("debug enabled", "MEDIUM", re.compile(r"\bdebug\s*=\s*True\b"), {".py"}),
]
# Reviewed exceptions: (rule name, path fragment, reason). Every entry needs a real reason.
ALLOW = [
    ("exec()", "app/modules/devspace/", "Dev Workspace runs the person's own code inside its isolation layer; gated by Special access and feature flags (docs/phase9/SECURITY.md)"),
    ("exec()", "tests/", "tests"), ("eval()", "tests/", "tests"), ("shell=True", "tests/", "tests"),
    ("hard-coded credential assignment", "tests/", "test fixtures use throw-away passwords"),
    ("eval()", "app/modules/devspace/_devboot.py", "notebook kernel: evaluates the person's own cell inside the isolated runner, never server-side input"),
    ("private key block", "tests/", "fixture that proves the Git secret scanner rejects a private key"),
    ("weak hash (md5/sha1)", "research_intelligence/studio/generators.py", "stable short id for a generated item, not a security control"),
    ("weak hash (md5/sha1)", "workspace/profiling.py", "row-duplicate fingerprint, not a security control"),
]
CONFIG_CHECKS = [  # (file, pattern that must NOT appear, message, severity)
    ("config/env.production.example", re.compile(r"(?im)^\s*PREDICTAI_DEBUG\s*=\s*(?:1|true|yes)"), "production template enables debug", "HIGH"),
    ("config/env.production.example", re.compile(r"(?im)^\s*PREDICTAI_CORS[A-Z_]*\s*=\s*\*"), "production template allows every CORS origin", "HIGH"),
    ("config/env.production.example", re.compile(r"(?im)^\s*PREDICTAI_COOKIE_SECURE\s*=\s*(?:0|false|no)"), "production template turns off secure cookies", "HIGH"),
    ("config/env.production.example", re.compile(r"(?im)^\s*PREDICTAI_ENV\s*=\s*(?:dev|development|test)"), "production template is set to a non-production environment", "MEDIUM"),
]
FORBIDDEN_FILES = (".env", "*.pem", "*.key", "*.p12", "id_rsa", "*.sqlite", "*.sqlite3", "*.db", "dev-secret-key")


def iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            yield p


def allowed(rule: str, rel: str) -> str:
    for r, frag, why in ALLOW:
        if r == rule and frag in rel:
            return why
    return ""


def scan(root: Path) -> Dict[str, object]:
    findings: List[Dict[str, str]] = []
    suppressed: List[Dict[str, str]] = []
    import fnmatch
    for p in iter_files(root):
        rel = p.relative_to(root).as_posix()
        if rel in ("scripts/security_scan.py", "backend/tests/test_p11_security_scan.py"):          # the scanner and its self-test hold the patterns on purpose
            continue
        if any(fnmatch.fnmatch(p.name, pat) for pat in FORBIDDEN_FILES) and not p.name.endswith(".example"):
            findings.append({"severity": "HIGH", "rule": "file that should never be committed", "where": rel, "detail": p.name})
            continue
        if p.suffix not in TEXT_EXT and p.name not in {".env.example"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        is_example = p.name.endswith(".example") or "/docs/" in "/" + rel
        for name, sev, rx in SECRET_RULES:
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                f = {"severity": sev, "rule": name, "where": f"{rel}:{line}", "detail": "(value not shown)"}
                why = allowed(name, rel)
                if is_example and name == "hard-coded credential assignment":
                    why = why or "documentation or template placeholder"
                (suppressed if why else findings).append({**f, "reason": why} if why else f)
        for name, sev, rx, exts in CODE_RULES:
            if p.suffix not in exts:
                continue
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                f = {"severity": sev, "rule": name, "where": f"{rel}:{line}", "detail": text.splitlines()[line - 1].strip()[:120]}
                why = allowed(name, rel)
                (suppressed if why else findings).append({**f, "reason": why} if why else f)
    for rel, rx, msg, sev in CONFIG_CHECKS:
        p = root / rel
        if p.is_file() and rx.search(p.read_text(encoding="utf-8", errors="ignore")):
            findings.append({"severity": sev, "rule": "unsafe configuration template", "where": rel, "detail": msg})
    unpinned = []
    for rq in sorted((root / "backend").glob("requirements*.txt")):
        for ln in rq.read_text().splitlines():
            ln = ln.split("#")[0].strip()
            if ln and not ln.startswith("-") and not re.search(r"(==|<|~=)", ln):
                unpinned.append(f"{rq.name}: {ln}")
    return {"findings": findings, "suppressed": suppressed, "unbounded_dependencies": unpinned,
            "not_covered": ["known-CVE dependency check (needs network: pip-audit, npm audit)", "runtime/DAST testing", "container and OS hardening", "secret history in git (this tree is not a git checkout)"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json"); ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    res = scan(Path(a.root))
    by = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in res["findings"]:
        by[f["severity"]] += 1
        print(f"{f['severity']:6} {f['rule']:38} {f['where']}  {f['detail']}")
    print(f"\n{by['HIGH']} high, {by['MEDIUM']} medium, {by['LOW']} low; {len(res['suppressed'])} reviewed exception(s); {len(res['unbounded_dependencies'])} dependency line(s) without an upper bound")
    print("Not covered:", "; ".join(res["not_covered"]))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=2))
    return 1 if by["HIGH"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

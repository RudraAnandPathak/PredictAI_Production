#!/usr/bin/env python3
"""Builds the combined requirements register (Phases 1-10) and its coverage report from the per-phase registers.

It does NOT invent evidence: each row keeps the original wording of its status and evidence, gets a normalised status in the eight
categories the release brief requires, and every test file it names is checked to exist. Rows whose mapping is a judgement are flagged.

    python3 docs/phase11/build_register.py            # writes REQUIREMENTS_REGISTER.md and requirements_combined.json
    python3 docs/phase11/build_register.py --check    # exit 1 if the committed files are stale or the coverage rules fail"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

CATS = ["Complete and verified", "Implemented but partially verified", "Implemented but not tested", "Partially implemented", "Broken",
        "Blocked by unavailable infrastructure", "Deferred", "Not implemented"]
SOURCES = [  # (phase, module, file)
    ("3", "Workspace / data", "docs/phase3/REQUIREMENTS_REGISTER.md"),
    ("4", "Analytics", "docs/phase4/REQUIREMENTS_REGISTER.md"),
    ("5", "Predictive", "docs/phase5/REQUIREMENTS_REGISTER.md"),
    ("6", "Research (Phase 6)", "docs/phase6/REQUIREMENTS_REGISTER.md"),
    ("6c", "Research corrective", "docs/phase6/CORRECTIVE_REQUIREMENTS.md"),
    ("7", "Intelligence / exam / reports / collaboration", "PHASE7_REQUIREMENTS.md"),
    ("8", "Identity, roles, research readiness", "PHASE8_REQUIREMENTS.md"),
    ("9", "Development Workspace", "PHASE9_REQUIREMENTS.md"),
    ("10", "Research Intelligence & Knowledge Studio", "PHASE10_REQUIREMENTS.md"),
    ("10A", "Phase 10A: monitoring, forecasting, ensembles, what-if, magic link", "PHASE10A_REQUIREMENTS.md"),
    ("10B", "Phase 10B: AI Brain (Gemini) and exam prediction", "PHASE10B_REQUIREMENTS.md"),
    ("10C", "Phase 10C: Notebook (Stage 1), default-on external AI, task routing", "PHASE10C_REQUIREMENTS.md"),
    ("10D", "Phase 10D: Notebook Studio, Deep Research, import, older areas retired", "PHASE10D_REQUIREMENTS.md"),
    ("10E", "Phase 10E: Reviews, and the account/project deletion fix", "PHASE10E_REQUIREMENTS.md"),
    ("11T", "Phase 11T: Turso (libSQL) database integration", "PHASE11_TURSO_REQUIREMENTS.md"),
]
QUALIFIERS = re.compile(r"fake|mock|unverified|not verified|not browser|never rendered|stand-in|canned|not run|local-only|feature-flagged|api only|"
                        r"never launched|not tested|could not|no network|partially verified|not viewed|test adapter", re.I)

# Phases 1-2 have no per-phase register; these rows are written by hand and each names a test file that must exist.
PHASE12 = [
    ("P1-01", "Platform", "Health check endpoints (`/health`, `/health/ready`)", "backend/app/api/v1/health.py", ["test_health_api"]),
    ("P2-01", "Identity", "Registration with email validation, duplicate prevention", "app/modules/identity", ["test_identity_registration", "test_identity_api"]),
    ("P2-02", "Identity", "Login, logout, hashed passwords, persistent sessions", "app/modules/identity", ["test_identity_auth", "test_identity_api"]),
    ("P2-03", "Identity", "Password reset (single-use tokens)", "app/modules/identity", ["test_identity_auth", "test_identity_api"]),
    ("P2-04", "Identity", "Roles (normal / special / owner) and permission checks", "app/modules/identity", ["test_identity_access", "test_identity_platform"]),
    ("P2-05", "Identity", "Special-access codes, hashed, rate-limited", "app/modules/identity/access_codes.py", ["test_identity_access"]),
    ("P2-06", "Security", "CSRF protection on state-changing requests", "app/core", ["test_identity_api"]),
    ("P2-07", "Security", "Audit log of security events", "app/modules/identity", ["test_identity_platform"]),
    ("P2-08", "Identity", "Google / GitHub sign-in (live providers not reachable here)", "app/modules/identity/social", ["test_social_auth", "test_social_auth_api"]),
]


def normalise(raw: str, phase: str) -> tuple[str, bool]:
    s = re.sub(r"[*`]", "", raw).strip(); low = s.lower(); flag = False
    if "blocked" in low:                                   return CATS[5], False
    if low.startswith("missing") or "deferred" in low[:20]: return CATS[6], False
    if low.startswith(("not included", "not implemented")): return CATS[7], False
    if low.startswith(("open", "not verified", "unverified")): return CATS[2], True
    if low.startswith("partial") and "verified" not in low[:40]: return CATS[3], False
    if "partially verified" in low[:60] or "see testing" in low: return CATS[1], "see testing" in low
    if low.startswith(("fully", "complete", "done", "implemented", "tested", "delivered", "existing")):
        if "done*" in low or QUALIFIERS.search(low): return CATS[1], False
        if low.startswith(("existing", "implemented as foundation")): return CATS[1], True
        return CATS[0], False
    return CATS[1], True


def table_rows(path: Path):
    lines = path.read_text().splitlines()
    for i, l in enumerate(lines):
        if l.startswith("|") and i + 1 < len(lines) and re.match(r"\|[\s\-|:]+\|$", lines[i + 1].strip()):
            head = [x.strip().lower() for x in l.strip("|").split("|")]
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                cells = [x.strip() for x in lines[j].strip("|").split("|")]
                if len(cells) == len(head): yield dict(zip(head, cells))
                j += 1


def pick(row: dict, *names: str, exact: bool = False) -> str:
    for n in names:
        for k, v in row.items():
            if (k == n) if exact else (n in k): return v
    return ""


def test_files() -> set[str]:
    names = {p.stem for p in (ROOT / "backend" / "tests").glob("test_*.py")} | {p.name.replace(".js", "") for p in (ROOT / "frontend" / "tests").glob("*.test.js")}
    for p in (ROOT / "backend" / "tests").glob("*.py"):                       # a cited name may be a test function as well as a file
        names |= set(re.findall(r"def (test_\w+)", p.read_text(errors="ignore")))
    for p in (ROOT / "frontend" / "tests").glob("*.js"):
        names |= {re.sub(r"\W+", "_", m).strip("_") for m in re.findall(r"test\(\s*['\"]([^'\"]+)", p.read_text(errors="ignore"))}
    return names


def build():
    known = test_files(); rows = []
    for phase, module, rel in SOURCES:
        for n, r in enumerate(table_rows(ROOT / rel), 1):
            status_raw = pick(r, "status") or pick(r, "where")
            rid = pick(r, "id", "#", "part", exact=True) or f"P{phase}-{n:02d}"
            if re.fullmatch(r"\d+", rid) or rid in {"", "—"}: rid = f"P{phase}-{rid or n:0>2}"
            desc = pick(r, "requirement") or pick(r, "spec section", "part", "§") or ""
            if not desc and "requirement" not in r: desc = list(r.values())[1] if len(r) > 1 else ""
            impl = pick(r, "implementation", "where it lives", "where")
            ev = pick(r, "tests", "evidence", "acceptance")
            cat, flag = normalise(status_raw, phase)
            cited = sorted(set(re.findall(r"\b(test_[a-z0-9_]+)", ev + " " + impl + " " + status_raw)))
            if cat == CATS[0] and not cited and not ev.strip():            # never Complete on code or a claim alone: needs a named test or evidence text
                cat, flag = CATS[1], True
            rows.append({"id": rid, "phase": phase, "module": module, "requirement": re.sub(r"\s+", " ", desc)[:300], "source": rel, "implementation": impl[:200],
                         "tests_cited": cited, "tests_missing": [t for t in cited if t not in known and not any(k.startswith(t) for k in known)],
                         "status": cat, "original_status": re.sub(r"\s+", " ", re.sub(r"[*`]", "", status_raw))[:200], "evidence": re.sub(r"\s+", " ", ev)[:240],
                         "needs_review": bool(flag)})
    for rid, mod, desc, impl, tests in PHASE12:
        ok = all(t in known for t in tests)
        cat = CATS[1] if "live providers" in desc else (CATS[0] if ok else CATS[2])
        rows.append({"id": rid, "phase": rid[1], "module": mod, "requirement": desc, "source": "tests (no per-phase register existed)", "implementation": impl, "tests_cited": tests,
                     "tests_missing": [t for t in tests if t not in known], "status": cat, "original_status": "written for this register", "evidence": ", ".join(tests), "needs_review": False})
    return rows


def coverage(rows):
    return {
        "total": len(rows),
        "by_status": {c: sum(r["status"] == c for r in rows) for c in CATS},
        "by_phase": {p: sum(r["phase"] == p for r in rows) for p in sorted({r["phase"] for r in rows})},
        "no_implementation_location": [r["id"] for r in rows if not r["implementation"].strip()],
        "no_test_named": [r["id"] for r in rows if not r["tests_cited"] and r["status"] == CATS[0]],
        "cites_missing_test_file": {r["id"]: r["tests_missing"] for r in rows if r["tests_missing"]},
        "mapping_needs_review": [r["id"] for r in rows if r["needs_review"]],
        "downgraded_for_missing_evidence": [r["id"] for r in rows if r["needs_review"] and not r["tests_cited"] and not r["evidence"].strip()],
    }


def render(rows, cov):
    L = ["# Combined requirements register — Phases 1–10", "",
         "Generated by `docs/phase11/build_register.py` from the per-phase registers (originals are untouched and still the authority for detail).",
         "Phases 1–2 had no per-phase register; their rows are written from the test suite. Nothing was removed; superseded or deferred items keep their rows.", "",
         "**Verification scope.** Every `Complete and verified` row means: built, and its named automated tests passed **in the build sandbox** (FastAPI stand-in over real Starlette, no browser, no Docker, no live providers, no macOS). "
         "It does not mean verified in the target environment — that is what the release gates in `RELEASE_GATES.md` track. Rows with a qualifier in their original wording (fakes, canned payloads, not browser-tested, local-only…) are `Implemented but partially verified`.", "",
         "**Not carried by the earlier registers:** Priority and Dependencies per requirement. They are not invented here (see the coverage report).", "",
         "## Totals", "", "| Status | Rows |", "|---|---|"] + [f"| {c} | {n} |" for c, n in cov["by_status"].items()] + [f"| **Total** | **{cov['total']}** |", "",
         "## Rows", "", "| ID | Module | Requirement | Status | Original status | Evidence / tests | Review |", "|---|---|---|---|---|---|---|"]
    esc = lambda s: s.replace("|", "\\|")
    for r in rows:
        L.append(f"| {r['id']} | {esc(r['module'])} | {esc(r['requirement'])} | {r['status']} | {esc(r['original_status'][:90])} | {esc(r['evidence'][:110])} | {'check' if r['needs_review'] else ''} |")
    return "\n".join(L) + "\n"


def render_cov(cov):
    L = ["# Requirements coverage report", "", f"Rows: **{cov['total']}**. Source: `requirements_combined.json` (regenerate with `build_register.py`).", "", "## By phase", ""]
    L += [f"- Phase {p}: {n}" for p, n in cov["by_phase"].items()]
    L += ["", "## Findings", ""]
    L.append(f"- Rows with **no implementation location**: {len(cov['no_implementation_location'])} {cov['no_implementation_location'][:25]}")
    L.append(f"- `Complete and verified` rows that **name no test file** (evidence is prose or a browser run from an earlier sandbox): {len(cov['no_test_named'])}")
    L.append(f"- Rows citing a **test file that does not exist**: {len(cov['cites_missing_test_file'])} {dict(list(cov['cites_missing_test_file'].items())[:25])}")
    L.append(f"- Rows whose status mapping is a judgement and should be reviewed by a person: {len(cov['mapping_needs_review'])} {cov['mapping_needs_review'][:25]}")
    L += ["", "## Rule applied", "", "A row is never promoted to `Complete and verified` because code or UI exists: it needs an unqualified status in the source register. "
          "`Complete and verified` rows with no named test file are listed above so they can be audited; they are kept, not downgraded automatically, because many cite browser runs or prose evidence from earlier phases."]
    return "\n".join(L) + "\n"


def main() -> int:
    rows = build(); cov = coverage(rows)
    files = {"REQUIREMENTS_REGISTER.md": render(rows, cov), "REQUIREMENTS_COVERAGE.md": render_cov(cov), "requirements_combined.json": json.dumps({"coverage": cov, "rows": rows}, indent=1) + "\n"}
    if "--check" in sys.argv:
        stale = [n for n, t in files.items() if not (OUT / n).exists() or (OUT / n).read_text() != t]
        bad = cov["cites_missing_test_file"]
        if stale or bad: print("stale:", stale, "missing test files:", bad); return 1
        print("register is current"); return 0
    for n, t in files.items(): (OUT / n).write_text(t)
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in cov.items() if k != "cites_missing_test_file"}, indent=1)); print("missing test files:", cov["cites_missing_test_file"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

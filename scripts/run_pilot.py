#!/usr/bin/env python3
"""Runs the pilot scenarios A-H as they exist TODAY: each scenario is a chain of automated end-to-end tests (HTTP through the real application stack
on a fresh database, or the service layer where the HTTP flow needs live services). Results go to docs/phase11/PILOT_RESULTS.md and .json.

What this is NOT: a pilot with people, a live-provider test, a browser test, or a container-isolation test. Each step records its layer and
what is mocked, so a green line here is never read as more than it is.

    cd backend && PYTHONPATH=<fastapi-or-standin>:.:tests python3 ../scripts/run_pilot.py"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend" / "tests")]

H, S = "HTTP, real app stack, fresh SQLite", "service layer, real SQLite"
# (scenario, title, role, [(test id or module, layer, mocked/external note)], status override or None, note)
SCENARIOS = [
    ("A", "Document intelligence", "standard user", [
        ("test_p10_api.Flow.test_upload_process_search_ask_cite_and_delete_over_http", H, "no AI model: extractive answers; citations verified as exact quotes"),
        ("test_p10_reports.Building", S, "report from a finished analysis"),
        ("test_p10_reports.Exports", S, "PDF/DOCX/PPTX/MD read back"),
    ], None, "Scanned PDFs (OCR) are not supported; no model-written answers were exercised."),
    ("B", "Data analytics", "standard user", [
        ("test_analytics_api.AnalyticsApiTests.test_schema_run_get_rows_export_over_http", H, "synthetic CSV"),
        ("test_p7_reports", S, "dataset/prediction/exam/research to report, versions, citations"),
    ], None, "XLSX path is covered by parser tests, not by this scenario; charts are data-only (never rendered)."),
    ("C", "Predictive modelling", "standard user", [
        ("test_predictive_api.Workflow", H, "in-process training of a synthetic dataset"),
        ("test_p7_api.Phase7Api.test_lifecycle_needs_confirmation_and_prediction_names_its_version", H, ""),
        ("test_p7_api.Phase7Api.test_model_to_report_export_and_download", H, ""),
    ], None, "Large-data training times not measured here."),
    ("D", "Research", "standard user", [
        ("test_research_api.ResearchApi.test_a_research_session_from_question_to_evidence_to_export_and_deletion", H, "MOCKED web/Wikipedia/arXiv providers"),
        ("test_research_intelligence", S, "includes the Louis XVI historical question with canned sources"),
        ("test_p10_reasoning", S, "multi-source analysis; quotes exact"),
    ], None, "NOT a live-provider test: Wikipedia, arXiv, SearXNG and the AI providers were never reached."),
    ("E", "Exam Intelligence", "standard user", [
        ("test_p7_exam", S, "synthetic syllabus and past papers"),
    ], None, "OCR of scanned papers not exercised."),
    ("F", "Collaboration", "standard user, viewer, editor, admin", [
        ("test_p7_collab", S, "roles, grants, revocation incl. downloads"),
        ("test_p7_api.Phase7Api.test_sharing_over_http_grants_then_revokes_access", H, ""),
    ], None, "Two simulated sessions on the stand-in server; no second real person, browser or notification delivery."),
    ("G", "Development Workspace", "special-access user", [
        ("test_p9_api", H, "Linux-namespace local_trusted engine only"),
        ("test_p9_security", S, "20 release-blocking tests for the Linux layer"),
    ], "BLOCKED for untrusted execution", "Docker and macOS isolation were never run here. Untrusted multi-user code execution stays DISABLED; this scenario proves only the restricted local_trusted path."),
    ("H", "Report delivery", "standard user", [
        ("test_p7_schedule.Schedules", S, "TEST ADAPTER for email/Slack; failure, retry and dead-letter simulated"),
    ], None, "No real email or Slack delivery was attempted."),
]


def run_one(test_id: str):
    loader = unittest.TestLoader()
    try:
        suite = loader.loadTestsFromName(test_id)
    except Exception as e:                                                   # noqa: BLE001
        return {"id": test_id, "ran": 0, "failures": 1, "errors": 0, "skipped": 0, "detail": f"could not load: {type(e).__name__}: {e}"}
    res = unittest.TestResult()
    t0 = time.time()
    suite.run(res)
    detail = "; ".join(f"{t.id().split('.')[-1]}: {tb.strip().splitlines()[-1]}" for t, tb in (res.failures + res.errors))[:300]
    if res.testsRun == 0:
        detail = detail or "no tests found"
    return {"id": test_id, "ran": res.testsRun, "failures": len(res.failures) + (1 if res.testsRun == 0 else 0), "errors": len(res.errors), "skipped": len(res.skipped), "seconds": round(time.time() - t0, 1), "detail": detail}


def main() -> int:
    results = []
    for sid, title, role, steps, override, note in SCENARIOS:
        out = []
        for test_id, layer, mocked in steps:
            r = run_one(test_id); r.update({"layer": layer, "mocked": mocked}); out.append(r)
        ran = sum(o["ran"] for o in out); bad = sum(o["failures"] + o["errors"] for o in out); skipped = sum(o["skipped"] for o in out)
        status = "FAIL" if bad else ("PASS (restricted)" if override else "PASS with limitations" if note else "PASS")
        results.append({"scenario": sid, "title": title, "role": role, "steps": out, "tests_run": ran, "problems": bad, "skipped": skipped, "status": status, "gate": override, "limitations": note})
    env = {"platform": platform.platform(), "python": platform.python_version(), "date": time.strftime("%Y-%m-%d %H:%M"), "fastapi": "stand-in over real Starlette/pydantic" if "standin" in " ".join(sys.path) else "installed"}
    out_dir = ROOT / "docs" / "phase11"; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pilot_results.json").write_text(json.dumps({"environment": env, "results": results}, indent=2))
    lines = ["# Pilot scenario results (automated, in the build sandbox)", "",
             f"Environment: {env['platform']}, Python {env['python']}, FastAPI: {env['fastapi']}, {env['date']}.", "",
             "**These are automated end-to-end tests, not a pilot with people.** External services are mocked where stated; nothing here proves live-provider connectivity, browser behaviour or container isolation.", "",
             "| Scenario | Role | Tests run | Problems | Result | Limitations |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['scenario']} — {r['title']} | {r['role']} | {r['tests_run']} | {r['problems']} | **{r['status']}**{' — ' + r['gate'] if r['gate'] else ''} | {r['limitations'] or '—'} |")
    lines += ["", "## Steps"]
    for r in results:
        lines += ["", f"### {r['scenario']} — {r['title']}"]
        for s in r["steps"]:
            lines.append(f"- `{s['id']}` — {s['layer']}{'; ' + s['mocked'] if s['mocked'] else ''}: {s['ran']} run, {s['failures'] + s['errors']} problem(s), {s['skipped']} skipped" + (f" — {s['detail']}" if s['detail'] else ""))
    (out_dir / "PILOT_RESULTS.md").write_text("\n".join(lines) + "\n")
    for r in results:
        print(f"{r['scenario']} {r['title']:24} {r['tests_run']:4} tests  {r['problems']} problem(s)  {r['status']}")
    return 1 if any(r["problems"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

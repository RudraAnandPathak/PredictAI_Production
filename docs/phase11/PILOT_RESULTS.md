# Pilot scenario results (automated, in the build sandbox)

Environment: Linux-6.18.44-fc-v470-x86_64-with-glibc2.39, Python 3.13.18, FastAPI: stand-in over real Starlette/pydantic, 2026-11-15 17:26.

**These are automated end-to-end tests, not a pilot with people.** External services are mocked where stated; nothing here proves live-provider connectivity, browser behaviour or container isolation.

| Scenario | Role | Tests run | Problems | Result | Limitations |
|---|---|---|---|---|---|
| A — Document intelligence | standard user | 15 | 0 | **PASS with limitations** | Scanned PDFs (OCR) are not supported; no model-written answers were exercised. |
| B — Data analytics | standard user | 11 | 0 | **PASS with limitations** | XLSX path is covered by parser tests, not by this scenario; charts are data-only (never rendered). |
| C — Predictive modelling | standard user | 12 | 0 | **PASS with limitations** | Large-data training times not measured here. |
| D — Research | standard user | 69 | 0 | **PASS with limitations** | NOT a live-provider test: Wikipedia, arXiv, SearXNG and the AI providers were never reached. |
| E — Exam Intelligence | standard user | 10 | 0 | **PASS with limitations** | OCR of scanned papers not exercised. |
| F — Collaboration | standard user, viewer, editor, admin | 14 | 0 | **PASS with limitations** | Two simulated sessions on the stand-in server; no second real person, browser or notification delivery. |
| G — Development Workspace | special-access user | 30 | 0 | **PASS (restricted)** — BLOCKED for untrusted execution | Docker and macOS isolation were never run here. Untrusted multi-user code execution stays DISABLED; this scenario proves only the restricted local_trusted path. |
| H — Report delivery | standard user | 10 | 0 | **PASS with limitations** | No real email or Slack delivery was attempted. |

## Steps

### A — Document intelligence
- `test_p10_api.Flow.test_upload_process_search_ask_cite_and_delete_over_http` — HTTP, real app stack, fresh SQLite; no AI model: extractive answers; citations verified as exact quotes: 1 run, 0 problem(s), 0 skipped
- `test_p10_reports.Building` — service layer, real SQLite; report from a finished analysis: 8 run, 0 problem(s), 0 skipped
- `test_p10_reports.Exports` — service layer, real SQLite; PDF/DOCX/PPTX/MD read back: 6 run, 0 problem(s), 0 skipped

### B — Data analytics
- `test_analytics_api.AnalyticsApiTests.test_schema_run_get_rows_export_over_http` — HTTP, real app stack, fresh SQLite; synthetic CSV: 1 run, 0 problem(s), 0 skipped
- `test_p7_reports` — service layer, real SQLite; dataset/prediction/exam/research to report, versions, citations: 10 run, 0 problem(s), 0 skipped

### C — Predictive modelling
- `test_predictive_api.Workflow` — HTTP, real app stack, fresh SQLite; in-process training of a synthetic dataset: 10 run, 0 problem(s), 0 skipped
- `test_p7_api.Phase7Api.test_lifecycle_needs_confirmation_and_prediction_names_its_version` — HTTP, real app stack, fresh SQLite: 1 run, 0 problem(s), 0 skipped
- `test_p7_api.Phase7Api.test_model_to_report_export_and_download` — HTTP, real app stack, fresh SQLite: 1 run, 0 problem(s), 0 skipped

### D — Research
- `test_research_api.ResearchApi.test_a_research_session_from_question_to_evidence_to_export_and_deletion` — HTTP, real app stack, fresh SQLite; MOCKED web/Wikipedia/arXiv providers: 1 run, 0 problem(s), 0 skipped
- `test_research_intelligence` — service layer, real SQLite; includes the Louis XVI historical question with canned sources: 27 run, 0 problem(s), 0 skipped
- `test_p10_reasoning` — service layer, real SQLite; multi-source analysis; quotes exact: 41 run, 0 problem(s), 0 skipped

### E — Exam Intelligence
- `test_p7_exam` — service layer, real SQLite; synthetic syllabus and past papers: 10 run, 0 problem(s), 0 skipped

### F — Collaboration
- `test_p7_collab` — service layer, real SQLite; roles, grants, revocation incl. downloads: 13 run, 0 problem(s), 0 skipped
- `test_p7_api.Phase7Api.test_sharing_over_http_grants_then_revokes_access` — HTTP, real app stack, fresh SQLite: 1 run, 0 problem(s), 0 skipped

### G — Development Workspace
- `test_p9_api` — HTTP, real app stack, fresh SQLite; Linux-namespace local_trusted engine only: 10 run, 0 problem(s), 0 skipped
- `test_p9_security` — service layer, real SQLite; 20 release-blocking tests for the Linux layer: 20 run, 0 problem(s), 0 skipped

### H — Report delivery
- `test_p7_schedule.Schedules` — service layer, real SQLite; TEST ADAPTER for email/Slack; failure, retry and dead-letter simulated: 10 run, 0 problem(s), 0 skipped

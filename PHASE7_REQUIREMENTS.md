# Phase 7 requirement register

Spec: `PREDICTAI — PHASE 7` (Advanced Intelligence, Model Registry, Exam Intelligence, Reports, Collaboration & Owner Command Center).
Status vocabulary: **Fully implemented** (built and verified end to end in the real target stack) · **Implemented but partially verified** (built, covered by the tests listed, but some part of the target stack could not be run here) · **Prototype** · **Planned** · **Blocked**.

Nothing is "Fully implemented" in this release, because the real FastAPI server, the real `vite build`, a browser, macOS/Python 3.14 and live providers could not be run in the build environment (see `docs/phase7/KNOWN_LIMITATIONS.md`). Backend behaviour is verified at service level and over HTTP against a FastAPI stand-in; the frontend is unit-tested, and bundled with esbuild to confirm every import resolves.

| # | Spec section | Where it lives | Tests | Status | Notes / gaps |
|---|---|---|---|---|---|
| 1 | 2.1 Root-cause exploration | `modules/intelligence/` (`root_cause`), `/assets/{id}/intelligence/root-cause`; screen: Advanced analysis | `test_p7_intelligence` | Implemented but partially verified | Associations with multiple-comparison correction; hypotheses, missing information and distinguishing tests listed; never a verdict |
| 2 | 2.2 Metric explanations | `explain_metric` (column or registry version), `/intelligence/metric` | `test_p7_intelligence` | Implemented but partially verified | **API only; no screen yet** |
| 3 | 2.3 Scenario analysis | `scenario`, `/intelligence/scenarios` | `test_p7_intelligence` | Implemented but partially verified | Baseline preserved, ranges validated, absolute/relative difference, sensitivity via non-saving predictions. **API only; no screen yet** |
| 4 | 2.4 Causal-method candidates | `causal_candidates`, `/assets/{id}/intelligence/causal-candidates` | `test_p7_intelligence` (including the forbidden-wording guard) | Implemented but partially verified | Association estimates with seeded bootstrap; labelled "not causal effects". **API only; no screen yet** |
| 5 | 2.5 Explainability, reproducibility | five registers, result hash, reproduction record on every run | `test_p7_intelligence` | Implemented but partially verified | |
| 6 | 3.1–3.2 Registration, versioning | `modules/registry/`, `/registry/...`; screen: Model registry | `test_p7_registry`, `test_p7_api` | Implemented but partially verified | Artifact copied with SHA-256; versions per registry model; same-family check |
| 7 | 3.3 Lifecycle | server-enforced transition table, confirmations, history, audit | `test_p7_registry`, `test_p7_api`, `phase7.test.js` | Implemented but partially verified | "Active" is a record, not a deployment (stated everywhere) |
| 8 | 3.4 Comparison | `/registry/compare` | `test_p7_registry` | Implemented but partially verified | **API only; no comparison screen yet** (Predictions has its own compare page for unregistered models) |
| 9 | 3.5 Predictions and lineage | `/registry/versions/{id}/predict` records the version | `test_p7_registry`, `test_p7_api` | Implemented but partially verified | **Predict-from-registry has no screen**; Predictions screens use Phase 5 models |
| 10 | 4.1 Ingestion | `modules/exam/` documents from existing uploads | `test_p7_exam` | Implemented but partially verified | Images only inside scanned PDFs; standard accounts 3 documents |
| 11 | 4.2 Curriculum mapping | `parse_curriculum`, `map_question`, append-only corrections | `test_p7_exam` | Implemented but partially verified | Correction is **API only; no screen yet** |
| 12 | 4.3–4.4 Frequency, emphasis score | `engine.analyse` | `test_p7_exam` | Implemented but partially verified | Documented heuristic, never "probability" |
| 13 | 4.5–4.7 Mock exams, blueprint, answers | blueprint/generate/edit/regenerate | `test_p7_exam` | Implemented but partially verified | Screen supports counts per type only; difficulty mix, chapters, seed, question edit/regenerate are API only |
| 14 | 4.8 Review and approval | validate/approve/reject with blocking vs warning | `test_p7_exam`, `test_p7_api` | Implemented but partially verified | Never auto-published |
| 15 | 4.9 Exam reporting | `report_pool`, template `exam_intelligence` | `test_p7_exam`, `test_p7_reports` | Implemented but partially verified | Creation from an exam analysis is **API only** in the report form |
| 16 | 5.1 Ten report types | `reports/templates.py` | `test_p7_reports` | Implemented but partially verified | |
| 17 | 5.2–5.3, 10.1 Research-to-Report, shared evidence | `ReportService.create` from research bundles without repeat research; `rep_evidence` register | `test_p7_reports` | Implemented but partially verified | Verified with a real stored bundle in tests; no live research |
| 18 | 5.4–5.5 Structure, evidence-grounded writing | sections → blocks → claims with evidence refs and caveats | `test_p7_reports` | Implemented but partially verified | Text is assembled from stored findings; no free generation |
| 19 | 5.6 Editing | ops with optimistic `base_version` | `test_p7_reports`, `test_p7_api`, `phase7.test.js` | Implemented but partially verified | Screen edits title, statements and citation style; adding/removing sections, tables, notes, evidence is **API only** |
| 20 | 5.7 Templates | system + user templates | `test_p7_reports` | Implemented but partially verified | Saving a template has **no screen** |
| 21 | 5.8 Citation styles | APA, MLA, Harvard, IEEE, Chicago | `test_p7_reports` | Implemented but partially verified | Missing details shown as missing |
| 22 | 5.9 Exports | PDF, DOCX, PPTX, MD, CSV, XLSX, PNG | `test_p7_reports` (headings, citations, version and readiness checked inside the exported files; ZIP-based formats opened and tested), `test_p7_api` | Implemented but partially verified | Needs `requirements-reports.txt`; layout not inspected visually; Py3.14 wheels unverified |
| 23 | 5.10 Versioning | immutable versions, diff, stale detection | `test_p7_reports` | Implemented but partially verified | Diff has no screen |
| 24 | 6.1–6.4 Workspaces, roles, resource permissions, sharing, revocation | `modules/collab/` | `test_p7_collab`, `test_p7_api` | Implemented but partially verified | Role change / member expiry / share expiry are API only; no public sharing (by design) |
| 25 | 7.1–7.2 Scheduling, snapshot/refresh | `modules/schedule/`, `scripts/run_schedules.py` | `test_p7_schedule` | Implemented but partially verified | Runs only when the script runs; editing a schedule is delete-and-recreate |
| 26 | 7.3 Delivery integration | local adapter always; email via SMTP; Slack webhook | `test_p7_schedule` (fakes) | **Local: Implemented but partially verified. Email/Slack: Prototype, Blocked without credentials** | Never reported as sent unless an adapter confirmed it |
| 27 | 7.4 Delivery history | runs and deliveries with attempts, errors, dead-letter, manual retry | `test_p7_schedule` | Implemented but partially verified | |
| 28 | 8.1–8.2 Dashboard | `modules/dashboard/`, `/dashboard`; screen | `test_p7_dashboard_owner`, `test_p7_api` | Implemented but partially verified | Counts of real rows; each section degrades alone with a recovery hint |
| 29 | 9.1 Owner overview | `modules/ownerops/`, `/owner/overview` | `test_p7_dashboard_owner` | Implemented but partially verified | Counts only |
| 30 | 9.2–9.3 User management, access controls | existing `/owner/users…` (search, block/unblock, special access, codes, audit) + metadata-only usage summary | Phase 2 tests + `test_p7_dashboard_owner` | Implemented but partially verified | Reuses Phase 2; nothing duplicated |
| 31 | 9.4 Monitoring | `/owner/monitoring` | `test_p7_dashboard_owner` | **Partial: CPU/memory Planned (reported "cannot be measured")**; others Implemented but partially verified | No agent installed by design |
| 32 | 9.5 Maintenance mode | `own_maintenance`, API guard, scheduler pause | `test_p7_dashboard_owner` | Implemented but partially verified | Enforced for authenticated writes; sign-in/registration/reset unaffected by design |
| 33 | 9.6 Privacy boundary | owner views are metadata-only | `test_p7_dashboard_owner` (a secret title must not appear) | Implemented but partially verified | |
| 34 | 10.2 Other workflows | prediction→report, exam→report, scenario→report, share→collaborate, schedule | `test_p7_api`, `test_p7_reports`, `test_p7_schedule` | Implemented but partially verified | |
| 35 | 11.1–11.2 Database, API | migration `0006`, 7 new routers | `test_workspace_projects` (upgrade 5→6 keeps rows), `test_p7_api` | Implemented but partially verified | |
| 36 | 12.1–12.3 Frontend | pages under `frontend/src/pages/phase7`, owner tabs, navigation | `phase7.test.js`, `navigation.test.js`, esbuild bundle | Implemented but partially verified | `exam` and `reports` placeholders removed; no fake progress; build and browser unverified |
| 37 | 13 Security, privacy, governance | access resolver (404 on no access), rate limits, typed errors, pagination, audit, CSV/XLSX formula neutralisation | `test_p7_*` | Implemented but partially verified | |
| 38 | 13.8 Regression | full Phase 1–6 suite re-run | see `docs/phase7/TESTING.md` | see TESTING.md | Existing tests were changed only where the migration count (5→6) or the navigation status of exam/reports/models legitimately changed; none were weakened |
| 39 | 13.9 Production build | `npm run build` | — | **Blocked in this environment** | Must be run on your machine/CI |

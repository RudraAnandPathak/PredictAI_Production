# Phase 7: known limitations (read before relying on it)

Nothing below is hidden in the interface; each item is also stated where it matters.

## Not verified in the build environment
- **Real FastAPI and `vite build`.** The sandbox has no network and no FastAPI/Vite install. Backend HTTP tests ran against a small stand-in for FastAPI (`/home/claude/work/standin`, not part of the release); the frontend was syntax- and import-checked by bundling with esbuild (all imports and named exports resolve) and unit-tested with `node --test`. Neither is a substitute for `vite build` and the real server. Run `pip install -r requirements-dev.txt && python -m unittest discover -s tests` and `npm ci && npm test && npm run build` on your Mac and CI before trusting this.
- **macOS / Python 3.14.** Developed on the sandbox's Python 3.13. CI targets 3.12. Wheel availability for the export libraries on 3.14 is unknown.
- **Live providers, email and Slack delivery.** Email and Slack adapters are tested against fakes only. They are off (and the interface says so) until credentials exist. Local delivery (keep the file in PredictAI) is the only path exercised end to end.
- **Browsers and accessibility.** No browser was driven. Keyboard and screen-reader behaviour was designed for (labels, roles, native controls) but not audited.

## Product limits
- **Scheduler is external.** Schedules run only when `backend/scripts/run_schedules.py` is run (cron/launchd, every few minutes). There is no hidden background daemon. Missed runs are not replayed; the next due time is used.
- **Exam documents:** images are supported only as scanned PDFs (Phase 3 has no standalone image upload type). Standard accounts are limited to 3 documents in total (owner-adjustable; Special access lifts it), which also bounds how many papers an exam subject can use.
- **Exam analysis is a heuristic.** "Historical frequency", "relative emphasis" and a documented "heuristic score" (weights 0.4 marks share, 0.3 recurrence, 0.2 count share, 0.1 recency). It is not a probability and never a forecast; the word does not appear in output (tested). Question and curriculum parsing is rule-based and can mis-map; every mapping can be corrected and corrections are kept.
- **Mock exams are drafts.** Nothing is published until a person approves. Questions built from templates are labelled as such. Answers and marking schemes are only as good as the supplied material.
- **Advanced intelligence reports associations**, with multiple-comparison correction and bootstrap intervals. It never states a cause. Causal "candidates" are screening results, not effects.
- **Reports are built only from the creator's own saved results** (research bundles, analyses, registered model versions, exam analyses, scenario runs). Creating or refreshing a report never re-runs research, training or calculations.
- **Sharing:** reports, registered models, research results, mock exams, analyses and exam analyses can be shared with a workspace. Datasets, documents and conversations are shareable by description only (no content). No public links exist. Viewers never edit, whatever the grant says. Removing a member or revoking a share takes effect on the next request.
- **Invitations are not emailed** unless SMTP is configured; otherwise the admin copies the one-time link and sends it themselves.
- **Model registry is a record, not a deployment.** "Active" means "the version predictions use by default inside PredictAI". Nothing is served elsewhere. Rollback changes the pointer, not the stored versions.
- **Exports:** PDF/DOCX/PPTX/XLSX/PNG need the optional libraries in `requirements-reports.txt`; a missing one returns a typed "format unavailable" error and other formats keep working. Visual fidelity was checked by reading the generated files back (text, tables, structure), not by looking at them in Word/PowerPoint.
- **Owner Command Center:** CPU and memory are reported as "unavailable" (PredictAI collects no host metrics and installs no agent). Disk and load average come from the operating system when available. Job health uses heartbeats; a missing heartbeat is shown as "no recent signal", never as healthy. Maintenance mode blocks changes by non-owners at the API layer for every authenticated write route; public auth routes (sign-in, registration, password reset) and sign-out are intentionally unaffected.
- **Owners cannot read user content.** The platform-owner role is separate from workspace roles and grants no access to projects, documents, reports or conversations. Owner views are counts, sizes, states and timestamps.
- **Rate limits are in-process counters** (as in earlier phases); with several server processes each has its own counters.
- **Lifecycle table duplication.** The frontend keeps a copy of the lifecycle rules as a fallback; the version page uses the server's `allowed_transitions`. If the server rules change, update `features/phase7/labels.js`.

# PredictAI

Explainable, privacy-aware data and document intelligence. This repository holds the
**Phase 1 foundation** (React shell + FastAPI health check), **Phase 2: Identity & Security**
(accounts, sessions, roles, special access, owner controls), **Phase 3: Projects, Data Workspace,
Governance and Social Sign-in**, **Phase 4: Core Intelligence** (analytics, statistical tests, insights,
natural-language assistant, interactive charts) and **Phase 5: Predictive Intelligence & AutoML** (dataset preparation for machine learning,
automatic model training and comparison, evaluation, explainability, predictions, and a plain-English **Explanation** for every result;
details below) and **Phase 6: Research & World Intelligence** (evidence-based research with citations, opt-in web research, document search,
news and event intelligence, association analysis, AI provider routing) and **Phase 7: Advanced Intelligence, Model Registry, Exam Intelligence, Reports, Collaboration and the Owner Command Center** (see "Phase 7 at a glance"). Further feature modules arrive in later phases;
the roadmap and requirements live in the Master Engineering Documentation.

## Layout

```
backend/    FastAPI service (Python)
            app/core      config, errors, logging, middleware, clock
            app/db        SQLite access + versioned SQL migrations (app/db/migrations)
            app/api       thin routers and dependencies (auth, me, owner, oauth, health)
            app/modules/workspace  projects, uploads, extraction/OCR, profiling, cleaning, governance
            app/modules/analytics  Phase 4 analysis engine, insights, assistant (+ the mixed-variable operation)
            app/modules/predictive Phase 5 ML preparation, AutoML, evaluation, explainability, prediction, jobs
            app/modules/explanation Phase 5 universal "Explanation" mechanism used by every analytical module
            app/modules/storage    storage backend (local files, opaque keys)
            app/modules/identity   all identity rules, framework-free: passwords, sessions, RBAC,
                          access codes, audit, rate limiting, email, OAuth, owner provisioning
            app/cli.py    server-side administration (owner provisioning and recovery)
            tests/
frontend/   React + Vite single-page app. src/api = the only place that calls the API;
            src/features/identity = auth state, permissions, re-authentication; src/features/workspace = project/upload/asset logic; src/pages = screens
config/     Environment templates (copy to .env; never commit real values)
docker/     docker/searxng: a private, local-only SearXNG for general web research (see docs/phase6/WEB_RESEARCH_SETUP.md)
shared/     Cross-stack contracts (JSON examples used by both test suites)
.github/    CI workflow
```

Boundaries: the frontend knows the backend only through `frontend/src/api` and `shared/contracts`.
Routers stay thin; rules live in `backend/app/modules/<domain>` and never import routers.

## Prerequisites

Python 3.10 or newer, Node.js 20 or newer. No paid services and no secrets are needed for development.

## Run locally

```bash
# 1. Configuration (repository root)
cp .env.example .env

# 2. Backend  ->  http://127.0.0.1:8000   (docs at /api/v1/docs in development)
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m app.cli migrate                                                           # create the database
python -m app.cli provision-owner --email you@example.com --display-name "Your Name"   # prompts for a password
python -m app

# 3. Frontend, in a second terminal  ->  http://localhost:5173
cd frontend
npm install
npm run dev
```

Check the connection: `curl -i http://127.0.0.1:8000/api/v1/health`, then open http://localhost:5173. The top bar
shows **API connected** when the backend answers; the **System status** page shows the full health payload. In
development the Vite server proxies `/api` to the backend, so no CORS setup is needed.

Register an account, or sign in as the owner you provisioned. In development, verification and password-reset emails
are written as files to `data/outbox/` (open the link inside). The database is created automatically at
`data/predictai.db`; both live in the git-ignored `data/` folder. Without `PREDICTAI_SECRET_KEY`, development generates
a random secret into `data/dev-secret-key` so sessions survive restarts.

## Phase 11 at a glance (hardening and release readiness)

Backup/restore (`python -m app.ops.backup`), release manifest and migration-compatibility check (`python -m app.ops.release`), a security sweep and scanner, an accessibility contrast check, an offline performance baseline, pilot-scenario runner, 14 runbooks and a 16-gate release assessment. **Read `docs/phase11/RELEASE_GATES.md` first.** Verdict in one line: fine for local development; a restricted internal pilot only under the listed conditions; **not** ready for external or public release; untrusted code execution stays disabled. Nothing here was verified on a real server, in a browser, on macOS, with Docker or against live providers.

## Conversations

One list and one chat over the dataset assistant and the research assistant: history, search, rename, delete, Markdown export, cited answers. See `docs/phase12/CONVERSATIONS.md`. Page: sidebar → Conversations. API: `/api/v1/conversations`.

## Phase 11T: Turso database (production)

The API can run on a remote Turso (libSQL) database instead of a local SQLite file: set `PREDICTAI_DATABASE_BACKEND=turso`, `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`, and install `backend/requirements-turso.txt`. Local development keeps using SQLite with no changes. `python -m app.cli import-to-turso` copies an existing SQLite database (dry run by default). `render.yaml` deploys the API to Render in Singapore. Not yet verified against a real Turso database, and uploaded files still live on the server disk; read `docs/PHASE11_TURSO_DATABASE_INTEGRATION.md` first.

## Phase 10E: Reviews

Under each piece of their own work people can leave an optional 1–5 star review with a comment. The owner reads them in Owner → Reviews with the account that wrote them. When an account is deleted its reviews stay, anonymised. This phase also fixes account and project deletion for people who used exam prediction or Notebook. See `docs/phase12/PHASE10E.md`.

## Phase 10D: full Notebook Studio, Deep Research, older research retired

The Notebook Studio now also makes flashcards, quizzes, mind maps, data tables, slide decks (PowerPoint download), infographics and a two-host audio overview read aloud by your browser. Deep Research finds web pages and papers and adds them as sources. Reports from the older Research area can be copied into notebooks, and the two older research areas are gone from the menu. See `docs/phase12/PHASE10D.md`; video overviews are not built.

## Phase 10C: Notebook, default-on external AI, task routing

A NotebookLM-style Notebook (sources, cited chat, Studio briefings, study guides, FAQs and timelines) built on the research-intelligence workspaces. External AI is now on by default and each person can switch it off for themselves. See `docs/phase12/PHASE10C.md`; later Studio outputs, Deep Research, audio and data migration are not built yet.

## Phase 10B: AI Brain (Gemini) and exam prediction

Gemini through Google AI Studio on the existing provider layer, with request caps and fallback to a local model and the built-in rules; and an exam prediction feature that ranks chapters from past papers and the syllabus, tests the ranking on your own papers, optionally reads exam articles from the web, and builds an estimated paper. Setup, limits and the data notice are in `docs/phase12/PHASE10B.md`. Nothing was run against live Gemini or the live web.

## Phase 10A: monitoring, forecasting, ensembles, what-if, magic link

Built from the master-blueprint audit. Model drift and outcome-based performance on each registered version (owner only), a Forecast page with backtest and intervals, opt-in voting/stacking ensembles, what-if scenarios on a model, and one-time-link sign-in for confirmed email addresses. See `docs/phase12/PHASE10A.md` for what it does, the API and the limits; gaps still open (SHAP, connectors, Apple sign-in) are in `docs/phase12/BLUEPRINT_GAP_ANALYSIS.md`.

## Phase 3 at a glance

- **Projects:** create, rename, archive/restore, delete (name confirmation), activity history. Strictly per owner;
  another user's project is indistinguishable from a missing one.
- **Data and documents:** datasets (CSV, XLSX, XLS, ODS, JSON, XML) and documents (PDF with text or OCR, DOCX, RTF,
  TXT, MD). Chunked, resumable, checksummed uploads with server-side validation. Originals are never modified.
- **Extraction and profiling:** traceable extraction (nothing dropped silently, OCR confidence per page); profiles
  label every number as fact, heuristic or estimate and record the source version and time. No predictive modelling.
- **Cleaning:** suggestions, then a preview, then an explicit confirmation creates a new version; full history and rollback.
- **Governance:** retention dates, deletion with completion evidence, storage footprint and inventory, privacy-safe logs.
  Standard accounts: 3 documents of up to 100 pages; Special/Owner unlimited through the existing entitlements.
- **Google / GitHub:** register, sign in, link, unlink and re-authenticate. Accounts are created only from a
  provider-verified email and are **never merged by email**. Client secrets stay on the server (see `.env.example`).
  Apple and magic links are deferred.

Optional free extras for reading more formats: `pip install -r backend/requirements-data.txt`; for scanned PDFs install
`tesseract` and `poppler` (`brew install tesseract poppler`). Without them the affected format reports that it cannot be read yet.
Documentation: `docs/phase3/` (IMPLEMENTATION, REQUIREMENTS_REGISTER, TESTING, MIGRATIONS, GOVERNANCE, SOCIAL_AUTH, PLAN).

## Phase 4 at a glance

Open a dataset from **Analytics** (or "Analyse this dataset" on a file). Tabs: **Overview** (descriptive statistics, missing values,
data quality), **Relationships** (ranked, adjusted for many comparisons), **Tests** (assumptions, effect size, confidence interval),
**Charts** (bar, histogram, scatter, heatmap, time series; hover, brush, click to see the rows, export), **Insights** (inspect, save,
dismiss), **Ask** (plain-language questions that ask rather than guess), **Saved** (reopen and re-run to confirm reproducibility).

- Every number is calculated on the server by a typed, resource-limited analysis plan; the browser and any language model never calculate.
- Every result shows its source version, filters, method, assumptions, uncertainty and limits, and is marked **Evidence verified** or
  **Evidence incomplete**. Associations are never described as causes.
- Memory limits: enforced by the kernel only where the OS proves it (Linux); on macOS they are monitored best-effort (a watchdog kills over-limit runs). See `docs/phase4/TESTING.md`.
- Needs `numpy`, `pandas`, `scipy` (in `backend/requirements.txt`). Optional local AI wording via Ollama is off by default and never receives rows.
- Settings: `PREDICTAI_ANALYTICS_ISOLATION`, `PREDICTAI_AI_*` (see `.env.example`). Docs: `docs/phase4/`.

## Phase 5 at a glance

Open **Predictions**, choose a dataset, then work left to right: **1. Objective** (pick the column to predict; PredictAI works out whether it is
a yes/no question, a choice between categories or a number, and says why) → **2. Data preparation** (what each column will be used for, missing
values, repeated rows, columns that give the answer away, imbalance — all explained and all adjustable; your file is never changed) →
**3. Training** (pick algorithms or let PredictAI choose; fair validation on rows the model never saw; optional bounded tuning; live progress and
**Cancel**) → **Models** (open one: **Evaluation**, **Model comparison**, **Explainable AI**, **Predict one case**, **Predict a dataset** with CSV download,
**Details and history**). **Predictions → All models** manages, renames, deletes and compares saved models.

- **scikit-learn only** (12 algorithm families, all free). No paid service and no external call; nothing leaves the server.
- **No leakage by construction:** every preprocessing step is part of one pipeline fitted on the training part of each fold; test scores are shown but never
  used to rank or tune; rows with no known answer are excluded and counted.
- **Honest explanations:** permutation and model-native importance, partial dependence, and "what moved this prediction" by replacing one input at a time.
  These describe what the model relies on, **not what causes an outcome**. SHAP is **not** included (it is not installed here) and is labelled as such.
- **Explanation section everywhere:** every result — Phase 4 analytics, tests, charts, insights, assistant answers, data profiles and all of Phase 5 — carries
  a section titled **Explanation** in plain English, written from the actual numbers (every figure in it is checked against the computed result) and
  marked *unavailable, with the reason* when it cannot be written reliably. One reusable mechanism: `app/modules/explanation`.
- **Mixed-variable analysis:** numeric–numeric, category–numeric and category–category associations choose the suitable method (Pearson/Spearman,
  correlation ratio/ANOVA/Kruskal–Wallis, chi-square/Cramér's V), check assumptions, report effect sizes and intervals, and refuse invalid pairings.
- **Safety:** each training/prediction task runs in a separate resource-limited process with a scrubbed environment and can be cancelled; models are
  server-written files verified by SHA-256 before use; every model, job and prediction belongs to its owner and project (other accounts get the same
  "not found" as for something that does not exist); limits, rate limits and audit rows are applied like the rest of the app.
- Needs `scikit-learn` and `joblib` (in `backend/requirements.txt`). Settings: `PREDICTAI_ML_ISOLATION`, `PREDICTAI_ML_WORKERS` (see `.env.example`).
  Migration `0004` adds three tables (`python -m app.cli migrate`). Docs: `docs/phase5/`.

## Phase 10 at a glance (Research Intelligence and Knowledge Studio)
**Research Intelligence** (`/research-intelligence`): private research workspaces where you add PDF, DOCX, TXT, Markdown, CSV, XLSX files or a web address / DOI / arXiv id, then ask questions. Answers quote your sources with clickable citations, or say there is not enough evidence. Documents stay on your computer unless you explicitly allow an external AI for a workspace.
- Needs the research worker for processing: `python backend/scripts/run_research_worker.py` (install `backend/requirements-research.txt`; report exports also need `backend/requirements-reports.txt`).
- **Eight sections:** Research Home, My Workspaces, Discover Research (Crossref / OpenAlex / arXiv / Semantic Scholar), Source Library (save, compare, add to a workspace), Research Assistant, Research Studio (compare sources, keep and connect findings, outlines, research questions and hypotheses), Research Reports (7 types, edit, regenerate, version history, export to PDF / DOCX / PPTX / Markdown), Research Settings.
- Reports separate what the sources said (exact quotes) from what the tool computed and from what you wrote, never invent references, and state that they are not peer reviewed or academically validated.
- **Not verified:** the screens have never been rendered in a browser and the live providers, Qdrant and Ollama were never reached from the build sandbox. Run `scripts/verify_phase10_live.py` and follow the checklist in `docs/phase10/TESTING.md`. OpenAlex needs a free key.
- Details: `docs/phase10/` and `PHASE10_REQUIREMENTS.md`.

## Phase 9 at a glance
Development Workspace (Special access only; the brief was titled "Phase 8" but that number was already delivered): code files with versions, notebooks, per-project environments, jobs, artifacts, Git, pipelines (feature-flagged), experiments with lineage, and Compute usage/quotas.
- **Code never runs in the web server.** Jobs are queued in SQLite and executed by a separate worker: `python backend/scripts/run_dev_worker.py`. Execution is **off by default** (`PREDICTAI_DEV_EXECUTION=off`).
- **`local_trusted`** is for one trusted person on their own machine (Linux: network/PID/mount namespaces verified in the build sandbox; macOS `sandbox-exec` **unverified**). **`docker`** mode is built but was **never run** (no Docker in the build sandbox). **Neither is a multi-user sandbox**; do not run other people's code until you have verified Docker isolation yourself (`docs/phase9/SECURITY.md`).
- Migration `0008` adds 18 empty `dv_*` tables. Needs `cryptography` (`backend/requirements-devspace.txt`) to store secrets.
- No deployment exists: approvals and rollback targets are recorded decisions only.

Start with `docs/phase9/ARCHITECTURE.md`, then `SECURITY.md`, `INSTALLATION.md`, `KNOWN_LIMITATIONS.md`, `TESTING.md`. Requirement status: `PHASE9_REQUIREMENTS.md`.

## Phase 8 at a glance
Accounts, access control and research readiness. Phase 2 already provided registration, sign-in, sessions, password reset, roles, hashed special-access codes and the audit log, so Phase 8 **audited and tested them rather than rebuilding them**, and added only what was missing:
- **First owner from the server environment** (`PREDICTAI_OWNER_*`, or `python -m app.cli bootstrap-owner`): created once, never a second owner, never a password reset, no website or API route.
- **Research workspace foundation** (migration 0007, `/research-workspaces` API, owner-adjustable limits). No research engine yet; "Research Intelligence" is listed as Planned (phase 10).
- **A Phase 7 deletion defect fixed:** deleting a project, file or account now also removes registry models, reports, exports, schedules, exam material, shares and the stored objects outside the project folder.

Start with `docs/phase8/IMPLEMENTATION.md`, then `OWNER_AND_ACCESS.md`, `API.md`, `MIGRATION.md`, `TESTING.md`, `KNOWN_LIMITATIONS.md` and `READINESS_PHASE9_PHASE10.md`. Requirement status: `PHASE8_REQUIREMENTS.md`.

## Phase 7 at a glance

Implemented on top of Phases 1–6 without rebuilding them (one create-only migration, `0006`; every earlier API route unchanged):

- **Model Registry**: versioned, checksum-verified copies of trained models with a confirmed lifecycle, history and rollback. A record, not a deployment.
- **Advanced analysis**: possible drivers of an outcome, metric explanations, scenario comparison and causal-method *candidates*; association, never proven cause; five separated result registers and a reproducibility record.
- **Exam Intelligence**: syllabus and past-paper analysis (historical frequency, relative emphasis, a documented heuristic score; never a probability), mock-exam drafts with origin labels, validation and explicit approval.
- **Reports**: ten templates built from results you already have (research, analyses, models, exam, scenarios), claim-level evidence, five citation styles, immutable versions, exports to PDF, Word, PowerPoint, Markdown, CSV, Excel and PNG.
- **Collaboration**: private workspaces, viewer/editor/admin roles, invitations, per-item sharing and instant revocation. No public sharing.
- **Scheduled delivery**: run by `backend/scripts/run_schedules.py`; local delivery always, email/Slack only when configured; retries, history, manual retry.
- **Dashboard** and **Owner Command Center**: your own real counts, limits and attention items; owner overview, honest monitoring ("cannot be measured" is never shown as healthy), quota policy and confirmed maintenance mode, all metadata-only.

Start with `PHASE7_REQUIREMENTS.md` (status of every requirement), `docs/phase7/INSTALL.md`, `docs/phase7/KNOWN_LIMITATIONS.md` and the guides in `docs/phase7/`. Phase 7 was verified offline: the real FastAPI server, `vite build`, a browser, macOS/Python 3.14 and live email/Slack were **not** run (`docs/phase7/TESTING.md`).

## Phase 6 at a glance

Open **Research**, pick a project, and use its tabs: **Research** (history and search of earlier work), **Search documents**, **Sources and evidence**, **News and events**,
**Influence analysis** and **Privacy for this project**. **New research** turns a question into a plan you can read (parts, search phrases, what is and is not used), runs it in the
background with live progress and **Cancel**, and gives a **Result**: conclusions that are quotations with their page or web address, a label for where each came from
(your material, calculated, model prediction, outside source) and how strong it is, disagreements shown side by side, what was not found, quality checks, limits, and the
shared **Explanation**. Click a citation to see the passage re-read from the saved source. Export as JSON, Markdown or CSV.

- **Nothing leaves your server unless you say so.** Web research needs three switches (the server, you, the project) and your approval of the exact search phrases; text from your
  documents and datasets is never put in a search or sent to an outside AI. Search sites: Wikipedia, arXiv and a SearXNG you run. No paid service, no new dependency.
- **No invented findings.** A conclusion is a quotation; the engine says "nothing found" instead of guessing. Retrieved pages and feeds are untrusted data: instruction-like text is
  removed or the article held back, and addresses are checked against private networks (SSRF) at every redirect.
- **News and events** (feeds you add, under their publishers' terms): topics, entities, tone, events grouped across outlets, timelines, trends with a significance test. "Confirmed"
  means several outlets agree, not that PredictAI verified it; tone is wording, not truth. These methods are heuristics.
- **Influence analysis** tests whether coverage and one of your numeric columns move together, and which came first. It reports association only; cause is never claimed.
- **AI providers** are configuration: the built-in path works with no model; an owner may add a local Ollama or an OpenAI-compatible server. Paid providers are never switched on automatically.
- **Corrective release (research engine, web search, datasets, Wikipedia and arXiv):** web search was broken because a stock SearXNG answers 403 to JSON requests (a ready-made local setup is now in `docker/searxng`),
  and answers were weak because one pile of loose keywords went to every provider. Now each provider gets phrases in its own syntax; Wikipedia is read through the MediaWiki API by section (tables, lists, redirects,
  disambiguation) and arXiv through its API with the paper's full text and versions, both labelled (encyclopedia, preprint); an uploaded **dataset can be researched directly** (calculated by the Phase 4/5 tools, never
  written by a model), combined with documents, models and outside sources in one **report** that keeps calculated findings and outside claims apart; every citation opens its calculation or original page;
  **follow-up questions** build on what was already read; **Research → project → Search tools** tests each provider and searches one on its own. Setup, limits and troubleshooting: `docs/phase6/WEB_RESEARCH_SETUP.md`.
  Report: `docs/phase6/CORRECTIVE_REPORT.md`; requirement mapping: `docs/phase6/CORRECTIVE_REQUIREMENTS.md`. No migration was needed.
- **Not in Phase 6:** Exam Intelligence and the Model Registry stay Phase 7 deliverables (read interfaces only). See `docs/phase6/OUTSTANDING.md`.
- Migration `0005` adds 14 tables. Settings: `PREDICTAI_RESEARCH_*`, `PREDICTAI_SEARXNG_URL`, `PREDICTAI_AI_ALLOW_PAID` (see `.env.example`). Docs: `docs/phase6/`. Browser run: `scripts/e2e/`.

## Identity & security at a glance

- **Passwords:** scrypt (memory-hard, from the Python standard library) with per-password salts, NFKC
  normalisation, automatic re-hash when parameters change, no plaintext anywhere.
- **Sessions:** a random opaque token in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` + `__Host-` prefix when
  hosted). Only its SHA-256 hash is stored. Idle and absolute expiry (stricter for owners); rotated on sign-in,
  password change and re-authentication (no fixation); revoked on logout, suspension, role change and reset.
- **CSRF:** trusted-`Origin` check plus a per-session token (HMAC) sent as `X-CSRF-Token`.
- **Abuse protection:** database-backed rate limits per account and per address; temporary lockout after repeated
  failures; uniform answers for register / recovery / sign-in so addresses cannot be enumerated.
- **Roles:** `user` and `owner` roles, `standard` and `special` tiers, mapped to named permissions in
  `modules/identity/constants.py` (add roles/permissions there). Permissions are recomputed from the database on
  every request, so revocation is immediate. The UI only hides things; the server decides.
- **Special access:** the owner creates individual, single-use codes (100 bits, shown once, only a hash stored,
  expiring, revocable until used). Redeeming one stores an entitlement on the account. There is no universal code.
- **Owner:** created only on the server (`python -m app.cli provision-owner`); never via the web, no default
  credentials. At most `PREDICTAI_MAX_OWNERS` owners, the last owner cannot be removed or deleted. Owner changes need a
  recent password confirmation (step-up). Break-glass recovery: `python -m app.cli recover-owner`.
- **Privacy:** only email and an optional name are stored; sessions keep a coarse device label (e.g. "Chrome on macOS");
  audit records keep a truncated IP (IPv4 /24, IPv6 /48) and never secrets. Account deletion anonymises personal data.
  Audit retention is configurable (`PREDICTAI_AUDIT_RETENTION_DAYS`; `python -m app.cli purge`).
- **Email links** carry their single-use token in the URL fragment (`#token=`), so it is not sent to servers or logs.

## Server administration (CLI)

```bash
cd backend
python -m app.cli migrate            # apply migrations (also automatic in development)
python -m app.cli provision-owner    # first owner; add --allow-additional for a backup owner
python -m app.cli list-owners
python -m app.cli recover-owner      # set a new password for an existing owner and end all its sessions
python -m app.cli oauth-status       # is Google/GitHub sign-in configured? (names only, no secrets)
python -m app.cli purge              # delete data past its retention period (identity + workspace files); schedule daily when hosted
```

Passwords are prompted without echo (or read from stdin with `--password-stdin`) and are never printed.

## Tests

```bash
cd backend  && python -m unittest discover -s tests -v   # HTTP tests need requirements-dev.txt (they skip without FastAPI)
cd frontend && npm test                                  # Node's built-in runner, no extra packages
cd frontend && npm run build                             # production build
python scripts/package_release.py ../PredictAI.zip       # clean release archive (never includes .env, data/, databases, keys)
```

## Configuration

All settings come from environment variables (the root `.env` is read in development; real environment
variables win). The backend validates everything at start-up and reports every problem at once. In production
the API refuses to start without a strong `PREDICTAI_SECRET_KEY`, an https `PREDICTAI_FRONTEND_BASE_URL`,
explicit https CORS origins, a non-file email backend and secure cookies. Only `VITE_*` variables reach the
browser, so never put credentials in them. See `.env.example` (development) and `config/env.production.example`. In staging and production API docs are off by default.

External things you must supply for production: a secret key, an SMTP account (for verification, reset and
notification emails), https hosting, persistent storage for `PREDICTAI_STORAGE_DIR` and the database, and (optionally)
Google/GitHub OAuth client credentials (redirect URI and steps: `docs/phase3/SOCIAL_AUTH.md`).

## API conventions

- Versioned under `/api/v1`. Success: `{ "data": ..., "meta": { "request_id": ... } }`.
  Failure: `{ "error": { "code", "message", "request_id", "details"? } }`.
- Every response carries `X-Request-ID`; the same ID appears in logs. Errors never include stack traces,
  internal text or echoed input (validation errors name the field and rule, never the value).
- Examples of both shapes are in `shared/contracts` and are checked by the tests.

## Status and known limitations

Phases 1, 2, 3, 4, 5 and 6 are implemented and tested together (Phase 6 record: `docs/phase6/TESTING.md`; requirement status: `docs/phase6/REQUIREMENTS_REGISTER.md`; Phase 5 record, including what could not be run here: `docs/phase5/TESTING.md`; Phase 4 record: `docs/phase4/TESTING.md`). Test results, what could not be run in the build
environment (the real Vite build, real FastAPI, real Google/GitHub and SMTP) and the requirement-by-requirement status
are in `docs/phase3/TESTING.md` and `docs/phase3/REQUIREMENTS_REGISTER.md`; the Phase 1+2 record is `docs/INTEGRATION.md`.
The research engine was upgraded for entity-aware, relevance-checked answers (`docs/phase6/INTELLIGENCE_UPGRADE.md`); check live providers with `python scripts/live_research_check.py`.

**Phases 7, 8 and 9 are implemented but only partially verified** (`PHASE7_REQUIREMENTS.md`, `PHASE8_REQUIREMENTS.md`, `PHASE9_REQUIREMENTS.md`, `docs/phase7/TESTING.md`, `docs/phase8/TESTING.md`, `docs/phase9/TESTING.md`); the next phase is not started. **PredictAI is not declared production-ready:** the platform-wide quality assessment is scheduled after Phase 12. Phase 6 and its corrective release were verified offline against local fakes only: no real Wikipedia, arXiv, SearXNG, feed or AI provider was contacted (what was and was not run: `docs/phase6/TESTING.md`). Run `npm install && npm run build` and `pip install -r backend/requirements-dev.txt` once on your own machine to confirm the unverified items (real Vite build, real FastAPI; listed in `docs/phase5/TESTING.md`).

## Decisions to know about

- **Routing:** a small History-API router in `frontend/src/app/router`. Public pages: overview, system status and the
  sign-in/recovery screens; everything else needs an account.
- **SQLite** (standard library, WAL) with plain, checksummed SQL migrations that stay D1-compatible.
- **JavaScript, not TypeScript, for now.**
- **Not yet built (Phase 10):** OCR, PDF table extraction, semantic clustering, browser/accessibility/performance tests, and any verification against live providers. The Model registry is now real (Phase 7) and sits beside Predictions, which still manages the models you train.
- `npm install` creates `frontend/package-lock.json`; commit it, then switch CI to `npm ci`.

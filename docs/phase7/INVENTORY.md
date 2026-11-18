# Phase 7 — repository inventory and requirement mapping (Stage 1)

Base: `PredictAI_Phase6_Intelligence` (Phases 1–6 plus the research intelligence upgrade), copied unchanged to `PredictAI_Phase7/`. Migrations 0001–0005 exist; Phase 7 adds 0006 only.

## What exists (verified by reading the code, not the UI)
| Area | State | Phase 7 use |
|---|---|---|
| Identity: users, sessions, roles `user`/`owner`, tier `special`, entitlements, audit_events, owner API (users, block/unblock, special access, codes, audit, owners) | Complete | Reused. Permissions are `Perm` constants in `identity/constants.py`; owner routes use `require_owner` |
| Projects/assets/versions, uploads, extraction (PDF text layer, OCR if installed), profiling, governance (quotas) | Complete | Exam documents are ordinary assets; reports/models/exams are project-scoped |
| Analytics (profiling, stats, charts, evidence) | Complete | Advanced Intelligence reuses `analytics.frame` + `scipy`; nothing re-implemented |
| Predictive: `ml_models` (artifact key + sha256, metrics, lineage), `ml_predictions`, training jobs, `registry_view.py` (read-only view, states "not supported") | Complete for training and predicting | Registry builds on `ml_models` rows; it does not retrain |
| Research: sessions, sealed `predictai.evidence_bundle/1`, citations, `EvidenceHub`, `ExamResearchInterface` (explicitly "not built") | Complete | Report engine consumes bundles through the research service |
| Existing exports | Research bundle JSON/Markdown; analytics CSV | Report exports are new (PDF/DOCX/PPTX/MD/CSV/XLSX/PNG) |
| Job runners | Thread pools for ML and research jobs | Report/scheduled jobs use a small runner of the same shape |
| Frontend | React/Vite, navigation model with `exam` and `reports` as `planned` placeholders | Replace placeholders, no second app |
| Sharing between users | **None.** Every repository read filters on `owner_user_id` | New access layer (below) |
| Quotas, maintenance mode, owner overview/monitoring | Quotas in governance; no maintenance, no overview | New |
| Export libraries in requirements | None of reportlab / python-docx / python-pptx / openpyxl / matplotlib | Optional `requirements-reports.txt`; a missing library gives a named, typed error per format |

## Architecture decisions
1. **One migration (0006), prefixed tables** (`reg_`, `exam_`, `adv_`, `rep_`, `ws_`, `sch_`, `own_`). No existing table is altered.
2. **Sharing without rewriting Phases 3–6:** `collab.access.AccessResolver` answers "may this person do X to resource R?" (owner, or an unexpired workspace membership plus a non-revoked grant capped by the role). Phase 7 services use it. Existing Phase 3–6 services keep owner-only reads; shared research and models are exposed through *Phase 7 read endpoints* that resolve access first, then read as the resource's owner. Documents, datasets and conversations are shareable by reference only (metadata); see limits.
3. **Evidence everywhere:** reports keep a version-independent `rep_evidence` table (the Report Evidence Bundle) and every paragraph carries claim→evidence ids. Editing re-checks the links and warns.
4. **No invented probabilities:** exam emphasis is a documented heuristic score; the word "probability" never appears in its output (tested).
5. **Delivery adapters are provider-independent** with a local adapter (writes to a delivery outbox directory) that works without credentials; email/Slack adapters refuse to send without configured credentials and say so.

## Requirement → code (filled in as built; final table in `PHASE7_REQUIREMENTS.md`)
Module 1 → `modules/intelligence/` · Module 2 → `modules/registry/` · Module 3 → `modules/exam/` · Modules 4–5 → `modules/reports/` · Module 5/6 collaboration → `modules/collab/` · Module 6 scheduling → `modules/schedule/` · Module 7 → `modules/dashboard/` · Module 8 → `modules/ownerops/` · API → `api/v1/phase7_*.py` · UI → `frontend/src/pages/{registry,exam,reports,workspaces,dashboard,owner}`.

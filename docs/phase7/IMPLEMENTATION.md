# Phase 7: implementation summary

## What was added (nothing from Phases 1–6 was rebuilt)
| Layer | Files |
|---|---|
| Migration | `backend/app/db/migrations/0006_phase7.sql` (create-only; prefixes `reg_ exam_ adv_ rep_ ws_ sch_ own_`) |
| Shared plumbing | `modules/p7.py` (ids, JSON, validation, audit, maintenance error), `modules/phase7.py` (`build_phase7`), `modules/collab/access.py` (`AccessResolver`) |
| Services (framework-free) | `registry/`, `intelligence/`, `exam/`, `reports/` (+`exports.py`, `citations.py`, `templates.py`), `collab/`, `schedule/`, `dashboard/`, `ownerops/` |
| API (thin routers) | `api/v1/registry.py reports.py exam.py intelligence.py workspaces.py schedules.py dashboard.py owner_ops.py`; `api/deps.py` gains `get_p7` and the maintenance guard in `require_principal` |
| Small changes to existing code | `workspace/quota.py` reads owner overrides; `identity/ratelimit.py` gains `p7_*` policies; navigation/routes; owner page tabs; `UsersTab` shows a usage summary |
| Scripts | `backend/scripts/run_schedules.py` |
| Frontend | `src/api/phase7.js`, `src/features/phase7/labels.js`, `src/pages/phase7/*`, `src/pages/owner/OwnerOpsTabs.jsx` |

## Design decisions
1. **One authorisation point for sharing.** `AccessResolver` answers "may this person do X to resource R?": the owner, or an active, unexpired workspace member with a non-revoked, unexpired grant, capped by their role (viewer → view only), where the resource owner's account is still active. No access → "not found". Phase 3–6 repositories keep filtering on `owner_user_id`; sharing is added in the Phase 7 services only, so no earlier code path became more permissive.
2. **Reports are assembled, not regenerated.** A report is built from stored results (bundles, runs, registry versions, exam analyses, scenario runs). Evidence lives in a version-independent register; each version holds claims that reference it.
3. **Honest numbers.** Exam emphasis is a documented heuristic; association results are labelled associations; the registry state is a record. Wording guards are tested.
4. **No hidden background work.** Scheduling is a script you run; monitoring reports only what it can measure.
5. **Owner separation.** Platform-owner permissions are separate from workspace roles; owner endpoints return metadata only.
6. **Free and open source only.** No new paid or hosted dependency; export libraries are optional.

## Spec order deviation
The spec's implementation order puts Exam and Intelligence before Reports. Reports and the Registry were built first because Exam, Intelligence, Collaboration and Scheduling all feed or consume reports; the end result is the same.

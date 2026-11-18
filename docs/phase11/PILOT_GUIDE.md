# Controlled pilot guide

**Status:** *no pilot with people has been run.* `PILOT_RESULTS.md` records **automated end-to-end scenario tests in the build sandbox**; they are evidence the workflows work, not evidence that real users can use them. This guide describes how to run the first restricted internal pilot on the Mac and what to record.

## Pilot configuration (restricted internal pilot)
| Setting | Value | Why |
|---|---|---|
| Environment | your Mac, `PREDICTAI_ENV=production`-like config **with** `PREDICTAI_SECRET_KEY` and https or localhost frontend URL | start-up refuses weak production config |
| Users | 3–10 named, trusted people; accounts you create or approve; no public sign-up link shared | no external exposure verified |
| Development Workspace | **`PREDICTAI_DEV_EXECUTION=off`** | untrusted multi-user execution is Blocked |
| Web research | off unless you have set up SearXNG (`docs/phase6/WEB_RESEARCH_SETUP.md`); then on for named users only | live providers unverified |
| AI provider | `none` or local Ollama | no paid dependency; fallbacks to quotes exist |
| Email | `file` backend (outbox) or your own SMTP test account | real delivery unverified |
| Backups | `backup create` before the pilot and daily; one restore drill in week 1 | `BACKUP_RESTORE.md` |
| Monitoring | external poll of `/health/ready` + owner snapshot twice a day | `MONITORING.md` |
| Data | **no regulated or highly sensitive data**; tell participants the system is pre-release | untested at scale and unaudited for CVEs |

Before inviting anyone: run `scripts/verify_phase10_live.py` and the accessibility procedure on the Mac; run `pip-audit`/`npm audit`; make the first backup and restore it.

## Scenarios (A–H) — who does what
| | Scenario | Participant task | Automated evidence (sandbox) | Needs a person / Mac |
|---|---|---|---|---|
| A | Document intelligence | upload 2 documents, search, ask a question, check each citation opens the right passage, delete | `PILOT_RESULTS.md` A | readability, scanned PDFs (unsupported), real model answers |
| B | Data analytics | upload a CSV and an Excel file, profile, ask the assistant, build charts, export | B | charts in a browser, XLSX files from real tools |
| C | Predictive | train on a dataset, read the evaluation and explanation, predict one row and a batch | C | training time on real data |
| D | Research | run a research question from documents (web off), then with web on if configured; export a report | D | live providers, answer quality judged by a human |
| E | Exam | upload a syllabus and two past papers, generate a blueprint | E | scanned papers (OCR) |
| F | Collaboration | share with a second person (viewer, then editor), revoke and confirm access ends | F | two real browsers/people |
| G | Development Workspace | **not piloted** — blocked | G (restricted path only) | requires verified isolation |
| H | Report delivery | schedule a report to the local outbox; simulate failure | H (test adapter) | real email / Slack |

## What to record per participant
Task, success (yes/partial/no), time taken, errors shown (code and request ID — never content), confusion points, accessibility barriers, and whether they would trust the answer without checking. Use `INCIDENT_RESPONSE.md` for anything SEV1–2.

## Stop criteria (pause the pilot and open an incident)
Any cross-user data visibility; a citation that does not match its source; data loss or a failed restore; repeated crashes in a core workflow; any suggestion that untrusted code ran outside its limits.

## Exit criteria for a *controlled external* pilot (suggested)
Restricted pilot completed with no SEV1/SEV2 open; accessibility audit finished and findings fixed or accepted; dependency audit clean or risks accepted in writing; restore drill passed on the Mac; real provider tests recorded; TLS reverse-proxy tested.

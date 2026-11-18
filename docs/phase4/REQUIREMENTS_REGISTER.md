# Phase 4 requirements register

Status: **Implemented+tested** = code and automated tests; **Partial** = implemented, with a stated gap.

| Requirement | Status | Evidence |
|---|---|---|
| A Descriptive statistics reproducible | Implemented+tested | engine tests vs independent values; rerun hash comparison |
| B Relationships expose evidence | Implemented+tested | correlation/discover ops, evidence panel, rows drill-down |
| C Tests report assumptions, effect size, CI, uncertainty; refuse invalid tests | Implemented+tested | hypothesis op tests; Tests tab |
| D Insights: 10 fields, inspect/save/dismiss/revisit, "Not enough evidence." | Implemented+tested | service tests, browser flow |
| E Assistant: typos, aliases, filters, clarification, 9-step flow, no paid API, no external data without consent | Implemented+tested | 29 assistant tests, browser flow. Free-text understanding is rule-based and limited to supported question types |
| E Optional AI explainer | Partial | tested with a fake provider only |
| F Five chart types + hover, sort, filter, variable select, range select, drill-down, trend overlay, data export | Implemented+tested | browser flow (hover covered by unit/DOM logic, not an automated pointer-hover assertion) |
| F Chart retains snapshot, columns, filters, date range, aggregation, method | Implemented+tested | stored run + evidence |
| G Evidence VERIFIED/INCOMPLETE, persisted, inspectable | Implemented+tested | evidence tests, panel |
| H Memory limit | Linux: hard (kernel, probed). macOS: monitored best-effort | `sandbox.py`, sandbox tests |
| H Typed plans, isolated execution, resource limits, deterministic seeds, no generated code, no duplicated logic | Implemented+tested | executor/plan tests, static check |
| Security: cross-user / cross-project | Implemented+tested | unit + browser 404 checks |
| Phase 1–3 intact | Tested | full backend suite; e2e 47/47 and Phase 3 flows |
| Real production build | **Not verified** | esbuild substitute; run `npm run build` |

Deferred (Phase 5+): predictive modelling, forecasting, anything beyond descriptive/inferential analytics.

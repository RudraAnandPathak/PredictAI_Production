# Phase 10B — Requirements & status
Scope: the PredictAI Brain (Gemini through Google AI Studio, on the existing provider layer) and Exam Prediction (past papers + curriculum + syllabus + web articles → ranked chapters and an estimated paper). No live request to Gemini or the web was made while building: everything was tested with fakes.

| ID | Requirement | Status | Tests |
|---|---|---|---|
| 10B-01 | Gemini is a provider preset on the existing OpenAI-compatible adapter; the key is read from `PREDICTAI_AI_KEY_GEMINI` at call time and never stored, logged or returned | Implemented, tested with a fake Gemini; live service unverified | test_p13_brain |
| 10B-02 | Owner-only setup that requires an explicit data-use acknowledgement, the remote-host platform flag and an unpaid provider | Implemented, tested | test_p13_brain |
| 10B-03 | Request-count caps (per day in the provider's quota timezone, per minute) enforced before every call and every retry; free-tier figures are the owner's to confirm in AI Studio | Implemented, tested; the real limits are unverified | test_p13_brain, test_p11_release |
| 10B-04 | Fallback order Gemini, then a local model, then the built-in rules; private data goes to Gemini only when the person allows external AI | Implemented, tested | test_p13_brain |
| 10B-05 | Migration 0013 is additive and keeps existing exam data (adds prediction table, three provider columns, the `ai_written` origin) | Implemented, tested | test_p13_exam_prediction |
| 10B-06 | Chapter ranking from the person's own papers: historical score plus a web bonus capped at 15; no web evidence never lowers a score | Implemented, tested | test_p13_exam_prediction |
| 10B-07 | Leave-one-paper-out backtest on the person's papers; "better than chance" only at lift 1.15 or more over 3 or more folds; otherwise said plainly | Implemented, tested | test_p13_exam_prediction |
| 10B-08 | Web evidence: needs web consent and a SearXNG provider; one voice per domain; syllabus-change text only flags a chapter; web text is treated as untrusted data | Implemented, tested with a fake search; real web unverified | test_p13_exam_prediction |
| 10B-09 | Predicted paper built through the existing blueprint and generate path; AI writes slots in batches, marks/type/chapter come from the plan, strict JSON checks, copy check against past questions | Implemented, tested with a fake Gemini | test_p13_exam_prediction |
| 10B-10 | Every AI-written question is labelled and raises a warning that must be acknowledged before approval | Implemented, tested | test_p13_exam_prediction |
| 10B-11 | UI: Prediction panel on the subject page, owner AI Brain tab, formatting and input checks | Implemented, tested (formatting logic only); never rendered in a browser | predict.test.js |

# Phase 10B: AI Brain (Gemini) and Exam Prediction

## What it is
- **AI Brain**: one status screen and one setup path for Gemini via Google AI Studio's OpenAI-compatible endpoint, built on the existing provider router (circuit breaker, retries, consent, usage ledger). Fallback: Gemini → local Ollama → built-in rules.
- **Exam Prediction**: from a syllabus/curriculum and past papers, ranks chapters, tests the ranking on your own papers, optionally reads exam articles from the web, and builds an *estimated* paper.

## Cost
₹0 if you stay inside the AI Studio free tier. PredictAI sets `paid=false`, caps requests (default 15/day, 4/minute, below the third-party-reported 20/5) and never enables a paid provider without `PREDICTAI_AI_ALLOW_PAID`. Check the real limits in your AI Studio rate-limit console.

> Updated in Phase 10C: external AI is on by default (a person can switch it off for themselves) and Gemini setup no longer needs the outside-host flag or a tick box. See PHASE10C.md.

## Setup (Gemini)
1. Create a key in Google AI Studio. Use a project with no billing attached if you want a hard ₹0.
2. On the server set `PREDICTAI_AI_KEY_GEMINI=<key>` and `PREDICTAI_AI_ALLOW_REMOTE_HOST=true`; restart the API. Never type the key into PredictAI.
3. Owner → AI Brain: choose a model (List models), set caps, tick the data notice, Save, then Send a test request.
4. Each person must have "allow external AI" on for their papers to be sent. The research assistant uses a separate switch (workspace AI data policy).

**Data notice**: on the free tier Google may use content to improve its products. Do not send material you must keep private.

## Exam prediction: how the ranking works
Score = Phase 7 heuristic (0–100) + web bonus (max 15). The backtest holds out each paper in turn and checks whether the top chapters contain what was actually asked; it needs 3+ papers. It is a heuristic ranking, **not a probability**. "Often asked but missing from the latest paper" is only noted. Web articles can add emphasis or flag a possible syllabus change; they never lower a score.

## API
- `GET /brain/status`, `POST /brain/setup/gemini` (owner), `GET /brain/models` (owner), `POST /brain/test` (owner)
- `GET /exam/subjects/{id}/prediction-options`, `POST|GET /exam/subjects/{id}/predictions`, `GET /exam/predictions/{id}`, `POST /exam/predictions/{id}/paper` (`ai`: `off|gaps|all`)

## Schema (migration 0013)
`ai_providers` + `daily_request_limit`, `requests_per_minute`, `quota_tz`; new `exam_predictions`; `exam_assessments.prediction_id`; `exam_assessment_questions.origin` accepts `ai_written`.

## Known limits
- Never run against live Gemini or the live web; model ids, the `/models` list shape and the free limits are unverified.
- Gemini "thinking" tokens may use part of `max_tokens`; no reasoning setting is sent.
- Web evidence needs web consent and a SearXNG provider.
- A standard account allows 3 documents (syllabus + 2 papers); more needs Special access or a raised owner setting.
- Gemini grounding/search is not used; PredictAI's own web search is.
- UI never rendered in a browser.

# Conversations

One place to ask PredictAI about **a dataset** or **a research workspace**, with saved history, search, rename, delete and Markdown export. Sidebar: **Conversations** (`/conversations`, `/conversations/:kind/:id`).

## What it is — and is not
It is a **hub over the two assistants that already existed**; it stores nothing of its own and adds **no migration**.

| Kind | Backed by | Answers |
|---|---|---|
| `data` | Analytics assistant (`conversations` / `conversation_messages`) | a question becomes a validated plan, the engine computes, an independent check verifies the figures; the message keeps its run id |
| `research` | Research Intelligence assistant (`rw_conversations` / `rw_messages`) | every sentence cites a stored passage; mode is stated: `model`, `extractive` (quotes only) or `insufficient_evidence` |

Every action is delegated to the owning assistant, so ownership checks, rate limits, the daily AI limit, verification and citation rules are the existing ones. Conversations started through the older per-module endpoints appear in the hub and can be continued there; deleting a dataset or a workspace still removes its conversations.

**Not included (deliberately):** a single chat that mixes several datasets/workspaces in one thread; sharing conversations with other people; streaming replies; editing or re-sending an earlier message; a general-knowledge chatbot. Answers are only about the dataset or workspace chosen. Without a language model (default) research answers are exact quotations; set an AI provider (e.g. local Ollama) for model-written answers, which are still checked sentence by sentence.

## API (`/api/v1/conversations`, session + CSRF like everything else)
| Route | Purpose |
|---|---|
| `GET /conversations?q=&kind=&limit=` | the person's conversations, newest first; `q` matches titles and message text (wildcards are literal) |
| `GET /conversations/targets` | datasets (ready, in active projects) and active research workspaces they can talk about |
| `POST /conversations` `{kind, target_id, title?}` | start one |
| `GET /conversations/{kind}/{id}` | messages; research answers carry `citations` (label, source, page, quote, `verified`) and `answer` (mode, provider, removed_unsupported) |
| `POST /conversations/{kind}/{id}/messages` `{text, source_ids?, use_ai?}` | ask; returns the assistant's result and the updated conversation |
| `PATCH /conversations/{kind}/{id}` `{title}` | rename (1–200 characters, control characters removed) |
| `DELETE /conversations/{kind}/{id}` | delete the chat only; datasets, sources, runs and notes stay |
| `GET /conversations/{kind}/{id}/export` | Markdown download, with a "Sources cited" section for research answers |

Someone else's conversation, a missing one and a foreign dataset/workspace all return the same 404. An unknown kind or bad input returns 422.

Also added to the analytics assistant: create, rename and delete of a dataset conversation (`AssistantService`).

## Tests (executed)
`backend/tests/test_p12_conversations.py` (10, HTTP through the real app stack on a fresh database): session/CSRF on every route; isolation for both kinds; start/ask/rename/export/delete for data; verified citations and export for research; old-style conversations visible and continuable; dataset and workspace deletion; merged list order, search by title and message, kind filter, literal wildcards; input rejection. `frontend/tests/conversations.test.js` (8): API paths/methods/CSRF/encoding, routes, grouping by day, question limits, honest answer notes, steps, start rules.

## Limits of the evidence
Run on the build sandbox (FastAPI stand-in, no browser, no live providers, no Ollama). **The page has been bundled and its logic tested but never rendered or clicked in a browser**, and its accessibility has not been audited beyond the contrast and structure checks (labelled fields, `role="log"` thread, `aria-current`, keyboard-operable buttons). Model-written answers were not exercised.

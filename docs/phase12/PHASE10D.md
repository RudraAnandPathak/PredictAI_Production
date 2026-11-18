# Phase 10D: Notebook Stages 2 and 3, import, retiring the older research areas

## Studio, complete list
| Output | Needs AI | Download |
|---|---|---|
| Briefing, study guide, FAQ, timeline | No (an honest extract if no AI answers) | Markdown |
| Flashcards | Yes | Markdown, CSV (front,back — imports into Anki) |
| Quiz (4 options, answer explained, scored in the page) | Yes | Markdown, CSV |
| Mind map (foldable, up to 3 levels, 80 nodes) | Yes | Markdown |
| Data table | Yes | Markdown, CSV |
| Slide deck | Yes | Markdown, PowerPoint (.pptx) |
| Infographic (headline figures and key points) | Yes | Markdown |
| Audio overview (two-host script, read aloud by your browser) | Yes | Markdown |
| Deep Research | Searches work without AI; AI plans better searches | Markdown |

### How structured outputs are checked
The model must return JSON in a fixed shape. The server drops items of the wrong type, trims text, removes source marks that do not exist, and does not keep flashcards, quiz questions, table rows or infographic figures that have no valid source mark. If too few usable items remain, nothing is saved and the reason is shown. Each kept item's marks open the passage it came from.

### Audio overview
The server writes the script; your browser reads it aloud with its built-in voices (Web Speech API). It costs nothing and nothing extra is uploaded, but the voices depend on the device and browser, and there is no audio file to download. A server-side speech engine was not added: none is installed, and adding one needs a decision about which free engine to use.

### Deep Research
1. The AI writes up to 3 search queries (your question is sent as public text, like any web search). Without an AI, the question itself is searched.
2. Each query goes to the discovery providers the owner enabled (SearXNG, Crossref, OpenAlex, arXiv, Semantic Scholar).
3. Results are merged, open-access ones first, then by relevance; up to 8 are added to the notebook (full text where a provider reported an open copy, otherwise the abstract).
4. Sources are read in the background; use the Studio once they are ready.

## Older research
- The menu no longer shows "Research" or "Research Intelligence". `/research` and `/research-intelligence` open the Notebook list; `/research-intelligence/<id>` opens that workspace as a notebook (a notebook *is* one of those workspaces).
- Reports from the older Research area: the Notebook list shows a "Copy them into notebooks" button. Each finished report becomes its own notebook ("Imported: <title>") with the report as a Markdown source. Running it again copies only new ones. Nothing in the older area is changed or deleted.
- The older pages (project research runs, reports, discovery, settings) still open by address and their API is unchanged, so nothing saved there becomes unviewable. They are no longer linked from the menu.

## API (new in 10D)
`GET /notebooks/outputs/{id}/export?format=markdown|csv|pptx`, `POST /notebooks/{id}/deep-research` (`question`, `max_sources` 1–8), `GET /notebooks/legacy`, `POST /notebooks/legacy/import`. `POST /notebooks/{id}/studio` now also accepts `flashcards`, `quiz`, `mind_map`, `data_table`, `slides`, `infographic`, `audio_script`.

## Schema (migration 0015)
New `nb_imports (session_id PK, owner_user_id, workspace_id, created_at)`. Structured output data is stored in `nb_outputs.meta.data`.

## Limits
- Never run against live Gemini, live search providers or a browser; the JSX was not compiled with the real build tool here.
- Every Studio output and every Deep Research plan uses one Gemini request; the default cap is 15 a day.
- PowerPoint export needs `python-pptx` (already in `requirements-reports.txt`); without it the server says so and Markdown still works.
- Video overview is not built.
- Rate limits: 30 Studio outputs per hour, 10 Deep Research runs per hour, 5 imports per hour per person.

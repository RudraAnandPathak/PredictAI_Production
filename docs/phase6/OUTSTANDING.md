# Outstanding requirements register (carried to later phases)

Nothing here is implemented in Phase 6 and none of it is claimed. Each item keeps its owner phase from the Master Engineering Documentation roadmap.

## Phase 7 (named by the Phase 6 specification)
| ID | Requirement | State after Phase 6 | What Phase 6 prepared |
|---|---|---|---|
| OUT-01 | **Exam Intelligence**: complete syllabus analysis, past-paper analysis, topic recurrence and weight analysis, MCQ / short-answer / long-answer generation, mock examinations, answer keys, marking schemes, assessment and evaluation | **Missing** | `research/interfaces.py` topic-research interface (curriculum context, source-grounded explanations, educational evidence retrieval); `purpose="education"` sessions |
| OUT-02 | **Model Registry**: model metadata, experiment records, versions, training-dataset lineage, evaluation results, explanations, deployment state, rollback | **Missing** (read interfaces only) | `predictive/registry_view.py` returns existing metadata, versions, lineage, evaluation; deployment state and rollback are reported as "not supported" |

## Other items from earlier phases that remain open
| ID | Requirement | Owner phase |
|---|---|---|
| OUT-03 | Reports and exports using evidence bundles (templates, schedules, sharing) | Phase 7 (Reports & collaboration) |
| OUT-04 | General conversation module (`/conversations`) across all areas; Phase 6 provides follow-up questions inside a research session only | Phase 7 |
| OUT-05 | Real SHAP explanations | later (needs an approved dependency) |
| OUT-06 | Neural sentence embeddings bundled with the app (today: optional local provider) | later, needs approved dependency |
| OUT-07 | Typed entity relations, multilingual news, article full-text licensing review | later |
| OUT-08 | Platform-wide quality and production-readiness audit | after Phase 12 (per the Phase 6 specification) |
| OUT-09 | Background `embed_index` job: dense embeddings are built inside the request with a passage cap | Phase 7 or later |
| OUT-10 | Screens for document comparison and retrieval evaluation (the API exists; the document search screen does) | Phase 7 |
| OUT-11 | Verification that could not be done in the Phase 6 environment: production `vite build`, the real FastAPI package, macOS with Python 3.14, and any contact with real search sites, feeds or AI providers (all were tested against local fakes) | First step of Phase 7, on the owner's machine and CI |
| OUT-12 | Measure topic, entity, event-grouping and tone quality on real news (all four are heuristics with unmeasured accuracy) | later |

## Carried from the corrective release (research engine, web search, datasets, Wikipedia and arXiv)
Nothing here is claimed. These are the honest limits of what was built and checked.

| ID | Item | State |
|---|---|---|
| OUT-13 | Live check of Wikipedia, arXiv and a real SearXNG container (the corrective tests use recorded response shapes and local stand-ins; no external service was reachable) | Open. First step on the owner's machine: **Research → project → Search tools → Test now** |
| OUT-14 | The real `fastapi` package, `vite build` and macOS with Python 3.14 (same gap as OUT-11; the corrective browser runs used a stand-in FastAPI and an esbuild bundle) | Open |
| OUT-15 | PDF papers: no maths markup (equations are the text layer's approximation) and multi-column pages can interleave lines; verified on small generated PDFs only, not on real papers | Open. arXiv's HTML version is preferred when it exists |
| OUT-16 | The arXiv request gate is per process; arXiv's terms count requests across all of a client's machines | Open. Run research on one server process |
| OUT-17 | The comparison of a calculated finding with an outside passage matches words, not meaning; conflict detection is by wording and numbers | Open. Both are labelled so in the product |
| OUT-18 | A column is recognised in a question by its full name; synonyms and abbreviations are not matched (for example "seats" does not match `seats_available`) | Open |
| OUT-19 | A follow-up with web research off can only use the sections the earlier question read (saved text holds only the sections used) | Open by design; switch web research on to read more |
| OUT-20 | General web search needs a SearXNG the owner runs; there is no fallback general search provider (Wikipedia and arXiv keep working without it) | Open by design: no paid or key-based search service may be added without approval |
| OUT-21 | Answers are quoted passages and fixed-template sentences chosen by ranked matching. They are accurate and cited but plain; an optional AI wording exists and is checked sentence by sentence | Open |
| OUT-22 | Research answer quality has not been measured on real questions against a reference set | Open |

| OUT-23 | Intelligence upgrade: run `scripts/live_research_check.py` and the new tests against the live providers and the real `french rev.pdf`; relevance rules were tuned on a modelled corpus | Open. First step on your machine |
| OUT-24 | Explanations are extracted sentences in fixed templates; a chosen sentence can depend on the one before it (shown as context, not rewritten) | Open |
| OUT-25 | New screens (clarification prompt, arXiv mode, explanation view) have unit-tested view-models only: no browser run, no real Vite build | Open |
| OUT-26 | PDFs uploaded before this change keep their old extracted text until re-uploaded | Open by design |

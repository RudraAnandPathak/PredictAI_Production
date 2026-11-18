# Critical Research Intelligence Upgrade

**Not production-ready.** Everything below was run offline against modelled providers and a stand-in PDF. Nothing here was run against live Wikipedia, arXiv, SearXNG or an AI provider, against the real `fastapi` package, a real `vite build`, macOS/Python 3.14, or your real `french rev.pdf`.

## What went wrong before

"How was France under Louis XVI?" returned Louis XVIII, CoPt / Weil-Petersson physics papers, a St. Louis article and half a sentence from a PDF. Causes: sources were ranked by word overlap, there was no notion of *who* the question is about, arXiv was searched for any question, and PDF text was cut by line and page.

## What changed (nothing removed; no migration; no new dependency)

| Spec | Change | Where (`backend/app/modules/…`) |
|---|---|---|
| §1 | Entity identity: Louis XVI = Louis 16 = Louis the Sixteenth; XVIII / XVII / XIV are different people; demonyms imply a place; source names (Wikipedia…) are not subjects | `research/understanding.py`, `research/relevance.py` |
| §2 | Task-aware outlines (overview, causes, consequences, comparison, statistics, methodology). Comparison gets one part per entity plus a shared one. Optional `ai_plan`: a model may only suggest ≤3 validated extra parts, shown for approval, never queries. Unclear questions ("this paper" with no file) are held with a question (HTTP 409 `NEEDS_CLARIFICATION`) unless `proceed_anyway` | `research/outlines.py`, `research/planner.py`, `research/service.py` |
| §3 | Every candidate is judged on identity, kind, topic and place/period; rejected ones are kept with a reason | `research/relevance.py`, `research/collector.py`, `research/quality.py` |
| §4 | Wikipedia exact-title/alias lookup; sections chosen per part; citations carry the section | `webaccess/providers/wikipedia.py`, `research/queryplan.py` |
| §5 | `arxiv_mode` auto/always/never; auto skips arXiv for history with a stated reason; "no relevant results" ≠ "failed" | `research/queryplan.py`, `research/collector.py` |
| §6 | HTTP ≥500 from SearXNG is `unavailable` (it was mislabelled as a rate limit); result is `partial` with a providers warning | `webaccess/providers/searxng.py`, `research/quality.py` |
| §7 | Running headers/footers/page numbers removed, wrapped lines joined, sentences cut by a page break completed and cited to the page they start on; arXiv PDFs keep line structure | `workspace/parsing/pdfclean.py`, `documents.py`, `retrieval/chunker.py` |
| §8 | `report.explanation`: direct answer per part, side-by-side for comparisons, connections the sources state themselves, immediate vs long-term (causes), and an explicit "not known" list. Every line is a quotation with its place | `research/synthesis.py`, `research/report.py` |
| UI | Clarification prompt, arXiv mode, AI-planning switch, "not searched" reasons, provider statuses, set-aside results, explanation view | `frontend/src/pages/research/*`, `features/research/plan.js`, `report.js` |

Existing tests changed because behaviour changed on purpose: `test_research_queries.py` and `test_research_combined.py` now pass `arxiv_mode="always"` where they test arXiv on non-scholarly questions, and the plan's `web` object has two new keys (`arxiv_mode`, `skipped_providers`).

## Tests

New: `backend/tests/test_research_intelligence.py` (A–F + real-socket SearXNG, 27 tests), `test_research_units.py` (16), `frontend/tests/research-intelligence.test.js` (5), requirement rows IU-01…IU-10 in `CORRECTIVE_REQUIREMENTS.md`. Fixtures: `tests/history_corpus.py`, `tests/history_support.py`, `tests/fixtures/arxiv_html_methods.html`. Full results: `TESTING.md`.

## Answers the tests produce (modelled sources)

- **A. How was France under Louis XVI?** Sources: your PDF, Wikipedia "Louis XVI", one web page. Set aside with reasons: Louis XVIII, XVII, XIV, Louis XVI style/furniture, DTF St. Louis, Louis Vuitton. arXiv skipped ("this question is about history"). Five parts answered with whole sentences cited to `french rev.pdf, p. 4 › Chapter 3…` or `Louis XVI › Finances and taxation`.
- **B. What caused the French Revolution?** Political, economic, social, intellectual causes and immediate triggers; Estates General / Tennis Court Oath / Bastille listed as immediate, the rest long-term.
- **C. Compare Louis XVI and Louis XVIII.** Separate parts; the Louis XVI side holds only Louis XVI statements (reign from 1774), the Louis XVIII side only 1755/1814/1815/1824; no merged biography.
- **D. Economic condition before the Revolution.** Debt, taxation and bread-price evidence from the document and the web; the arXiv economics paper is not used because arXiv is skipped on a history question (choose "Always" to include it).
- **E. Methodology of arXiv:2101.00001v2.** Paper read in full at v2; evidence from *Encoder and Decoder Stacks* and *Training Data and Batching*; problem and evaluation parts reported as "nothing relevant found".
- **F. SearXNG down (503).** Result `partial`; SearXNG shown as unavailable, no web page claimed, Wikipedia and the document still contribute.

## Known limitations

1. Answers are extracted sentences plus fixed-template wording, so they can read as a list of facts rather than an essay. One sentence per part is chosen; it can lean on the sentence before it (in A, the finance line ends "damaged that belief"). The preceding sentence is stored as context but is not woven into the text.
2. Relevance rules were tuned on the modelled corpus; real Wikipedia/web pages will expose cases not covered. Entity rules cover regnal names, demonyms and capitalised names; other ambiguity (two people with the same name) is not detected.
3. Very terse sentences carrying a single topic word are not used as evidence (two topic words or a matching section heading are needed).
4. A PDF uploaded before this change keeps its old extracted text until it is uploaded again.
5. Multi-column PDFs and equations are only as good as the PDF's text layer.
6. `ai_plan` and AI wording were tested with a stand-in model only.
7. The new screens have unit-tested view-models but no browser run and no real Vite build.
8. The explanation does not count a statement as confirmed unless two sources say nearly the same thing.

## Install and check on your Mac

```
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m app.cli migrate                      # no new migration; safe to re-run
PYTHONPATH=.:tests python -m unittest test_research_intelligence test_research_units
cd ../frontend && npm install && npm test && npm run build
cd ../backend && python ../scripts/live_research_check.py   # real Wikipedia / arXiv / SearXNG
```

`live_research_check.py` prints OK / UNAVAILABLE / WRONG per provider (exit 1 = wrong answer, 2 = unreachable). Run it first; if it reports WRONG, the modelled tests no longer represent the real services. To use SearXNG: `docker compose up -d` in `docker/searxng`, set `PREDICTAI_SEARXNG_URL=http://127.0.0.1:8080`, enable JSON format in its `settings.yml`. Re-upload `french rev.pdf` so it is re-extracted.

# Phase 10C — Requirements & status
Scope: one NotebookLM-style "Notebook" (Stage 1), external AI on by default with a per-person off switch, and task-based routing between a local model and Gemini. Stages 2 and 3 (flashcards, quiz, mind map, data table, slides, infographic, Deep Research, audio) are NOT built. Nothing was run against live Gemini.

| ID | Requirement | Status | Tests |
|---|---|---|---|
| 10C-01 | A notebook is a Research Intelligence workspace: sources, cited chat, notes and passages are reused, not duplicated | Implemented, tested | test_p14_notebook, test_research_intelligence |
| 10C-02 | Studio outputs (briefing, study guide, FAQ, timeline) written only from the notebook's ready, included sources; every statement carries a source mark | Implemented, tested with a fake Gemini; live service unverified | test_p14_notebook |
| 10C-03 | Source marks the model invents are removed; marks that remain open the stored passage; lines with no mark are counted and flagged | Implemented, tested | test_p14_notebook |
| 10C-04 | Source text is sent as untrusted data inside source blocks | Implemented, tested | test_p14_notebook |
| 10C-05 | When no AI answers (switched off, capped, failed) the output is an honest extract that says so, with real citations | Implemented, tested | test_p14_notebook |
| 10C-06 | External AI is ON by default; a person's own switch turns it off for that person only; a notebook set to local-only can narrow it further | Implemented, tested | test_p13_brain, test_p14_notebook |
| 10C-07 | Gemini setup needs only the key in the server environment (no outside-host flag, no tick box) | Implemented, tested | test_p13_brain |
| 10C-08 | Bulk tasks (classify, extract, embed) try the local model first; complex tasks try Gemini first; configurable with PREDICTAI_AI_LOCAL_FIRST_TASKS | Implemented, tested | test_p14_notebook |
| 10C-09 | Migration 0014 is additive (nb_outputs only) | Implemented, tested | test_p14_notebook, test_p11_release |
| 10C-10 | Notebook screens: list, three-pane notebook with Studio, clickable citations | Implemented, tested (formatting logic only); never rendered in a browser | notebook.test.js |
| 10C-11 | Move old Research and Research Intelligence data into notebooks | Done in Phase 10D (see PHASE10D_REQUIREMENTS.md: 10D-07, 10D-10) | test_p15_notebook_studio |
| 10C-12 | Stage 2: flashcards, quiz, mind map, data table, slide deck, infographic page | Done in Phase 10D (10D-01 to 10D-04) | test_p15_notebook_studio |
| 10C-13 | Stage 3: Deep Research, audio overview | Done in Phase 10D (10D-05, 10D-06); audio playback unverified in a browser | test_p15_notebook_studio |

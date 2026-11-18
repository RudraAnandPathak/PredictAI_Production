# Phase 10D — Requirements & status
Scope: Notebook Stages 2 and 3, the import of older research reports, and retiring the two older research areas from the menu. Nothing was run against live Gemini, live search providers or a browser.

| ID | Requirement | Status | Tests |
|---|---|---|---|
| 10D-01 | Studio: flashcards, quiz, mind map, data table, slide deck, infographic and audio-overview script, asked for as JSON in a fixed shape | Implemented, tested with a fake Gemini; live service unverified | test_p15_notebook_studio |
| 10D-02 | Every structured item is checked: wrong types dropped, text trimmed, invented source marks removed; flashcards, quiz questions, table rows and infographic numbers without a valid source are not kept | Implemented, tested | test_p15_notebook_studio |
| 10D-03 | A reply that is not usable JSON, or has too few usable items, saves nothing and says why; structured outputs never fall back to guessing when no AI answers | Implemented, tested | test_p15_notebook_studio |
| 10D-04 | Downloads: Markdown with a source list (any output), CSV with formula cells neutralised (table, flashcards, quiz), PowerPoint with sources in the speaker notes (slides) | Implemented, tested | test_p15_notebook_studio |
| 10D-05 | Audio overview read aloud by the browser's own speech voices (two hosts, two voices where available); no audio file and no server-side speech engine | Implemented; playback never tested in a browser | notebook.test.js |
| 10D-06 | Deep Research: AI-planned searches (question sent as public text), the platform's discovery providers, open-access results first, duplicates merged, best results added as notebook sources; failures and skips reported | Implemented, tested with a fake discovery service; live providers unverified | test_p15_notebook_studio |
| 10D-07 | Import older Research reports: each finished report becomes its own notebook with the report as a Markdown source; never copied twice; originals unchanged; failures listed | Implemented, tested with a fake older-research service | test_p15_notebook_studio |
| 10D-08 | Migration 0015 is additive (nb_imports only) | Implemented, tested | test_p15_notebook_studio, test_p11_release |
| 10D-09 | Notebook routes need a session, and changes need CSRF | Implemented, tested | test_p15_notebook_api |
| 10D-10 | Menu: "Research" and "Research Intelligence" removed; their start pages open Notebook; an old workspace address opens it as a notebook; other old pages stay reachable by address so saved work stays viewable | Implemented, tested (navigation logic); never rendered in a browser | navigation.test.js |
| 10D-11 | Video overview | NOT built (not feasible at ₹0 on a small server) | — |

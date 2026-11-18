# Phase 10C: Notebook (Stage 1), default-on external AI, task routing

## Notebook
Open **Notebook** in the sidebar. Create a notebook, add sources (files or web addresses), ask questions (answers cite stored passages), and use the **Studio** to make a briefing, study guide, FAQ or timeline. Each `[S1]`-style mark opens the passage it came from.
A notebook is a Research Intelligence workspace, so everything there (sources, chat, notes, findings) is the same data. The older "Research" and "Research Intelligence" entries are still in the menu, marked "(older)", and are unchanged.

### How an output is made
1. The server takes the notebook's ready, included sources and picks up to 60 passages, an equal share per source, spread across each source.
2. The Brain writes the output through the router (Gemini first for this task, a local model next, then an extract).
3. Marks the model invented are removed; lines without a mark are counted and flagged; the output says who wrote it.
4. If no AI answers, the output is an extract of real passages with citations, and says so.

## External AI: the rule now
- ON by default (`PREDICTAI_AI_EXTERNAL_DEFAULT=true`; the owner can set it to false).
- A person turns it OFF in their own research settings; this affects only that person.
- A notebook set to "local only" narrows it further for that notebook.
- Passwords, keys and access codes are never put in prompts.
- On Google's free tier, Google may use the text to improve its products. Switch it off to keep a notebook local.

## Gemini setup is now one step
Put the key in `PREDICTAI_AI_KEY_GEMINI` on the server, restart, and use Owner → AI Brain → Save. No outside-host flag and no tick box are needed for the fixed Gemini address.

## Task routing
`PREDICTAI_AI_LOCAL_FIRST_TASKS` (default `classify,extract,embed`) lists tasks that try a local model first. Others keep the provider priority (Gemini 20, local 50). When Gemini is capped or fails, the next provider answers.

## API
`GET /notebooks/studio/kinds`, `POST|GET /notebooks/{id}/studio`, `GET|DELETE /notebooks/outputs/{output_id}`. Sources, chat and notes use `/research-intelligence/workspaces/{id}/...`.

## Schema (migration 0014)
New `nb_outputs` (id, workspace_id, owner, kind, title, instructions, content, citations, meta, source_ids, mode, provider, model, created_at). Nothing else changes.

> Phase 10D built the remaining Studio outputs, Deep Research, the import and retired the older menu entries. See PHASE10D.md.

## Not built / limits
- Flashcards, quiz, mind map, data table, slides, infographic, Deep Research and audio are not built.
- Old Research and Research Intelligence data is not migrated into notebooks.
- Never run against live Gemini; the free-tier request cap (default 15/day) limits how many outputs and chat answers you get per day.
- Screens never rendered in a browser; the page was not compiled with the real build tool here.
- The local model (Ollama) must be reachable from the server; a hosted server cannot reach one on your Mac without a tunnel.

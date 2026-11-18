# Setting up web research (Wikipedia, arXiv and general web search)

Web research is **off** until the platform owner, you and the project each switch it on. This guide is the one place that says how, and what to do when it does not work.

| Source | What it needs | Notes |
|---|---|---|
| Wikipedia | the server switch (and a contact address, strongly recommended) | Official MediaWiki API. An encyclopedia (a tertiary source), always labelled so. |
| arXiv | the server switch (and a contact address, strongly recommended) | Official arXiv API. Papers are **preprints**, always labelled so, unless peer review is independently verified. |
| General web search | the server switch **and a SearXNG you run** (plus the contact address) | SearXNG is free and open source. There is no key and no paid service. |

Nothing from your documents or datasets is ever put in a search. Only short topic phrases made from the question are sent, and you see the exact phrases for each provider before anything runs.

## 1. Server settings (platform owner)

In the project's `.env` (copy `.env.example` first; never commit `.env`):

```
PREDICTAI_RESEARCH_WEB_ENABLED=true
PREDICTAI_RESEARCH_CONTACT=you@example.com          # an address or URL; sent in the User-Agent
PREDICTAI_SEARXNG_URL=http://127.0.0.1:8080         # only for general web search
```

Why the contact matters: Wikimedia and arXiv ask automated clients to identify themselves and may refuse requests that do not. The contact is not required for PredictAI to start, but without it a provider may refuse the request; the message then names this setting.

Restart the backend after changing `.env`.

## 2. Start a SearXNG (only for general web search)

```bash
cd docker/searxng
cp .env.example .env            # then put a random value after SEARXNG_SECRET=   (macOS/Linux: openssl rand -hex 32)
docker compose up -d
curl 'http://127.0.0.1:8080/search?q=test&format=json' | head -c 300
```

The `curl` must print JSON beginning `{"query":"test"`. If it prints an error page, `settings.yml` is not the file mounted into the container.

The shipped `docker/searxng/settings.yml` changes only what PredictAI needs. A **stock SearXNG answers 403 to every JSON request** and its bot limiter answers 429 to automated calls; PredictAI used to report both as "blocked", which was the cause of "web search is broken". The container listens on `127.0.0.1` only because its limiter is off: do not expose port 8080 to a network. Details: `docker/searxng/README.md`.

Without Docker, any SearXNG you run works if its `settings.yml` lists `json` under `search.formats`.

## 3. Switch it on for yourself and the project

1. **Research → Change settings** (`/research/settings`): tick **Allow web research** and the search sites you want.
2. In the project, **Privacy for this project** can only narrow what you allowed, never widen it.
3. **New research**: choose a question, optionally datasets and documents, and tick **Also search the web for this question** if you want outside sources. The plan lists the exact phrases each provider would be sent. Run only after you approve them.

## 4. Check that it works

Open **Research**, choose a project, then **Search tools → Test now**. Each switched-on provider is tested for real and reported as working, or with the reason and the fix:

| Message | What to do |
|---|---|
| 403 from SearXNG | `settings.yml` lacks `json` under `search.formats`, or it is not the mounted file |
| 429 from SearXNG | `server.limiter` is on; use the shipped `settings.yml` |
| connection refused | the container is not running: `docker compose ps`, `docker compose logs` |
| "engines did not answer" | SearXNG is fine, but the engines behind it are blocking your network for now; wait, or enable other engines in `settings.yml` |
| Wikipedia / arXiv refused | set `PREDICTAI_RESEARCH_CONTACT`; arXiv pauses all requests for a while after a refusal (its terms treat continued requests as abuse) |

The same page lets you search Wikipedia, arXiv or the web on their own and open one result section by section. A result that opens shows whether you are reading the whole article or paper, or only an abstract.

## 5. Limits you should know

- **arXiv is rate-limited by its terms**: one request at a time, three seconds apart (fifteen for the website pages). PredictAI enforces this inside one server process. If you run several server processes, run research on only one.
- **Papers**: arXiv's HTML version is used when it exists (equations come out as TeX). Otherwise the PDF is read; a PDF has no maths markup, so equations are the text layer's approximation and multi-column pages can interleave lines. A scanned PDF with no text is reported as such, never filled in.
- **Follow-up questions** reuse the saved text of the sections an earlier question read. With web research off, a follow-up can only use those sections; switch it on to read more.
- **Datasets** are analysed on the server by PredictAI's own analysis and model tools. A column is recognised in a question by its full name.
- The comparison between your data and an outside source matches **words, not meaning**. Read both passages before drawing a conclusion. An association is never presented as a cause.

## 6. Running the checks yourself

```bash
cd backend  && python -m unittest discover -s tests                      # every test uses local fakes; none contacts a real service
cd frontend && npm test
# optional browser run against local fixtures: scripts/e2e/README.md
```

No automated test depends on Wikipedia, arXiv or a SearXNG being reachable. A live check is only the **Test now** button above, which you run yourself.

# SearXNG for PredictAI

PredictAI's general web research uses a SearXNG instance that **you** run (free, open source, no key). Wikipedia and arXiv need nothing.

## Start it

```bash
cd docker/searxng
cp .env.example .env
# put a random secret after SEARXNG_SECRET=   (macOS/Linux:  openssl rand -hex 32)
docker compose up -d
curl 'http://127.0.0.1:8080/search?q=test&format=json' | head -c 300
```

The `curl` must print JSON beginning `{"query":"test"`. Then in PredictAI's `.env`:

```
PREDICTAI_SEARXNG_URL=http://127.0.0.1:8080
PREDICTAI_RESEARCH_CONTACT=you@example.com
```

Restart the backend. In the app, open **Research**, choose a project, then **Search tools → Test now**: it runs a real test of each switched-on source and says what to fix. (Web research must first be allowed: see `docs/phase6/WEB_RESEARCH_SETUP.md`.)

## What the settings change (and why)

| Setting | Why |
|---|---|
| `search.formats: [html, json]` | A stock SearXNG answers **403** to `format=json`. This was the cause of "web search is broken". |
| `server.limiter: false` | The bot limiter answers **429** to API-style calls. It is for public instances; this one listens on 127.0.0.1 only. |
| `SEARXNG_SECRET` | SearXNG will not start with its published default secret. |

## Troubleshooting

* `403` in the check → your `settings.yml` is not the one mounted, or lacks `json` under `search.formats`.
* `429` → `server.limiter` is on.
* connection refused → the container is not running: `docker compose ps`, `docker compose logs`.
* "engines did not answer" → SearXNG is fine but the search engines behind it (Google, DuckDuckGo, ...) are blocking automated queries from your network at the moment. Wait, or enable other engines in `settings.yml` (see the SearXNG documentation). Wikipedia and arXiv research keeps working meanwhile.
* Do **not** expose port 8080 to the internet or a shared network: the limiter is off.

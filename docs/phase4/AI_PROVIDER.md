# Optional AI explanation provider

Default: **none**. The assistant needs no paid API and sends nothing anywhere.

- `PREDICTAI_AI_PROVIDER=ollama` enables a local Ollama server (`PREDICTAI_AI_BASE_URL`, default `http://127.0.0.1:11434`,
  `PREDICTAI_AI_MODEL`, `PREDICTAI_AI_TIMEOUT_SECONDS`). A non-local host is refused unless `PREDICTAI_AI_ALLOW_REMOTE_HOST=true`.
- Even when configured, the person must tick "reword with AI" for each request (`use_ai`). Only the question and the already
  verified figures are sent, never rows.
- The model's text is accepted only if every number in it is among the verified numbers and it contains no causal claim;
  otherwise the standard template answer is shown with the reason.
- Verified here with a fake provider only; a live Ollama was not available in the build environment.

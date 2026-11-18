# Deferred infrastructure and policy decisions

None of these has been decided or built. Each needs an explicit owner decision; the recommendation is mine, not a commitment. No paid service is assumed anywhere.

| # | Decision | Why it is open | Options | Recommendation |
|---|---|---|---|---|
| 1 | **Isolation for untrusted code** | Only Linux-namespace confinement exists and is not a boundary against hostile code | Docker Desktop with `--network none`, read-only root, non-root, cgroup limits (adapter exists, unverified); gVisor/Firecracker; no untrusted execution | Keep disabled. Verify the Docker adapter on the Mac with the `test_p9_security` suite *run against it* and an escape test before any multi-user use |
| 2 | Production hosting | Brief to host publicly on Cloudflare-style free tiers vs the SQLite single-process design | Keep single-host; Cloudflare Tunnel in front of a Mac/VM; D1/R2 re-platform (large change) | Pilot behind a tunnel or on localhost first; do not re-platform before measuring |
| 3 | Database engine | SQLite is single-writer | Stay on SQLite + WAL for ≤ ~10 users; Postgres later | Stay; decide after load tests on the real server |
| 4 | Job queue | In-process queues lose in-flight state on restart | Keep; add a persistent worker process; external broker | Keep for pilot; add a worker process only if jobs outlive restarts matter |
| 5 | Backup destination and encryption | Tool writes to a local directory, unencrypted | Encrypted disk image; `age`/`gpg` wrap; off-site copy | Encrypted image + second copy; scheduled via launchd/cron |
| 6 | Monitoring/alerting | None built | External uptime poll; Prometheus/Grafana; log shipping | Start with an external poll of `/health/ready` and a daily backup-age check |
| 7 | TLS, headers, rate limiting at the edge | Not testable here | Reverse proxy (Caddy/nginx/Tunnel) | Terminate TLS at the proxy; set `PREDICTAI_TRUSTED_PROXY_COUNT` correctly |
| 8 | Release versioning and commit tracking | version `0.1.0`, no git repository | Initialise git; tag releases; SemVer | Initialise a repository now so manifests carry a commit |
| 9 | Dependency audit cadence | No network here | `pip-audit`/`npm audit` in CI weekly | Run before pilot; add to CI |
| 10 | Real AI/search providers in the pilot | Quality and privacy unverified live | local Ollama; SearXNG; paid APIs (not authorised) | Local Ollama + local SearXNG only |
| 11 | Frontend code splitting | 264 KB gzip single bundle | Route-level `React.lazy` | Do it when a browser performance pass is possible |
| 12 | Accessibility conformance target | Not stated | WCAG 2.1 AA vs 2.2 AA | 2.1 AA, after the browser audit |

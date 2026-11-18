# Recovery and troubleshooting

| You see | Meaning | Do |
|---|---|---|
| Job stays "Waiting in the queue" | No worker running | `python backend/scripts/run_dev_worker.py` |
| "Running code is switched off" | `PREDICTAI_DEV_EXECUTION=off` | Set `local_trusted` (own machine) and restart the API and worker |
| Job "Blocked: ... cannot isolate network" | Neither `unshare` nor `sandbox-exec` works here | Use Docker mode (after verifying) or leave execution off |
| Job "Blocked/Cancelled: Special access was removed" | Entitlement revoked | Files are kept; access returning reactivates the workspace |
| Job failed `memory_limit` / `wall_clock` / `output_limit` | Limit reached | Reduce the work or ask the owner to raise the limit |
| Env build failed | Package not available as a wheel from allowed sources | Pin a version that has a wheel, or use a wheelhouse |
| Session "not available" | Mode is not local_trusted or Jupyter/code-server is not installed | Install it or use notebook jobs |
| Git "consent required" / "protected branch" | Safety rules | Grant consent; push another branch |
| Worker died mid-job | | Next worker tick recovers the stale job and queues a bounded retry; exhausted retries are dead-lettered |
| `SECRETS_UNAVAILABLE` | `cryptography` not installed | `pip install -r backend/requirements-devspace.txt` |

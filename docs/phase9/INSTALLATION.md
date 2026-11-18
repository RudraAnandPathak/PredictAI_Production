# Installation (macOS, local, free)

1. Backend as before (`pip install -r backend/requirements.txt`), then `pip install -r backend/requirements-devspace.txt` (`cryptography`).
2. Copy `config/env.example` to `.env`; to run code on **your own machine**: `PREDICTAI_DEV_EXECUTION=local_trusted`. Restart the API.
3. Start the worker in a second terminal: `python backend/scripts/run_dev_worker.py`.
4. Optional: `pip install jupyterlab` and/or `code-server` for interactive sessions; `pip-audit` for dependency audits.
5. Grant yourself Special access (owner account or an access code), open **Development** in the sidebar.
6. **On your Mac, verify before relying on it:** run a job that tries a network connection (must fail) via `sandbox-exec`; if it does not, leave execution `off`. For Docker mode, install Docker Desktop, set `PREDICTAI_DEV_EXECUTION=docker`, and run the same network and filesystem probes (the tests in `tests/test_p9_security.py` show what to check).
Hosted deployments: `local_trusted` is refused; leave execution `off` unless Docker isolation has been verified by you.

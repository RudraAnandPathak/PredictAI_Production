# Notebooks, JupyterLab and the browser IDE

## What always works (any mode with execution on)
* **Notebook files** (`.ipynb`) are ordinary project files: create, edit as JSON, version, restore.
* **Notebook jobs**: submit `kind=notebook`; the worker runs the code cells in order in one namespace inside the isolation provider and writes outputs back as a new file version. IPython magics (`%`, `!`) are refused with a clear error; this is a headless runner, not Jupyter.
* **Export**: `.ipynb`, `.py`, `.html`. PDF is reported unavailable (501), not faked.

## Interactive JupyterLab and browser IDE: optional, local_trusted only
Offered only when `PREDICTAI_DEV_EXECUTION=local_trusted` **and** the program exists (`jupyter-lab` on PATH or `PREDICTAI_DEV_JUPYTER_COMMAND`; `code-server` or `PREDICTAI_DEV_IDE_COMMAND`). They run **unsandboxed** (they need a network port and the user's filesystem), bound to 127.0.0.1 with a random token, stopped after `PREDICTAI_DEV_IDLE_MINUTES`, limited by `PREDICTAI_DEV_MAX_SESSIONS`, and stopped on revocation or deletion. In `off` or `docker` mode, or when the program is missing, the page shows `SESSION_UNAVAILABLE` with the reason. **Neither server was run in the build environment (UNVERIFIED);** the availability logic, limits, start refusal and stop paths are tested.
Git credentials and secrets are never placed in a notebook or its environment.

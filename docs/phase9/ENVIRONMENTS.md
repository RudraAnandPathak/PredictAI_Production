# Environments and dependency management

* An environment is a name plus immutable **versions**. Changing requirements, adding or removing a package, or rebuilding creates a new version; old versions stay for comparison.
* Requirements must be **exact pins** (`name==version`); URLs, VCS links, local paths, options (`--index-url`, `-e`) and hash-less wildcards are refused.
* Builds run as an `env_build` job in the worker: `pip install --only-binary=:all:` (no source builds, so no arbitrary `setup.py`) from the allow-listed indexes (`PREDICTAI_DEV_PIP_INDEX_URLS`) or a local folder of wheels (`PREDICTAI_DEV_WHEELHOUSE`, works offline). The build is the one place network is used (`package_index`), and it is **not** inside the no-network isolation; this is documented rather than hidden.
* After a build: `pip freeze` lockfile, `pip check`, and `pip-audit` if installed (otherwise the report says "unavailable", never "clean").
* Jobs choose an environment by id; the environment version used is stored on the job and on experiments.
* Verified here: real offline wheel builds from a local wheelhouse. Not verified: builds from pypi.org (no network in the build sandbox), macOS / Python 3.14 wheel availability.

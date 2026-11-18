# Pipelines, experiments and lineage

* **Pipelines** are ordered stages (`lint`, `unit_test`, `script`, `notebook`, ...). Each stage is a normal job, so isolation, limits and revocation apply. Behind **`PREDICTAI_DEV_PIPELINES_ENABLED`** (default off): definitions can be saved when off, runs are refused with an explanation.
* **Deployment is not available.** `POST .../deploy` always returns `DEPLOY_DISABLED` (501) and records the attempt. Nothing in this release publishes, serves or deploys a model.
* **Approval / rejection / rollback targets** are *recorded decisions* with who, when and why. They change no running system.
* **Experiments** record objective, task type, dataset asset/version, params, feature set, preprocessing, split strategy, seed, environment version, commit/tree hash, metrics, limitations, and the job that produced them. Running an experiment submits a job with `PREDICTAI_EXPERIMENT_PARAMS` and `PREDICTAI_SEED`.
* **Registry link**: an experiment can link a Phase 7 `reg_versions` row only if it belongs to the same project and owner.
* **Lineage** (`/experiments/{id}/lineage`): each stage (dataset, transformations, code, environment, job, metrics, model, ...) is either a real record or reported missing with a note. The UI shows "Recorded" or "Missing"; nothing is invented.

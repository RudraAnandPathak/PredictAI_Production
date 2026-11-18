# Model Registry guide

**What it is.** A versioned, integrity-checked record of the models you trained under Predictions. Open it from *Model registry*.

**Register.** Pick a trained model; PredictAI stores a verified copy (SHA-256) as version 1 in state *Draft*. Registering again under the same registry model adds a new version, but only for the same kind of model (classification vs regression) in the same project.

**Lifecycle.** draft → registered → evaluated → staging → active, with archived and failed as side states. Rules are enforced by the server (`allowed_transitions`). Staging → active, active → archived and active → staging need an explicit confirmation. Only one version is active at a time; activating another archives the previous active version (recorded in the history), and **rollback** makes an earlier version active again (also confirmed, with an optional reason). A version can be marked *evaluated* only if it has a hold-out evaluation. Every change is written to the model's history and the audit log.

**Integrity.** *Verify copy* re-hashes the stored artifact. A version whose stored copy is missing or does not match its checksum cannot be activated, rolled back to, or used for a prediction (typed `ARTIFACT_INTEGRITY` error), and the dashboard lists it as needing attention.

**Predictions.** Predicting from a registry version names that version in the result, so a prediction can always be traced to the exact model. Nothing is served outside PredictAI: *active* is a record, not a deployment.

**Comparison.** Two to six versions of the same kind can be compared on the metrics stored at training time. Nothing is retrained.

**Sharing.** The owner can share a registered model with a workspace (view or edit). Viewers see versions and history but cannot change the lifecycle.

# Phase 5 migrations

`0004_predictive.sql` (checksummed, forward-only, plain SQLite / D1-compatible SQL) adds three tables and their indexes.
`0001`, `0002` and `0003` are untouched. Existing data and configuration are preserved.

| Table | Holds |
|---|---|
| `ml_jobs` | one row per training request: owner, project, dataset version, validated configuration + hash, status (`queued / running / completed / failed / cancelled`), progress, heartbeat, cancel flag, resulting model id, error code, timings. The entitlement (`extended`) is captured at submission so limits cannot change under a running job. |
| `ml_models` | one row per saved model: owner, project, dataset version, per-dataset-and-target version number, target, problem type, algorithm, headline scores, seed, `ml_version`, configuration, features used/dropped with reasons, preprocessing settings, hyperparameters, input schema, dataset snapshot, storage keys, **SHA-256 of the model file**, sizes, timings. |
| `ml_predictions` | one row per prediction: owner, model, kind (`single` / `batch`), for a single prediction the values the person entered and the result, for a batch the summary and the storage key of the CSV. |

Rules the schema follows:
- every table carries `owner_user_id` and `project_id`, and every read filters on the owner;
- dataset rows are never stored in the database — model files, reports and batch CSVs live in storage under
  `p/<project>/a/<asset>/m/<model>/…`;
- `ml_predictions.source_asset_id` deliberately has no foreign key, so deleting a dataset is never blocked by an old batch;
- audit rows written by the modelling code hold ids, counts and outcomes only — never feature values or targets (the only
  place a person's entered values are kept is their own prediction history, shown back to them and deleted with the model).

Deletion: removing a project, an account, or a retention-expired dataset removes the modelling rows and files with it; a
deleted dataset hides its models immediately, before the purge finishes (all covered by tests in `test_predictive_security.py`).

## Upgrade check that was run

A database built by the **corrected Phase 4** code (`migrate` → "Applied migrations: [1, 2, 3]", one owner provisioned, no
`ml_*` tables) was then opened with the **Phase 5** code:

```
python -m app.cli migrate   →  Applied migrations: [4]
python -m app.cli migrate   →  Database is up to date.
tables after: ml_jobs, ml_models, ml_predictions     users after: 1 (owner preserved)     schema_migrations: [1, 2, 3, 4]
```

The migrator test (`test_workspace_projects.py`) also checks the discovered versions are `[1, 2, 3, 4]`, that 0001's checksum
matches its file, and that applying `[1]` and then the rest yields `[2, 3, 4]`.

Rollback: migrations are forward-only by design. Restore the database backup taken before upgrading; files under the storage
directory that belong to models can be removed safely because the older schema never refers to them.

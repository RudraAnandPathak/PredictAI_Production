# Release procedure and rollback procedure

PredictAI is a Python/FastAPI + SQLite + React application. There is **no deployment pipeline, no container image and no hosted environment** in this repository; "release" here means a verified, immutable archive plus a checked upgrade path. Nothing in this document deploys anything.

## Versioning
- The version is `app.__version__` in `backend/app/__init__.py`. **It is still `0.1.0`** — it was never bumped across phases. Choose the release version (suggested: `MAJOR.MINOR.PATCH`, bump MINOR per phase-sized change, PATCH per fix) **before** a pilot so support requests name a real version. This is left to the owner (see `DEFERRED_DECISIONS.md`); the tool never invents one.
- Database schema version = number of the latest applied migration (`schema_migrations`); currently **0011**. Migrations are numbered, checksummed, and a changed already-applied migration is refused.

## Release procedure
1. **Freeze**: working tree on the intended commit. (This build tree is not a git checkout; if you have one, pass `--commit SHA`; the tool never guesses.)
2. **Verify** on the build machine: backend tests (`cd backend && python -m unittest discover -s tests`), frontend (`cd frontend && npm test`), `python scripts/security_scan.py`, `python scripts/a11y_contrast.py`, `python docs/phase11/build_register.py --check`, `python scripts/run_pilot.py`. All must be green; paste actual counts into the report.
3. **Check migrations are safe**: `python -m app.ops.release compat` — every migration must be additive (adds tables/columns/indexes only), so the *previous* app version still runs on the upgraded schema. A non-additive migration needs an explicit expand/contract plan and a written rollback note.
4. **Package**: `python scripts/package_release.py ../PredictAI_release.zip` (excludes `.env*`, databases, keys, `dev-secret-key`, caches, `node_modules`, `*.zip`).
5. **Manifest**: `python -m app.ops.release manifest ../PredictAI_release.zip --tests-json results.json --out release-manifest.json` records version, build id, archive SHA-256, migration list with checksums, dependency/config hashes and the test report. It **fails** if the archive contains a secret or a database.
6. **Gates**: update `RELEASE_GATES.md` honestly; do not ship past a Blocked or Failed gate that the release depends on.
7. **Approval**: the owner reads the gate document, signs the decision (the four recommendations are separate), and the manifest is stored with the archive. Publish the archive's SHA-256 separately from the archive.

### Upgrading an installation
1. Announce; optionally maintenance mode on (runbook 14).
2. **`python -m app.ops.backup create ...` and `backup verify`** — a verified pre-upgrade backup is mandatory.
3. Stop the app; unpack the new release beside the old one (keep the old directory); keep `.env` and data directory in place.
4. `python -m app.cli migrate`; start the app.
5. Verify: `/health/ready` 200, sign in, open a project, run one small workflow, owner snapshot `ok`.
6. Maintenance mode off.

## Rollback procedure
Rollback is **restore, not reverse-migrate** (there are no down-migrations, by design — they are the riskier path).
1. Decide: roll back if verification in step 5 fails and cannot be fixed quickly. Maintenance mode on.
2. Stop the new app.
3. **Restore the pre-upgrade backup into a new empty directory**: `python -m app.ops.backup restore <backup> --target <new-dir>`. It runs integrity and foreign-key checks and verifies every file.
4. Start the **old release directory** pointed at the restored data (`PREDICTAI_DATABASE_PATH`, `PREDICTAI_STORAGE_DIR`).
5. Verify as above. Keep the failed upgrade's data directory aside for diagnosis; do not delete it.
6. **Data written after the backup is lost.** If it matters, export it from the aside copy before discarding.
7. Because all migrations are additive, an alternative for a *code-only* fault is to start the old release against the **upgraded** database without restoring — verified by `test_every_migration_is_additive_so_the_previous_app_version_still_works`.

## Evidence (build sandbox)
| Claim | Test |
|---|---|
| fresh install reaches the latest schema | `test_fresh_install_reaches_the_latest_version` |
| every one of the 11 migrations is additive; the checker really detects removals | `test_every_migration_is_additive…`, `test_the_additive_check_really_detects_removals` |
| interrupted migration leaves the database at the old version | `test_interrupted_migration_leaves_the_database_at_the_old_version` |
| a modified applied migration is refused | `test_a_changed_applied_migration_is_refused` |
| **rollback drill:** data at v10 → backup → upgrade to v11 → old queries still run → restore backup → schema is v10 again, files intact, live DB untouched, re-upgrade works | `test_upgrade_with_data_then_roll_back_by_restoring_the_pre_upgrade_backup` |
| manifest ties archive to contents; commit never invented; secret/database in archive fails the manifest; the real packager's output passes | `test_manifest_*`, `test_commit_is_never_invented`, `test_an_archive_carrying_a_secret_or_a_database_fails_the_manifest`, `test_the_real_packager_output_passes` |

## Not tested
Rollback on a real installation or macOS; a failed rollback of data larger than the test; migrations against a copy of real production data (none exists); anything involving containers, hosting or DNS.

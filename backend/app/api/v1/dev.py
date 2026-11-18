"""Development Workspace API (Phase 9), versioned under /api/v1/dev. Thin by design: authenticate, CSRF, call one service method. Special access, project ownership, limits and every other rule
live in the services, so they cannot be skipped by calling a route differently. Services validate their own input, so bodies are plain JSON objects and only whitelisted keys are passed on."""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.devspace import artifacts as A, engine as E, envs as V, experiments as X, git as G, notebooks as N, overview as O, pipelines as P, secrets as S, sessions as Z
from ...modules.devspace.compute import ComputeService
from ...modules.devspace.files import FileService
from ...modules.identity.authorization import Principal
from ..deps import enforce_csrf, get_services, get_workspace, require_principal

router = APIRouter(prefix="/dev", tags=["development-workspace"], dependencies=[Depends(enforce_csrf)])
hooks = APIRouter(prefix="/dev/webhooks", tags=["development-workspace"])      # no session, no CSRF: authenticated by the HMAC signature


class Body(BaseModel):
    """Any JSON object. The services validate every field they read, so no field list is repeated here; only whitelisted keys are ever passed on (see pick)."""
    model_config = ConfigDict(extra="allow")

    def get(self, key: str, default: Any = None) -> Any:
        return (self.model_extra or {}).get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in (self.model_extra or {})

    def __getitem__(self, key: str) -> Any:
        return (self.model_extra or {})[key]


PR = Depends(require_principal)
base = "/projects/{project_id}"


def dev(cls):
    def dep(services=Depends(get_services), ws=Depends(get_workspace)):
        return cls(services.ctx, ws)
    return Depends(dep)


def ok(request: Request, data: Any) -> dict:
    return success(data, request_id_from_scope(request.scope))


def pick(b: Body, *keys: str) -> Body:
    return {k: b[k] for k in keys if k in b}


# ---------------------------------------------------------------- landing, capabilities, history
@router.get("/overview", summary="Landing page data (real counts, recent jobs, isolation status). Returns access.allowed=false instead of an error when locked.")
def overview(request: Request, principal: Principal = PR, s: O.OverviewService = dev(O.OverviewService)) -> dict:
    return ok(request, s.landing(principal))


@router.get(base + "/history", summary="Consolidated project history")
def history(project_id: str, request: Request, action: Optional[str] = None, before: Optional[int] = None, limit: int = 50, principal: Principal = PR, s: O.OverviewService = dev(O.OverviewService)) -> dict:
    return ok(request, s.history(principal, project_id, action=action, before=before, limit=limit))


@router.get(base + "/timeline", summary="Latest milestone of each kind")
def timeline(project_id: str, request: Request, principal: Principal = PR, s: O.OverviewService = dev(O.OverviewService)) -> dict:
    return ok(request, s.timeline(principal, project_id))


# ---------------------------------------------------------------- files
@router.get(base + "/files", summary="File tree")
def files_list(project_id: str, request: Request, principal: Principal = PR, s: FileService = dev(FileService)) -> dict:
    return ok(request, s.list(principal, project_id))


@router.get(base + "/files/content", summary="Read a file (optionally an older version)")
def files_read(project_id: str, path: str, request: Request, version: Optional[int] = None, principal: Principal = PR, s: FileService = dev(FileService)) -> dict:
    return ok(request, s.read(principal, project_id, path, version))


@router.put(base + "/files/content", summary="Create or save a file (optimistic concurrency via expected_version)")
def files_write(project_id: str, b: Body, request: Request, principal: Principal = PR, s: FileService = dev(FileService)) -> dict:
    return ok(request, s.write(principal, project_id, b.get("path"), b.get("content"), b.get("expected_version")))


@router.post(base + "/files/delete", summary="Delete a file (earlier versions stay restorable)")
def files_delete(project_id: str, b: Body, request: Request, principal: Principal = PR, s: FileService = dev(FileService)) -> dict:
    return ok(request, s.delete(principal, project_id, b.get("path"), b.get("confirm")))


@router.post(base + "/files/restore", summary="Restore an earlier version as a new version")
def files_restore(project_id: str, b: Body, request: Request, principal: Principal = PR, s: FileService = dev(FileService)) -> dict:
    return ok(request, s.restore(principal, project_id, b.get("path"), b.get("version")))


@router.get(base + "/files/history", summary="Version history of one file")
def files_history(project_id: str, path: str, request: Request, principal: Principal = PR, s: FileService = dev(FileService)) -> dict:
    return ok(request, s.history(principal, project_id, path))


@router.get(base + "/files/search", summary="Search file contents")
def files_search(project_id: str, q: str, request: Request, principal: Principal = PR, s: FileService = dev(FileService)) -> dict:
    return ok(request, s.search(principal, project_id, q))


# ---------------------------------------------------------------- notebooks and sessions
@router.post(base + "/notebooks", status_code=201, summary="Create a notebook")
def nb_create(project_id: str, b: Body, request: Request, principal: Principal = PR, s: N.NotebookService = dev(N.NotebookService)) -> dict:
    return ok(request, s.create(principal, project_id, b.get("path"), b.get("title")))


@router.get(base + "/notebooks/info", summary="Validate a notebook and show its export options")
def nb_info(project_id: str, path: str, request: Request, principal: Principal = PR, s: N.NotebookService = dev(N.NotebookService)) -> dict:
    return ok(request, s.info(principal, project_id, path))


@router.post(base + "/notebooks/export", summary="Export a notebook as ipynb, py or html (pdf is reported unavailable)")
def nb_export(project_id: str, b: Body, request: Request, download: bool = False, principal: Principal = PR, s: N.NotebookService = dev(N.NotebookService)):
    out = s.export(principal, project_id, b.get("path"), b.get("format"), b.get("strip_outputs", False))
    if download:
        return Response(out["content"], media_type=out["content_type"], headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(out["filename"]), "X-Content-Type-Options": "nosniff"})
    return ok(request, out)


@router.get(base + "/sessions", summary="Notebook / IDE availability and sessions")
def sessions_status(project_id: str, request: Request, principal: Principal = PR, s: Z.SessionService = dev(Z.SessionService)) -> dict:
    return ok(request, s.status(principal, project_id))


@router.post(base + "/sessions", status_code=201, summary="Start a JupyterLab or IDE session (local_trusted mode only)")
def sessions_start(project_id: str, b: Body, request: Request, principal: Principal = PR, s: Z.SessionService = dev(Z.SessionService)) -> dict:
    return ok(request, s.start(principal, project_id, b.get("kind")))


@router.post(base + "/sessions/{session_id}/touch", summary="Keep a session alive")
def sessions_touch(project_id: str, session_id: str, request: Request, principal: Principal = PR, s: Z.SessionService = dev(Z.SessionService)) -> dict:
    return ok(request, s.touch(principal, project_id, session_id))


@router.post(base + "/sessions/{session_id}/stop", summary="Stop a session")
def sessions_stop(project_id: str, session_id: str, request: Request, principal: Principal = PR, s: Z.SessionService = dev(Z.SessionService)) -> dict:
    return ok(request, s.stop(principal, project_id, session_id))


# ---------------------------------------------------------------- environments
@router.get(base + "/envs", summary="Environments and approved package sources")
def envs_list(project_id: str, request: Request, principal: Principal = PR, s: V.EnvService = dev(V.EnvService)) -> dict:
    return ok(request, s.list(principal, project_id))


@router.post(base + "/envs", status_code=201, summary="Create an environment (builds in the worker)")
def envs_create(project_id: str, b: Body, request: Request, principal: Principal = PR, s: V.EnvService = dev(V.EnvService)) -> dict:
    return ok(request, s.create(principal, project_id, **pick(b, "name", "requirements", "python_version", "env_refs")))


@router.get(base + "/envs/{env_id}", summary="One environment with its versions")
def envs_get(project_id: str, env_id: str, request: Request, principal: Principal = PR, s: V.EnvService = dev(V.EnvService)) -> dict:
    return ok(request, s.get(principal, project_id, env_id))


@router.put(base + "/envs/{env_id}/requirements", summary="Replace the requirements (new immutable version)")
def envs_req(project_id: str, env_id: str, b: Body, request: Request, principal: Principal = PR, s: V.EnvService = dev(V.EnvService)) -> dict:
    return ok(request, s.set_requirements(principal, project_id, env_id, b.get("requirements"), b.get("env_refs")))


@router.post(base + "/envs/{env_id}/packages", summary="Add or change one package")
def envs_add(project_id: str, env_id: str, b: Body, request: Request, principal: Principal = PR, s: V.EnvService = dev(V.EnvService)) -> dict:
    return ok(request, s.add_package(principal, project_id, env_id, b.get("package")))


@router.post(base + "/envs/{env_id}/packages/remove", summary="Remove one package")
def envs_remove(project_id: str, env_id: str, b: Body, request: Request, principal: Principal = PR, s: V.EnvService = dev(V.EnvService)) -> dict:
    return ok(request, s.remove_package(principal, project_id, env_id, b.get("package")))


@router.post(base + "/envs/{env_id}/rebuild", summary="Rebuild from the same manifest (new version)")
def envs_rebuild(project_id: str, env_id: str, request: Request, principal: Principal = PR, s: V.EnvService = dev(V.EnvService)) -> dict:
    return ok(request, s.rebuild(principal, project_id, env_id))


@router.get(base + "/envs/{env_id}/compare", summary="Compare two versions")
def envs_compare(project_id: str, env_id: str, a: int, b: int, request: Request, principal: Principal = PR, s: V.EnvService = dev(V.EnvService)) -> dict:
    return ok(request, s.compare(principal, project_id, env_id, a, b))


@router.post(base + "/envs/{env_id}/delete", summary="Delete an environment")
def envs_delete(project_id: str, env_id: str, b: Body, request: Request, principal: Principal = PR, s: V.EnvService = dev(V.EnvService)) -> dict:
    return ok(request, s.delete(principal, project_id, env_id, b.get("confirm")))


# ---------------------------------------------------------------- jobs
@router.get(base + "/jobs", summary="Jobs")
def jobs_list(project_id: str, request: Request, status: Optional[str] = None, limit: int = 50, principal: Principal = PR, s: E.JobService = dev(E.JobService)) -> dict:
    return ok(request, s.list(principal, project_id, status, limit))


@router.post(base + "/jobs", status_code=201, summary="Submit a job (script, notebook or python command). Runs in the worker, never in this process.")
def jobs_submit(project_id: str, b: Body, request: Request, principal: Principal = PR, s: E.JobService = dev(E.JobService)) -> dict:
    return ok(request, s.submit(principal, project_id, **pick(b, "kind", "entry", "argv", "env_id", "priority", "secret_refs", "max_attempts", "limits", "experiment_id")))


@router.get(base + "/jobs/{job_id}", summary="One job")
def jobs_get(project_id: str, job_id: str, request: Request, principal: Principal = PR, s: E.JobService = dev(E.JobService)) -> dict:
    return ok(request, s.get(principal, project_id, job_id))


@router.get(base + "/jobs/{job_id}/logs", summary="Redacted stdout and stderr")
def jobs_logs(project_id: str, job_id: str, request: Request, principal: Principal = PR, s: E.JobService = dev(E.JobService)) -> dict:
    return ok(request, s.logs(principal, project_id, job_id))


@router.post(base + "/jobs/{job_id}/cancel", summary="Cancel a job")
def jobs_cancel(project_id: str, job_id: str, request: Request, principal: Principal = PR, s: E.JobService = dev(E.JobService)) -> dict:
    return ok(request, s.cancel(principal, project_id, job_id))


@router.post(base + "/jobs/{job_id}/retry", status_code=201, summary="Run a failed job again")
def jobs_retry(project_id: str, job_id: str, request: Request, principal: Principal = PR, s: E.JobService = dev(E.JobService)) -> dict:
    return ok(request, s.retry(principal, project_id, job_id))


@router.get(base + "/jobs/{job_id}/artifacts", summary="Files a job produced")
def jobs_artifacts(project_id: str, job_id: str, request: Request, principal: Principal = PR, s: E.JobService = dev(E.JobService)) -> dict:
    return ok(request, s.artifacts(principal, project_id, job_id))


# ---------------------------------------------------------------- secrets (values are write-only)
@router.get(base + "/secrets", summary="Secret names (never values)")
def secrets_list(project_id: str, request: Request, principal: Principal = PR, s: S.SecretService = dev(S.SecretService)) -> dict:
    return ok(request, s.list(principal, project_id))


@router.post(base + "/secrets", status_code=201, summary="Store a secret (encrypted; the value is never returned)")
def secrets_create(project_id: str, b: Body, request: Request, principal: Principal = PR, s: S.SecretService = dev(S.SecretService)) -> dict:
    return ok(request, s.create(principal, project_id, b.get("name"), b.get("value"), b.get("purpose", "job")))


@router.post(base + "/secrets/{secret_id}/revoke", summary="Revoke a secret")
def secrets_revoke(project_id: str, secret_id: str, request: Request, principal: Principal = PR, s: S.SecretService = dev(S.SecretService)) -> dict:
    return ok(request, s.revoke(principal, project_id, secret_id))


# ---------------------------------------------------------------- artifacts
@router.get(base + "/artifacts/summary", summary="Bucket summary and quota")
def art_summary(project_id: str, request: Request, principal: Principal = PR, s: A.ArtifactService = dev(A.ArtifactService)) -> dict:
    return ok(request, s.summary(principal, project_id))


@router.get(base + "/artifacts", summary="Artifacts")
def art_list(project_id: str, request: Request, bucket: Optional[str] = None, name: Optional[str] = None, principal: Principal = PR, s: A.ArtifactService = dev(A.ArtifactService)) -> dict:
    return ok(request, s.list(principal, project_id, bucket, name))


@router.get(base + "/artifacts/{artifact_id}", summary="Read one artifact (checksum re-verified)")
def art_read(project_id: str, artifact_id: str, request: Request, principal: Principal = PR, s: A.ArtifactService = dev(A.ArtifactService)) -> dict:
    return ok(request, s.read(principal, project_id, artifact_id))


@router.post(base + "/artifacts/{artifact_id}/delete", summary="Delete an artifact")
def art_delete(project_id: str, artifact_id: str, b: Body, request: Request, principal: Principal = PR, s: A.ArtifactService = dev(A.ArtifactService)) -> dict:
    return ok(request, s.delete(principal, project_id, artifact_id, b.get("confirm")))


@router.post(base + "/artifacts/{artifact_id}/retention", summary="Set or clear a retention date")
def art_retention(project_id: str, artifact_id: str, b: Body, request: Request, principal: Principal = PR, s: A.ArtifactService = dev(A.ArtifactService)) -> dict:
    return ok(request, s.set_retention(principal, project_id, artifact_id, b.get("days")))


# ---------------------------------------------------------------- git
@router.get(base + "/git", summary="Repository status")
def git_status(project_id: str, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.status(principal, project_id))


@router.post(base + "/git/init", summary="Initialise Git for the project")
def git_init(project_id: str, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.init(principal, project_id))


@router.post(base + "/git/connect", status_code=201, summary="Connect a remote (consent required; credentials stored encrypted)")
def git_connect(project_id: str, b: Body, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.connect(principal, project_id, **pick(b, "provider", "remote_url", "branch", "consent", "consent_scope", "credential", "webhook_secret", "protected_branches")))


@router.post(base + "/git/disconnect", summary="Disconnect and revoke stored credentials")
def git_disconnect(project_id: str, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.disconnect(principal, project_id))


@router.get(base + "/git/log", summary="Commit history")
def git_log(project_id: str, request: Request, limit: int = 30, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.log(principal, project_id, limit))


@router.get(base + "/git/branches", summary="Branches")
def git_branches(project_id: str, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.branches(principal, project_id))


@router.get(base + "/git/tags", summary="Tags")
def git_tags(project_id: str, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.tags(principal, project_id))


@router.get(base + "/git/diff", summary="Working-tree diff")
def git_diff(project_id: str, request: Request, path: Optional[str] = None, staged: bool = False, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.diff(principal, project_id, path, staged))


@router.post(base + "/git/commit", status_code=201, summary="Commit (refused if staged files look like credentials)")
def git_commit(project_id: str, b: Body, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.commit(principal, project_id, b.get("message"), b.get("paths")))


@router.post(base + "/git/branches", status_code=201, summary="Create a branch")
def git_branch_create(project_id: str, b: Body, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.create_branch(principal, project_id, b.get("name"), b.get("switch", True)))


@router.post(base + "/git/branches/switch", summary="Switch branch")
def git_branch_switch(project_id: str, b: Body, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.switch_branch(principal, project_id, b.get("name")))


@router.post(base + "/git/tags", status_code=201, summary="Create a tag")
def git_tag(project_id: str, b: Body, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.tag(principal, project_id, b.get("name"), b.get("message")))


@router.post(base + "/git/pull", summary="Fast-forward pull")
def git_pull(project_id: str, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.pull(principal, project_id))


@router.post(base + "/git/push", summary="Push the current (non-protected) branch")
def git_push(project_id: str, b: Body, request: Request, principal: Principal = PR, s: G.GitService = dev(G.GitService)) -> dict:
    return ok(request, s.push(principal, project_id, b.get("branch")))


@hooks.post("/git/{repo_id}", summary="Git host webhook (HMAC signature + unique delivery id)")
async def git_webhook(repo_id: str, request: Request, services=Depends(get_services), ws=Depends(get_workspace)) -> dict:
    body = await request.body()
    return ok(request, G.GitService(services.ctx, ws).webhook(repo_id, dict(request.headers), body))


# ---------------------------------------------------------------- pipelines
@router.get(base + "/pipelines", summary="Pipelines and feature flags")
def pipe_list(project_id: str, request: Request, principal: Principal = PR, s: P.PipelineService = dev(P.PipelineService)) -> dict:
    return ok(request, s.list(principal, project_id))


@router.post(base + "/pipelines", status_code=201, summary="Define a pipeline")
def pipe_create(project_id: str, b: Body, request: Request, principal: Principal = PR, s: P.PipelineService = dev(P.PipelineService)) -> dict:
    return ok(request, s.create(principal, project_id, **pick(b, "name", "stages", "triggers")))


@router.post(base + "/pipelines/{pipeline_id}/delete", summary="Delete a pipeline definition")
def pipe_delete(project_id: str, pipeline_id: str, request: Request, principal: Principal = PR, s: P.PipelineService = dev(P.PipelineService)) -> dict:
    return ok(request, s.delete(principal, project_id, pipeline_id))


@router.post(base + "/pipelines/{pipeline_id}/run", status_code=201, summary="Start a run (feature-flagged)")
def pipe_run(project_id: str, pipeline_id: str, request: Request, principal: Principal = PR, s: P.PipelineService = dev(P.PipelineService)) -> dict:
    return ok(request, s.run(principal, project_id, pipeline_id))


@router.get(base + "/pipeline-runs", summary="Runs")
def pipe_runs(project_id: str, request: Request, pipeline_id: Optional[str] = None, principal: Principal = PR, s: P.PipelineService = dev(P.PipelineService)) -> dict:
    return ok(request, s.runs(principal, project_id, pipeline_id))


@router.get(base + "/pipeline-runs/{run_id}", summary="One run with its stages and final record")
def pipe_run_get(project_id: str, run_id: str, request: Request, principal: Principal = PR, s: P.PipelineService = dev(P.PipelineService)) -> dict:
    return ok(request, s.get_run(principal, project_id, run_id))


@router.post(base + "/pipeline-runs/{run_id}/cancel", summary="Cancel a run")
def pipe_run_cancel(project_id: str, run_id: str, request: Request, principal: Principal = PR, s: P.PipelineService = dev(P.PipelineService)) -> dict:
    return ok(request, s.cancel_run(principal, project_id, run_id))


@router.post(base + "/pipeline-runs/{run_id}/approve", summary="Approve a green run (recorded decision; nothing deploys)")
def pipe_run_approve(project_id: str, run_id: str, b: Body, request: Request, principal: Principal = PR, s: P.PipelineService = dev(P.PipelineService)) -> dict:
    return ok(request, s.approve(principal, project_id, run_id, b.get("reason")))


@router.post(base + "/pipeline-runs/{run_id}/reject", summary="Reject a green run")
def pipe_run_reject(project_id: str, run_id: str, b: Body, request: Request, principal: Principal = PR, s: P.PipelineService = dev(P.PipelineService)) -> dict:
    return ok(request, s.reject(principal, project_id, run_id, b.get("reason")))


@router.post(base + "/pipeline-runs/{run_id}/deploy", summary="Deploy (disabled in this release; always refuses and records the attempt)")
def pipe_run_deploy(project_id: str, run_id: str, request: Request, principal: Principal = PR, s: P.PipelineService = dev(P.PipelineService)) -> dict:
    return ok(request, s.deploy(principal, project_id, run_id))


# ---------------------------------------------------------------- experiments and lineage
@router.get(base + "/experiments", summary="Experiments")
def exp_list(project_id: str, request: Request, principal: Principal = PR, s: X.ExperimentService = dev(X.ExperimentService)) -> dict:
    return ok(request, s.list(principal, project_id))


@router.post(base + "/experiments", status_code=201, summary="Record an experiment")
def exp_create(project_id: str, b: Body, request: Request, principal: Principal = PR, s: X.ExperimentService = dev(X.ExperimentService)) -> dict:
    return ok(request, s.create(principal, project_id, **pick(b, "name", "objective", "task_type", "asset_id", "dataset_version_id", "env_id", "params", "feature_set", "preprocessing", "split_strategy", "seed")))


@router.post(base + "/experiments/compare", summary="Compare experiments")
def exp_compare(project_id: str, b: Body, request: Request, principal: Principal = PR, s: X.ExperimentService = dev(X.ExperimentService)) -> dict:
    return ok(request, s.compare(principal, project_id, b.get("ids")))


@router.get(base + "/experiments/{experiment_id}", summary="One experiment")
def exp_get(project_id: str, experiment_id: str, request: Request, principal: Principal = PR, s: X.ExperimentService = dev(X.ExperimentService)) -> dict:
    return ok(request, s.get(principal, project_id, experiment_id))


@router.post(base + "/experiments/{experiment_id}/run", status_code=201, summary="Run an experiment script as a job")
def exp_run(project_id: str, experiment_id: str, b: Body, request: Request, principal: Principal = PR, s: X.ExperimentService = dev(X.ExperimentService)) -> dict:
    return ok(request, s.run(principal, project_id, experiment_id, **pick(b, "entry", "priority", "secret_refs", "limits")))


@router.post(base + "/experiments/{experiment_id}/link-model", summary="Link a Phase 7 registry model version")
def exp_link(project_id: str, experiment_id: str, b: Body, request: Request, principal: Principal = PR, s: X.ExperimentService = dev(X.ExperimentService)) -> dict:
    return ok(request, s.link_model(principal, project_id, experiment_id, b.get("reg_version_id"), b.get("limitations")))


@router.post(base + "/experiments/{experiment_id}/approve", summary="Approve (recorded decision; nothing deploys)")
def exp_approve(project_id: str, experiment_id: str, b: Body, request: Request, principal: Principal = PR, s: X.ExperimentService = dev(X.ExperimentService)) -> dict:
    return ok(request, s.approve(principal, project_id, experiment_id, b.get("reason")))


@router.post(base + "/experiments/{experiment_id}/revoke-approval", summary="Revoke an approval")
def exp_revoke(project_id: str, experiment_id: str, b: Body, request: Request, principal: Principal = PR, s: X.ExperimentService = dev(X.ExperimentService)) -> dict:
    return ok(request, s.revoke_approval(principal, project_id, experiment_id, b.get("reason")))


@router.post(base + "/experiments/{experiment_id}/rollback-target", summary="Record an earlier registry version as the rollback target")
def exp_rollback(project_id: str, experiment_id: str, b: Body, request: Request, principal: Principal = PR, s: X.ExperimentService = dev(X.ExperimentService)) -> dict:
    return ok(request, s.set_rollback(principal, project_id, experiment_id, b.get("reg_version_id")))


@router.get(base + "/experiments/{experiment_id}/lineage", summary="Lineage chain (every link a real record or reported missing)")
def exp_lineage(project_id: str, experiment_id: str, request: Request, principal: Principal = PR, s: X.ExperimentService = dev(X.ExperimentService)) -> dict:
    return ok(request, s.lineage(principal, project_id, experiment_id))


# ---------------------------------------------------------------- compute
@router.get(base + "/compute/limits", summary="Effective limits")
def compute_limits(project_id: str, request: Request, principal: Principal = PR, s: ComputeService = dev(ComputeService)) -> dict:
    return ok(request, s.limits(principal, project_id))


@router.get(base + "/compute/usage", summary="Usage against limits")
def compute_usage(project_id: str, request: Request, principal: Principal = PR, s: ComputeService = dev(ComputeService)) -> dict:
    return ok(request, s.usage(principal, project_id))


@router.get("/compute/capabilities", summary="What this installation can and cannot do (GPU, pause, resize)")
def compute_caps(request: Request, principal: Principal = PR, s: ComputeService = dev(ComputeService)) -> dict:
    return ok(request, s.capabilities(principal))


@router.get("/owner/compute-overrides", summary="Owner: list overrides")
def ovr_list(request: Request, scope: Optional[str] = None, scope_id: Optional[str] = None, principal: Principal = PR, s: ComputeService = dev(ComputeService)) -> dict:
    return ok(request, s.list_overrides(principal, scope, scope_id))


@router.put("/owner/compute-overrides", summary="Owner: set an override (step-up, reason required, audited)")
def ovr_set(b: Body, request: Request, principal: Principal = PR, s: ComputeService = dev(ComputeService)) -> dict:
    return ok(request, s.set_override(principal, **pick(b, "scope", "scope_id", "key", "value", "reason")))


@router.post("/owner/compute-overrides/clear", summary="Owner: clear an override")
def ovr_clear(b: Body, request: Request, principal: Principal = PR, s: ComputeService = dev(ComputeService)) -> dict:
    return ok(request, s.clear_override(principal, **pick(b, "scope", "scope_id", "key")))

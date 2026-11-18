# Special access and creating a workspace

## Who can use it
Everything under `/api/v1/dev` and the Development / Compute pages needs `Perm.WORKSPACE_DEVELOPMENT` (Special access or Owner). The check runs inside **every service method** from the principal rebuilt on each request, so calling a service directly, replaying an old request, or editing the page cannot bypass it. A person without access receives `SPECIAL_ACCESS_REQUIRED` (403); the landing endpoint alone returns `{"access": {"allowed": false, "reason": ...}}` so the page can explain itself, and nothing else.

## Projects and ownership
A workspace belongs to an existing Project. Only the project's owner can open it; another Special user gets 404 (existence is not disclosed). The platform owner follows the same rule: owner status gives the Development permission and the compute-override controls, **not** access to other people's projects (Phase 7 behaviour is unchanged).

## Creating a workspace
There is no separate "create workspace" button: the `dv_workspaces` row is created the first time a writable operation (save a file, submit a job, create an environment) runs on the project. Reads of a never-opened project show an empty workspace.

## Losing and regaining access
Revoking Special access: queued jobs become `blocked` (`entitlement_revoked`), running jobs are cancelled, sessions are stopped, workspaces are `suspended` (readable by nobody until access returns; files are kept). When access is granted again the next gated call reactivates the workspace. Convergence is checked by the worker every second while a job runs and on every tick.

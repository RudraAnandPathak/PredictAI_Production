"""Git integration, built on the `git` binary (no third-party Git library).

Rules enforced here: consent is recorded before a remote is connected; read-only consent cannot push; protected branches are never pushed to from the workspace; no force push; credentials are encrypted
secrets handed to git through GIT_ASKPASS for a single command (never in the URL, argv, .git/config, project files, logs or notebooks); repository hooks never run; commits are refused when staged text
looks like a credential; webhooks are accepted only with a valid signature and a delivery id not seen before."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from .common import DevService, contains_secret_pattern, dev_error, invalid, new_id, not_found, project_dir, record_event, redact_text, safe_path, BLOCKED_NAMES
from . import secrets as secretlib

PROVIDERS = {"github": ("github.com", "x-access-token"), "gitlab": ("gitlab.com", "oauth2"), "selfhosted": (None, "git"), "local": (None, "git")}
BRANCH = re.compile(r"^(?!/)(?!.*(\.\.|//|@\{|\\|\s|~|\^|:|\?|\*|\[))[A-Za-z0-9][A-Za-z0-9._/-]{0,98}[A-Za-z0-9_-]$|^[A-Za-z0-9]$")
SHA = re.compile(r"^[0-9a-f]{7,40}$")
CRED_NAME, HOOK_NAME = "GIT_CREDENTIAL", "GIT_WEBHOOK_SECRET"
MAX_DIFF = 200_000
ASKPASS = "#!/bin/sh\ncase \"$1\" in\n  Username*) printf '%s' \"$PREDICTAI_GIT_USER\" ;;\n  *) printf '%s' \"$PREDICTAI_GIT_TOKEN\" ;;\nesac\n"


def valid_branch(name: Any) -> str:
    if not isinstance(name, str) or not BRANCH.match(name) or name.endswith((".lock", ".")):
        raise invalid("branch", "Use a plain branch name such as feature/new-model.")
    return name


def check_remote(settings, provider: str, url: Any) -> str:
    if provider not in PROVIDERS:
        raise invalid("provider", "Choose github, gitlab, selfhosted or local.")
    if not isinstance(url, str) or not url or len(url) > 300 or any(c in url for c in " \t\r\n\x00'\"`$;|&<>\\"):
        raise invalid("remote_url", "Enter the repository address.")
    if provider == "local":
        if not settings.dev_git_allow_local:
            raise dev_error("LOCAL_REMOTE_DISABLED", "Local repositories are only allowed in a local installation (PREDICTAI_DEV_GIT_ALLOW_LOCAL).", 403)
        if not url.startswith("/") or not Path(url).is_dir():
            raise invalid("remote_url", "Give the absolute path of an existing local repository folder.")
        return url
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443) or parts.query or parts.fragment:
        raise invalid("remote_url", "Use a plain https:// address without a username, password or port. Credentials are added separately and stored encrypted.")
    host = parts.hostname.lower()
    allowed = {h.strip() for h in settings.dev_git_hosts.split(",") if h.strip()}
    base = PROVIDERS[provider][0]
    if (base and host != base) or host not in allowed:
        raise dev_error("GIT_HOST_NOT_ALLOWED", f"The host {host} is not on this installation's allowed Git hosts ({', '.join(sorted(allowed))}).", 403)
    return url


def present_repo(r) -> Optional[Dict[str, Any]]:
    if r is None:
        return None
    return {"id": r["id"], "provider": r["provider"], "remote_url": r["remote_url"], "branch": r["branch"], "protected_branches": json.loads(r["protected_branches"]), "consent_scope": r["consent_scope"],
            "consent_at": r["consent_at"], "status": r["status"], "has_credential": r["credential_secret_id"] is not None, "has_webhook": r["webhook_secret_id"] is not None}


class GitService(DevService):
    # ------------------------------------------------------------------ plumbing
    def _tree(self, project_id: str) -> Path:
        t = project_dir(self.ctx.settings, project_id) / "tree"
        t.mkdir(parents=True, exist_ok=True)
        return t

    def _repo(self, project_id: str, need: bool = True):
        r = self.conn.execute("SELECT * FROM dv_repos WHERE project_id = ?", (project_id,)).fetchone()
        if need and (r is None or r["status"] != "connected"):
            raise dev_error("GIT_NOT_CONNECTED", "No Git repository is connected to this project.", 409)
        return r

    def _git(self, project_id: str, args: List[str], *, repo=None, network: bool = False, timeout: int = 60, check: bool = True, author: Optional[str] = None, stdin: Optional[str] = None) -> str:
        tree = self._tree(project_id)
        s = self.ctx.settings
        with tempfile.TemporaryDirectory(prefix="gitrun") as tmp:
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tmp, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8",
                   "GIT_OPTIONAL_LOCKS": "0"}
            secret_values: List[str] = []
            if network and repo is not None and repo["credential_secret_id"]:
                names = [self.conn.execute("SELECT name FROM dv_secrets WHERE id = ?", (repo["credential_secret_id"],)).fetchone()["name"]]
                vals = secretlib.resolve_values(self.conn, s, self.now(), project_id, names, purposes=("git",))
                token = vals[names[0]]
                script = Path(tmp) / "askpass.sh"
                script.write_text(ASKPASS)
                script.chmod(stat.S_IRWXU)
                env.update({"GIT_ASKPASS": str(script), "PREDICTAI_GIT_USER": PROVIDERS[repo["provider"]][1], "PREDICTAI_GIT_TOKEN": token})
                secret_values.append(token)
            protocols = "https" + (":file" if False else "")
            allow_file = repo is not None and repo["provider"] == "local"
            cfg = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", f"protocol.file.allow={'always' if allow_file else 'never'}",
                   "-c", "credential.helper=", "-c", "core.askPass=", "-c", "safe.directory=" + str(tree), "-c", "advice.detachedHead=false"]
            if author:
                cfg += ["-c", f"user.name={author}", "-c", "user.email=" + hashlib.sha256(author.encode()).hexdigest()[:12] + "@users.noreply.predictai.local", "-c", "commit.gpgsign=false"]
            try:
                p = subprocess.run(["git", *cfg, "-C", str(tree), *args], env=env, capture_output=True, text=True, timeout=timeout, input=stdin, stdin=None if stdin else subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                raise dev_error("GIT_TIMEOUT", "The Git command took too long and was stopped.", 504)
            except FileNotFoundError:
                raise dev_error("GIT_UNAVAILABLE", "The git program is not installed on the server.", 503)
            out = redact_text((p.stdout or "") + (("\n" + p.stderr) if p.stderr and (p.returncode != 0 or not p.stdout) else ""), secret_values)
            if check and p.returncode != 0:
                raise dev_error("GIT_FAILED", redact_text(out.strip().splitlines()[-1] if out.strip() else "The Git command failed.", secret_values)[:300], 422)
            return out

    def _initialised(self, project_id: str) -> bool:
        return (self._tree(project_id) / ".git").is_dir()

    def _rescan(self, project_id: str, actor: Optional[str]) -> Dict[str, int]:
        from .files import FileService
        return FileService(self.ctx, self.ws).rescan(project_id, actor)

    # ------------------------------------------------------------------ connection
    def status(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        repo = self._repo(project["id"], need=False)
        out: Dict[str, Any] = {"repo": present_repo(repo), "initialised": self._initialised(project["id"]), "git_available": self._git_installed()}
        if out["initialised"]:
            head = self._git(project["id"], ["rev-parse", "--short", "HEAD"], check=False).strip()
            br = self._git(project["id"], ["branch", "--show-current"], check=False).strip()
            porcelain = self._git(project["id"], ["status", "--porcelain=v1", "-uall"], check=False)
            out.update({"branch": br or None, "head": head if re.fullmatch(r"[0-9a-f]{7,40}", head) else None,
                        "changes": [{"status": l[:2].strip(), "path": l[3:]} for l in porcelain.splitlines()[:500] if len(l) > 3]})
        return out

    @staticmethod
    def _git_installed() -> bool:
        try:
            subprocess.run(["git", "--version"], capture_output=True, timeout=10)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def init(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_git:user", principal)
        if not self._initialised(project["id"]):
            self._git(project["id"], ["init", "-q", "-b", "main"])
            self.event(project["id"], principal, "git.initialised", "repo", project["id"])
        return self.status(principal, project["id"])

    def connect(self, principal, project_id: Any, *, provider: Any = None, remote_url: Any = None, branch: Any = "main", consent: Any = False, consent_scope: Any = "read",
                credential: Any = None, webhook_secret: Any = None, protected_branches: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_git:user", principal)
        if consent is not True:
            raise invalid("consent", "Confirm that you allow this project to connect to the repository. Nothing is connected without your consent.")
        if consent_scope not in ("read", "read_write"):
            raise invalid("consent_scope", "Choose read or read_write.")
        url, branch = check_remote(self.ctx.settings, provider, remote_url), valid_branch(branch)
        if self._repo(project["id"], need=False) is not None and self._repo(project["id"], need=False)["status"] == "connected":
            raise dev_error("GIT_ALREADY_CONNECTED", "A repository is already connected. Disconnect it first.", 409)
        protected = ["main", "master"] if protected_branches is None else [valid_branch(b) for b in protected_branches][:20]
        secrets_svc = secretlib.SecretService(self.ctx, self.ws)
        cred_id = hook_id = None
        # old (revoked) rows with these names do not block re-use because the unique index only covers live secrets
        if credential:
            cred_id = secrets_svc.create(principal, project["id"], CRED_NAME, credential, "git")["id"]
        if webhook_secret:
            hook_id = secrets_svc.create(principal, project["id"], HOOK_NAME, webhook_secret, "webhook")["id"]
        if not self._initialised(project["id"]):
            self._git(project["id"], ["init", "-q", "-b", branch])
        now = self.now()
        self.conn.execute("DELETE FROM dv_repos WHERE project_id = ? AND status = 'revoked'", (project["id"],))
        rid = new_id()
        self.conn.execute("INSERT INTO dv_repos (id, project_id, owner_user_id, provider, remote_url, branch, protected_branches, consent_at, consent_scope, credential_secret_id, webhook_secret_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (rid, project["id"], project["owner_user_id"], provider, url, branch, json.dumps(protected), now, consent_scope, cred_id, hook_id, now))
        have = self._git(project["id"], ["remote"], check=False).split()
        self._git(project["id"], ["remote", "set-url" if "origin" in have else "add", "origin", url])
        self.event(project["id"], principal, "git.connected", "repo", rid, provider=provider, scope=consent_scope, host=urlsplit(url).hostname or "local", credential=bool(cred_id))
        return self.status(principal, project["id"])

    def disconnect(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        repo = self._repo(project["id"])
        now = self.now()
        for col in ("credential_secret_id", "webhook_secret_id"):
            if repo[col]:
                self.conn.execute("UPDATE dv_secrets SET revoked_at = ?, ciphertext = '' WHERE id = ?", (now, repo[col]))
        self.conn.execute("UPDATE dv_repos SET status = 'revoked', revoked_at = ?, credential_secret_id = NULL, webhook_secret_id = NULL WHERE id = ?", (now, repo["id"]))
        self._git(project["id"], ["remote", "remove", "origin"], check=False)
        self.event(project["id"], principal, "git.disconnected", "repo", repo["id"])
        return self.status(principal, project["id"])

    # ------------------------------------------------------------------ read operations
    def log(self, principal, project_id: Any, limit: Any = 30) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        if not self._initialised(project["id"]):
            return {"items": []}
        n = max(1, min(int(limit) if isinstance(limit, int) and not isinstance(limit, bool) else 30, 100))
        out = self._git(project["id"], ["log", f"-{n}", "--date=iso-strict", "--pretty=format:%H%x1f%an%x1f%ad%x1f%s"], check=False)
        items = []
        for line in out.splitlines():
            p = line.split("\x1f")
            if len(p) == 4 and SHA.match(p[0]):
                items.append({"sha": p[0], "author": p[1], "date": p[2], "subject": p[3][:200]})
        return {"items": items}

    def branches(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        if not self._initialised(project["id"]):
            return {"items": [], "current": None}
        cur = self._git(project["id"], ["branch", "--show-current"], check=False).strip()
        names = [l.strip() for l in self._git(project["id"], ["for-each-ref", "--format=%(refname:short)", "refs/heads"], check=False).splitlines() if l.strip()]
        repo = self._repo(project["id"], need=False)
        prot = json.loads(repo["protected_branches"]) if repo else ["main", "master"]
        return {"current": cur or None, "items": [{"name": n, "current": n == cur, "protected": n in prot} for n in names]}

    def tags(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        if not self._initialised(project["id"]):
            return {"items": []}
        out = self._git(project["id"], ["for-each-ref", "--format=%(refname:short)%09%(objectname)", "refs/tags"], check=False)
        return {"items": [{"name": l.split("\t")[0], "sha": l.split("\t")[1]} for l in out.splitlines() if "\t" in l]}

    def diff(self, principal, project_id: Any, path: Any = None, staged: Any = False) -> Dict[str, Any]:
        project = self.open_project(principal, project_id)
        if not self._initialised(project["id"]):
            return {"diff": "", "truncated": False}
        args = ["diff", "--no-color", "--no-ext-diff"] + (["--cached"] if staged is True else [])
        if path is not None:
            args += ["--", safe_path(path)]
        out = self._git(project["id"], args, check=False)
        return {"diff": redact_text(out[:MAX_DIFF]), "truncated": len(out) > MAX_DIFF}

    # ------------------------------------------------------------------ write operations
    def commit(self, principal, project_id: Any, message: Any, paths: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_git:user", principal)
        if not self._initialised(project["id"]):
            raise dev_error("GIT_NOT_INITIALISED", "Initialise Git for this project first.", 409)
        if not isinstance(message, str) or not 3 <= len(message.strip()) <= 500:
            raise invalid("message", "Write a commit message of 3 to 500 characters.")
        if contains_secret_pattern(message):
            raise dev_error("SECRET_IN_COMMIT", "The commit message looks like it contains a credential.", 422)
        if paths is not None:
            if not isinstance(paths, list) or not paths or len(paths) > 200:
                raise invalid("paths", "Give a list of file paths, or leave it out to commit everything.")
            stage = ["add", "--", *[safe_path(p) for p in paths]]
        else:
            stage = ["add", "-A"]
        self._git(project["id"], stage)
        staged = self._git(project["id"], ["diff", "--cached", "--name-only", "-z"], check=False).split("\x00")
        problems = []
        tree = self._tree(project["id"])
        for rel in [p for p in staged if p]:
            if BLOCKED_NAMES.search(rel):
                problems.append(rel)
                continue
            f = tree / rel
            try:
                if f.is_file() and f.stat().st_size <= 1024 * 1024 and contains_secret_pattern(f.read_text(errors="ignore")):
                    problems.append(rel)
            except OSError:
                continue
        if problems:
            self._git(project["id"], ["reset", "-q"], check=False)
            self.event(project["id"], principal, "git.commit_blocked", "repo", project["id"], "denied", reason="credential_pattern", files=len(problems))
            raise dev_error("SECRET_IN_COMMIT", f"{len(problems)} file(s) look like they contain a credential or are credential files, so nothing was committed: {', '.join(problems[:5])}. Remove them or use a secret reference.", 422)
        if not staged or staged == [""]:
            raise dev_error("NOTHING_TO_COMMIT", "There are no changes to commit.", 409)
        self._git(project["id"], ["commit", "-q", "-m", message.strip()], author=(principal.display_name or "PredictAI user")[:60])
        sha = self._git(project["id"], ["rev-parse", "HEAD"]).strip()
        self.event(project["id"], principal, "git.committed", "commit", sha, files=len([p for p in staged if p]))
        return {"sha": sha, "files": len([p for p in staged if p])}

    def create_branch(self, principal, project_id: Any, name: Any, switch: Any = True) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_git:user", principal)
        name = valid_branch(name)
        self._git(project["id"], ["checkout", "-q", "-b", name] if switch else ["branch", name])
        self._rescan(project["id"], principal.user_id)
        self.event(project["id"], principal, "git.branch_created", "branch", name)
        return self.branches(principal, project["id"])

    def switch_branch(self, principal, project_id: Any, name: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_git:user", principal)
        name = valid_branch(name)
        if self._git(project["id"], ["status", "--porcelain"], check=False).strip():
            raise dev_error("UNCOMMITTED_CHANGES", "Commit or discard your changes before switching branches.", 409)
        self._git(project["id"], ["checkout", "-q", name])
        counts = self._rescan(project["id"], principal.user_id)
        self.event(project["id"], principal, "git.branch_switched", "branch", name, **counts)
        return self.branches(principal, project["id"])

    def tag(self, principal, project_id: Any, name: Any, message: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_git:user", principal)
        valid_branch(name)
        args = ["tag", "-a", name, "-m", message.strip()[:300]] if isinstance(message, str) and message.strip() else ["tag", name]
        self._git(project["id"], args, author=(principal.display_name or "PredictAI user")[:60])
        self.event(project["id"], principal, "git.tagged", "tag", name)
        return self.tags(principal, project["id"])

    def pull(self, principal, project_id: Any) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_git:user", principal)
        repo = self._repo(project["id"])
        if self._git(project["id"], ["status", "--porcelain"], check=False).strip():
            raise dev_error("UNCOMMITTED_CHANGES", "Commit or discard your changes before pulling.", 409)
        self._git(project["id"], ["fetch", "--no-tags", "origin", repo["branch"]], repo=repo, network=True, timeout=120)
        has_head = self._git(project["id"], ["rev-parse", "--verify", "-q", "HEAD"], check=False).strip()
        self._git(project["id"], ["merge", "--ff-only", "-q", "FETCH_HEAD"] if has_head else ["checkout", "-q", "-B", repo["branch"], "FETCH_HEAD"], repo=repo)
        counts = self._rescan(project["id"], principal.user_id)
        self.event(project["id"], principal, "git.pulled", "repo", repo["id"], branch=repo["branch"], **counts)
        return {"pulled": True, "branch": repo["branch"], "files": counts}

    def push(self, principal, project_id: Any, branch: Any = None) -> Dict[str, Any]:
        project = self.open_project(principal, project_id, writable=True)
        self.limit("dev_git:user", principal)
        repo = self._repo(project["id"])
        if repo["consent_scope"] != "read_write":
            self.event(project["id"], principal, "git.push_denied", "repo", repo["id"], "denied", reason="read_only_consent")
            raise dev_error("GIT_READ_ONLY", "This repository was connected with read-only consent, so pushing is not allowed.", 403)
        cur = self._git(project["id"], ["branch", "--show-current"], check=False).strip()
        target = valid_branch(branch) if branch is not None else cur
        if not target or target != cur:
            raise invalid("branch", "Push the branch you are on.")
        if target in json.loads(repo["protected_branches"]):
            self.event(project["id"], principal, "git.push_denied", "repo", repo["id"], "denied", reason="protected_branch", branch=target)
            raise dev_error("PROTECTED_BRANCH", f"'{target}' is a protected branch and cannot be pushed from the workspace. Create a feature branch, push that, and merge it in your Git host.", 403)
        if repo["credential_secret_id"] is None and repo["provider"] != "local":
            raise dev_error("GIT_NO_CREDENTIAL", "No credential is stored for this repository, so pushing is not possible.", 409)
        self._git(project["id"], ["push", "-q", "origin", f"refs/heads/{target}:refs/heads/{target}"], repo=repo, network=True, timeout=120)
        self.event(project["id"], principal, "git.pushed", "repo", repo["id"], branch=target)
        return {"pushed": True, "branch": target}

    # ------------------------------------------------------------------ webhooks (no session: authenticated by signature)
    def webhook(self, repo_id: str, headers: Dict[str, str], body: bytes) -> Dict[str, Any]:
        """Verifies and de-duplicates a delivery. Every failure returns the same 401 so the endpoint reveals nothing about which repositories exist."""
        deny = dev_error("WEBHOOK_REJECTED", "The delivery was not accepted.", 401)
        h = {k.lower(): v for k, v in headers.items()}
        if len(body) > 1_000_000:
            raise dev_error("WEBHOOK_TOO_LARGE", "The delivery is too large.", 413)
        repo = self.conn.execute("SELECT * FROM dv_repos WHERE id = ? AND status = 'connected' AND webhook_secret_id IS NOT NULL", (repo_id,)).fetchone()
        if repo is None:
            raise deny
        try:
            secret = secretlib.resolve_values(self.conn, self.ctx.settings, self.now(), repo["project_id"], [HOOK_NAME], purposes=("webhook",))[HOOK_NAME]
        except Exception:  # noqa: BLE001
            raise deny
        sig, tok = h.get("x-hub-signature-256", ""), h.get("x-gitlab-token", "")
        if sig:
            ok = hmac.compare_digest(sig, "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest())
            delivery = h.get("x-github-delivery", "")
            event = h.get("x-github-event", "")
        elif tok:
            ok = hmac.compare_digest(tok.encode(), secret.encode())
            delivery = h.get("x-gitlab-event-uuid", "") or h.get("idempotency-key", "")
            event = {"Push Hook": "push", "Merge Request Hook": "pull_request"}.get(h.get("x-gitlab-event", ""), h.get("x-gitlab-event", ""))
        else:
            ok = False
        if not ok or not re.fullmatch(r"[A-Za-z0-9._-]{8,80}", delivery or ""):
            self.event(repo["project_id"], None, "git.webhook_rejected", "repo", repo["id"], "denied", reason="signature_or_delivery_id")
            raise deny
        if not self.ctx_entitled(repo["owner_user_id"]):
            raise deny
        try:
            self.conn.execute("INSERT INTO dv_webhook_deliveries (repo_id, delivery_id, received_at) VALUES (?,?,?)", (repo["id"], delivery, self.now()))
        except Exception:  # noqa: BLE001 - primary key = replay
            self.event(repo["project_id"], None, "git.webhook_replayed", "repo", repo["id"], "denied")
            raise dev_error("WEBHOOK_REPLAY", "This delivery was already processed.", 409)
        self.event(repo["project_id"], None, "git.webhook_accepted", "repo", repo["id"], event=event)
        trig = {"push": "commit", "pull_request": "pull_request"}.get(event)
        started = []
        if trig:
            from . import pipelines
            started = pipelines.trigger_from_webhook(self.ctx, self.ws, repo, trig)
        return {"accepted": True, "event": event, "pipeline_runs": started}

    def ctx_entitled(self, user_id: str) -> bool:
        from ..identity.authorization import build_principal
        from ..identity.repository import Repository
        from .common import has_dev_access
        repo = Repository(self.conn)
        u = repo.user_by_id(user_id)
        return bool(u and u["status"] == "active" and u["deleted_at"] is None and has_dev_access(build_principal(repo, u)))

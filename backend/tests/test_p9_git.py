"""Phase 9: Git. Real git against a real local bare repository (no network). Remote hosting providers (GitHub/GitLab over https) are NOT contacted by any test."""
import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace

from app.core.errors import AppError
from app.modules.devspace.files import FileService
from app.modules.devspace.git import GitService, check_remote, valid_branch
from p9_support import DevHarness

HAS_GIT = subprocess.run(["git", "--version"], capture_output=True).returncode == 0


def code(fn, *a, **k):
    try:
        fn(*a, **k)
    except AppError as e:
        return e.code, e.status_code
    raise AssertionError("expected an AppError")


def bare_repo(path, seed=True):
    quiet = {"check": True, "capture_output": True}
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", path], **quiet)
    if seed:
        work = tempfile.mkdtemp()
        env = {**os.environ, "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@x", "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": "x@x"}
        subprocess.run(["git", "init", "-q", "-b", "main", work], **quiet)
        with open(os.path.join(work, "README.md"), "w") as f:
            f.write("seed\n")
        subprocess.run(["git", "-C", work, "add", "-A"], env=env, **quiet)
        subprocess.run(["git", "-C", work, "commit", "-q", "-m", "seed"], env=env, **quiet)
        subprocess.run(["git", "-C", work, "push", "-q", path, "main"], env=env, **quiet)


@unittest.skipUnless(HAS_GIT, "needs the git program")
class Git(unittest.TestCase):
    def setUp(self):
        self.d = DevHarness()
        self.addCleanup(self.d.close)
        self.remote_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.remote_dir.cleanup)
        self.remote = os.path.join(self.remote_dir.name, "remote.git")
        bare_repo(self.remote)
        self.a = self.d.user("alice@example.com")
        self.pid = self.d.project(self.a)
        self.git, self.fs = self.d.svc(GitService), self.d.svc(FileService)

    def connect(self, scope="read_write", **kw):
        return self.git.connect(self.a, self.pid, provider="local", remote_url=self.remote, consent=True, consent_scope=scope, **kw)

    def test_remote_address_rules(self):
        s = self.d.h.settings
        for ok_url, provider in (("https://github.com/o/r.git", "github"), ("https://gitlab.com/o/r", "gitlab")):
            self.assertEqual(check_remote(s, provider, ok_url), ok_url)
        for url, provider in (("http://github.com/o/r", "github"), ("https://user:pw@github.com/o/r", "github"), ("https://token@github.com/o/r", "github"), ("ssh://git@github.com/o/r", "github"),
                              ("file:///etc", "github"), ("https://evil.example/o/r", "github"), ("https://github.com:8443/o/r", "github"), ("https://github.com/o/r?x=1", "github"),
                              ("https://github.com/o/r;rm -rf", "github"), ("ext::sh -c id", "github"), ("https://gitlab.com/o/r", "github")):
            with self.assertRaises(AppError, msg=url):
                check_remote(s, provider, url)
        hosted = replace(s, dev_git_allow_local=False)
        with self.assertRaises(AppError):
            check_remote(hosted, "local", self.remote)

    def test_branch_name_rules(self):
        for ok_name in ("main", "feature/new-model", "fix_1.2"):
            valid_branch(ok_name)
        for bad in ("", "-rf", "../x", "a b", "a..b", "x.lock", "a:b", "~x", "x/", "/x", "a\\b", "x" * 200):
            with self.assertRaises(AppError, msg=bad):
                valid_branch(bad)

    def test_consent_is_required_and_nothing_connects_without_it(self):
        self.assertEqual(code(self.git.connect, self.a, self.pid, provider="local", remote_url=self.remote)[1], 422)
        self.assertEqual(code(self.git.connect, self.a, self.pid, provider="local", remote_url=self.remote, consent="yes")[1], 422)
        self.assertIsNone(self.git.status(self.a, self.pid)["repo"])

    def test_commit_branch_tag_log_diff_and_history_event(self):
        self.git.init(self.a, self.pid)
        self.fs.write(self.a, self.pid, "train.py", "print('v1')\n", None)
        self.assertEqual(len(self.git.status(self.a, self.pid)["changes"]), 1)
        c1 = self.git.commit(self.a, self.pid, "first model script")
        self.assertEqual(len(c1["sha"]), 40)
        self.fs.write(self.a, self.pid, "train.py", "print('v2')\n", None)
        self.assertIn("+print('v2')", self.git.diff(self.a, self.pid)["diff"])
        self.git.create_branch(self.a, self.pid, "feature/x")
        self.git.commit(self.a, self.pid, "second change")
        self.git.tag(self.a, self.pid, "v0.1", "first tag")
        log = self.git.log(self.a, self.pid)["items"]
        self.assertEqual([i["subject"] for i in log], ["second change", "first model script"])
        self.assertTrue(all("@" not in i["author"] for i in log))
        self.assertEqual([t["name"] for t in self.git.tags(self.a, self.pid)["items"]], ["v0.1"])
        br = self.git.branches(self.a, self.pid)
        self.assertEqual(br["current"], "feature/x")
        self.assertEqual(code(self.git.commit, self.a, self.pid, "nothing changed")[0], "NOTHING_TO_COMMIT")
        # switching back changes the working tree, and the file index follows
        self.git.switch_branch(self.a, self.pid, "main")
        self.assertEqual(self.fs.read(self.a, self.pid, "train.py")["content"], "print('v1')\n")
        self.assertTrue(self.d.h.conn.execute("SELECT 1 FROM dv_events WHERE action = 'git.committed'").fetchone())

    def test_a_commit_that_would_include_a_credential_is_refused_and_unstaged(self):
        self.git.init(self.a, self.pid)
        tree = os.path.join(self.d.root, self.pid, "tree")
        with open(os.path.join(tree, "leak.py"), "w") as f:       # written outside the file API (as a pull or a job could)
            f.write("TOKEN = 'ghp_" + "b" * 36 + "'\n")
        with open(os.path.join(tree, ".env"), "w") as f:
            f.write("A=1\n")
        err = code(self.git.commit, self.a, self.pid, "add files")
        self.assertEqual(err[0], "SECRET_IN_COMMIT")
        staged = subprocess.run(["git", "-C", tree, "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout
        self.assertEqual(staged.strip(), "")
        self.assertTrue(self.d.h.conn.execute("SELECT 1 FROM dv_events WHERE action = 'git.commit_blocked'").fetchone())

    def test_pull_brings_remote_files_into_the_tree_and_index(self):
        self.connect()
        out = self.git.pull(self.a, self.pid)
        self.assertEqual(out["files"]["added"], 1)
        self.assertEqual(self.fs.read(self.a, self.pid, "README.md")["content"], "seed\n")
        self.assertEqual(self.git.log(self.a, self.pid)["items"][0]["subject"], "seed")

    def test_protected_branches_read_only_consent_and_force_are_enforced(self):
        self.connect()
        self.git.pull(self.a, self.pid)
        self.fs.write(self.a, self.pid, "a.py", "1\n", None)
        self.git.commit(self.a, self.pid, "work on main")
        self.assertEqual(code(self.git.push, self.a, self.pid)[0], "PROTECTED_BRANCH")
        self.git.create_branch(self.a, self.pid, "feature/ok")
        self.assertEqual(self.git.push(self.a, self.pid), {"pushed": True, "branch": "feature/ok"})
        heads = subprocess.run(["git", "-C", self.remote, "for-each-ref", "--format=%(refname:short)"], capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(heads), ["feature/ok", "main"])
        main_sha = subprocess.run(["git", "-C", self.remote, "rev-parse", "main"], capture_output=True, text=True).stdout.strip()
        seed = self.git.log(self.a, self.pid)["items"][-1]["sha"]
        self.assertEqual(main_sha, seed)                                    # main on the remote never moved
        self.assertEqual(code(self.git.push, self.a, self.pid, "other")[1], 422)    # only the current branch can be pushed
        self.git.disconnect(self.a, self.pid)
        self.connect(scope="read")
        self.assertEqual(code(self.git.push, self.a, self.pid)[0], "GIT_READ_ONLY")

    def test_uncommitted_changes_block_pull_and_switch(self):
        self.connect()
        self.git.pull(self.a, self.pid)
        self.fs.write(self.a, self.pid, "dirty.py", "1\n", None)
        self.assertEqual(code(self.git.pull, self.a, self.pid)[0], "UNCOMMITTED_CHANGES")
        self.git.create_branch(self.a, self.pid, "other")
        self.assertEqual(code(self.git.switch_branch, self.a, self.pid, "main")[0], "UNCOMMITTED_CHANGES")

    def test_credentials_are_encrypted_never_in_git_config_logs_or_events(self):
        token = "ghp_" + "c" * 36
        self.git.connect(self.a, self.pid, provider="local", remote_url=self.remote, consent=True, consent_scope="read_write", credential=token)
        tree = os.path.join(self.d.root, self.pid, "tree")
        with open(os.path.join(tree, ".git", "config")) as f:
            self.assertNotIn(token, f.read())
        self.assertNotIn(token, self.d.h.conn.execute("SELECT ciphertext FROM dv_secrets").fetchone()[0])
        self.git.pull(self.a, self.pid)
        dump = " ".join(str(dict(r)) for r in self.d.h.conn.execute("SELECT * FROM dv_events").fetchall())
        self.assertNotIn(token, dump)
        self.assertNotIn(token, str(self.git.status(self.a, self.pid)))
        # git secrets cannot be handed to a job
        self.assertEqual(code(self.d.svc(__import__("app.modules.devspace.engine", fromlist=["JobService"]).JobService).submit, self.a, self.pid, kind="command", argv=["python", "-c", "1"], secret_refs=["GIT_CREDENTIAL"])[1], 404)
        self.git.disconnect(self.a, self.pid)
        self.assertEqual(self.d.h.conn.execute("SELECT COUNT(*) FROM dv_secrets WHERE ciphertext != ''").fetchone()[0], 0)

    def test_other_users_cannot_use_the_repository(self):
        bob = self.d.user("bob@example.com")
        self.connect()
        for call in (lambda: self.git.status(bob, self.pid), lambda: self.git.pull(bob, self.pid), lambda: self.git.log(bob, self.pid)):
            self.assertEqual(code(call)[1], 404)

    def test_repository_hooks_never_run(self):
        self.connect()
        self.git.pull(self.a, self.pid)
        marker = os.path.join(self.remote_dir.name, "hook-ran")
        hook = os.path.join(self.d.root, self.pid, "tree", ".git", "hooks", "pre-commit")
        with open(hook, "w") as f:
            f.write(f"#!/bin/sh\ntouch {marker}\n")
        os.chmod(hook, 0o755)
        self.fs.write(self.a, self.pid, "h.py", "1\n", None)
        self.git.commit(self.a, self.pid, "trigger hook?")
        self.assertFalse(os.path.exists(marker))


@unittest.skipUnless(HAS_GIT, "needs the git program")
class Webhooks(unittest.TestCase):
    def setUp(self):
        self.d = DevHarness()
        self.addCleanup(self.d.close)
        self.remote_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.remote_dir.cleanup)
        self.remote = os.path.join(self.remote_dir.name, "r.git")
        bare_repo(self.remote)
        self.a = self.d.user("alice@example.com")
        self.pid = self.d.project(self.a)
        self.git = self.d.svc(GitService)
        self.git.connect(self.a, self.pid, provider="local", remote_url=self.remote, consent=True, consent_scope="read", webhook_secret="hook-secret-value")
        self.repo = self.d.h.conn.execute("SELECT id FROM dv_repos").fetchone()[0]
        self.body = json.dumps({"ref": "refs/heads/main"}).encode()

    def headers(self, body=None, secret="hook-secret-value", delivery="abcd1234efgh", event="push"):
        body = self.body if body is None else body
        return {"X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), "X-GitHub-Delivery": delivery, "X-GitHub-Event": event}

    def test_valid_delivery_is_accepted_once_and_replay_is_refused(self):
        self.assertTrue(self.git.webhook(self.repo, self.headers(), self.body)["accepted"])
        self.assertEqual(code(self.git.webhook, self.repo, self.headers(), self.body), ("WEBHOOK_REPLAY", 409))
        self.assertTrue(self.git.webhook(self.repo, self.headers(delivery="another-delivery-2"), self.body)["accepted"])

    def test_bad_signature_wrong_secret_tampered_body_and_missing_headers_all_get_the_same_401(self):
        cases = [(self.headers(secret="wrong"), self.body), (self.headers(), self.body + b" "), ({"X-GitHub-Delivery": "abcd1234efgh"}, self.body), (self.headers(delivery="x"), self.body),
                 ({"X-Gitlab-Token": "wrong", "X-Gitlab-Event-UUID": "uuid-123456789"}, self.body)]
        for h, b in cases:
            self.assertEqual(code(self.git.webhook, self.repo, h, b), ("WEBHOOK_REJECTED", 401))
        self.assertEqual(code(self.git.webhook, "no-such-repo", self.headers(), self.body), ("WEBHOOK_REJECTED", 401))        # indistinguishable from a bad signature
        self.assertEqual(self.d.h.conn.execute("SELECT COUNT(*) FROM dv_webhook_deliveries").fetchone()[0], 0)

    def test_gitlab_style_token_works_and_oversized_bodies_are_refused(self):
        h = {"X-Gitlab-Token": "hook-secret-value", "X-Gitlab-Event-UUID": "uuid-123456789", "X-Gitlab-Event": "Push Hook"}
        self.assertEqual(self.git.webhook(self.repo, h, self.body)["event"], "push")
        self.assertEqual(code(self.git.webhook, self.repo, self.headers(), b"x" * 1_000_001)[1], 413)

    def test_webhook_after_disconnect_or_access_loss_is_rejected(self):
        c = self.d.h.conn
        c.execute("UPDATE entitlements SET revoked_at = ? WHERE user_id = ?", (self.d.h.clock.iso(), self.a.user_id))
        self.assertEqual(code(self.git.webhook, self.repo, self.headers(), self.body)[1], 401)
        c.execute("UPDATE entitlements SET revoked_at = NULL WHERE user_id = ?", (self.a.user_id,))
        self.git.disconnect(self.a, self.pid)
        self.assertEqual(code(self.git.webhook, self.repo, self.headers(delivery="zzzz99999999"), self.body)[1], 401)


if __name__ == "__main__":
    unittest.main()

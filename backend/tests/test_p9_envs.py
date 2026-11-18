"""Phase 9: reproducible environments. Packages come from a local wheelhouse built in the test (no network, no package index)."""
import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from dataclasses import replace

from app.core.errors import AppError
from app.modules.devspace import envs
from app.modules.devspace.engine import JobService
from app.modules.devspace.envs import EnvService, canonical, parse_requirements
from app.modules.devspace.files import FileService
from p9_support import DevHarness


def code(fn, *a, **k):
    try:
        fn(*a, **k)
    except AppError as e:
        return e.code, e.status_code
    raise AssertionError("expected an AppError")


def make_wheel(folder, name, version, body):
    dist = f"{name}-{version}.dist-info"
    files = {f"{name}/__init__.py": body, f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", f"{dist}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    record = "".join(f"{p},sha256={hashlib.sha256(c.encode()).hexdigest()},{len(c)}\n" for p, c in files.items()) + f"{dist}/RECORD,,\n"
    files[f"{dist}/RECORD"] = record
    path = os.path.join(folder, f"{name}-{version}-py3-none-any.whl")
    with zipfile.ZipFile(path, "w") as z:
        for p, c in files.items():
            z.writestr(p, c)
    return path


class Requirements(unittest.TestCase):
    def test_plain_pins_and_ranges_are_accepted_and_canonicalised(self):
        self.assertEqual(canonical(parse_requirements("# c\nrequests>=2.0,<3\nnumpy==1.26.4\npandas[excel]>=2\n")), "numpy==1.26.4\npandas[excel]>=2\nrequests>=2.0,<3\n")

    def test_anything_that_could_fetch_elsewhere_or_run_code_is_rejected(self):
        for bad in ("-r other.txt", "--index-url https://evil.example/simple", "-e .", "git+https://github.com/x/y.git", "https://evil.example/p.whl", "./local", "/abs/path", "pkg @ https://x/y.whl",
                    "pkg; python_version<'3'", "pkg==1.0 --hash=sha256:aa", "bad name", "x" * 200, "numpy\nnumpy"):
            with self.assertRaises(AppError, msg=bad):
                parse_requirements(bad)


class Environments(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        make_wheel(self.tmp.name, "tinypkg", "1.0", "VALUE = 42\n")
        make_wheel(self.tmp.name, "tinypkg", "2.0", "VALUE = 99\n")
        os.environ["PREDICTAI_DEV_WHEELHOUSE"] = self.tmp.name
        self.addCleanup(os.environ.pop, "PREDICTAI_DEV_WHEELHOUSE", None)
        self.d = DevHarness()
        self.addCleanup(self.d.close)
        self.a = self.d.user("alice@example.com")
        self.pid = self.d.project(self.a)
        self.env, self.fs, self.js = self.d.svc(EnvService), self.d.svc(FileService), self.d.svc(JobService)

    def build(self, e):
        self.d.run_all()
        return self.env.get(self.a, self.pid, e["id"])

    def test_build_lockfile_and_job_uses_the_environment(self):
        e = self.env.create(self.a, self.pid, name="main", requirements="tinypkg==1.0\n")
        self.assertEqual(e["versions"][0]["status"], "building")
        e = self.build(e)
        v = e["versions"][0]
        self.assertEqual((v["status"], e["current_version"]), ("ready", 1))
        self.assertIn("tinypkg==1.0", v["lockfile"])
        self.assertEqual(v["report"]["source"], "wheelhouse")
        self.assertEqual(v["report"]["conflicts"], [])
        self.assertEqual(v["report"]["vulnerability_check"]["status"], "unavailable")             # never reported clean when it did not run
        self.fs.write(self.a, self.pid, "use.py", "import tinypkg\nprint(tinypkg.VALUE)\n", None)
        job = self.js.submit(self.a, self.pid, kind="script", entry="use.py", env_id=e["id"])
        self.d.run_all()
        got = self.js.get(self.a, self.pid, job["id"])
        self.assertEqual((got["status"], got["env_version"]), ("succeeded", 1))
        self.assertEqual(self.js.logs(self.a, self.pid, job["id"])["stdout"]["text"].strip(), "42")

    def test_change_creates_a_new_immutable_version_and_compare_shows_it(self):
        e = self.build(self.env.create(self.a, self.pid, name="main", requirements="tinypkg==1.0\n"))
        e = self.build(self.env.add_package(self.a, self.pid, e["id"], "tinypkg==2.0"))
        self.assertEqual((e["current_version"], len(e["versions"])), (2, 2))
        cmp = self.env.compare(self.a, self.pid, e["id"], 1, 2)
        self.assertEqual(cmp["basis"], "lockfile")
        self.assertEqual(cmp["changed"], [{"package": "tinypkg", "from": "1.0", "to": "2.0"}])
        # job pinned to the version it was submitted with
        self.fs.write(self.a, self.pid, "use.py", "import tinypkg\nprint(tinypkg.VALUE)\n", None)
        job = self.js.submit(self.a, self.pid, kind="script", entry="use.py", env_id=e["id"])
        self.d.run_all()
        self.assertEqual(self.js.logs(self.a, self.pid, job["id"])["stdout"]["text"].strip(), "99")
        removed = self.build(self.env.remove_package(self.a, self.pid, e["id"], "tinypkg"))
        self.assertEqual(removed["versions"][0]["requirements"], "")
        self.assertEqual(code(self.env.remove_package, self.a, self.pid, e["id"], "tinypkg")[1], 404)

    def test_a_failed_build_is_reported_and_the_previous_version_keeps_working(self):
        e = self.build(self.env.create(self.a, self.pid, name="main", requirements="tinypkg==1.0\n"))
        e = self.build(self.env.set_requirements(self.a, self.pid, e["id"], "doesnotexist==9.9\n"))
        bad = e["versions"][0]
        self.assertEqual(bad["status"], "failed")
        self.assertIn("Installing the packages failed", bad["error"])
        self.assertEqual(e["current_version"], 1)                       # still the last good version
        self.fs.write(self.a, self.pid, "use.py", "import tinypkg\nprint(tinypkg.VALUE)\n", None)
        job = self.js.submit(self.a, self.pid, kind="script", entry="use.py", env_id=e["id"])
        self.d.run_all()
        self.assertEqual(self.js.get(self.a, self.pid, job["id"])["status"], "succeeded")

    def test_job_cannot_use_an_environment_that_is_not_ready_or_belongs_to_someone_else(self):
        e = self.env.create(self.a, self.pid, name="main", requirements="tinypkg==1.0\n")
        self.fs.write(self.a, self.pid, "use.py", "print(1)\n", None)
        self.assertEqual(code(self.js.submit, self.a, self.pid, kind="script", entry="use.py", env_id=e["id"])[0], "ENVIRONMENT_NOT_READY")
        self.build(e)
        bob = self.d.user("bob@example.com")
        bpid = self.d.project(bob)
        self.d.svc(FileService).write(bob, bpid, "use.py", "print(1)\n", None)
        self.assertEqual(code(self.d.svc(JobService).submit, bob, bpid, kind="script", entry="use.py", env_id=e["id"])[1], 404)
        self.assertEqual(code(self.d.svc(EnvService).get, bob, self.pid, e["id"])[1], 404)

    def test_rebuild_limit_quota_and_unavailable_sources(self):
        e = self.build(self.env.create(self.a, self.pid, name="main", requirements=""))
        self.assertEqual(self.build(self.env.rebuild(self.a, self.pid, e["id"]))["current_version"], 2)
        self.assertEqual(code(self.env.create, self.a, self.pid, name="MAIN")[0], "CONFLICT")
        for i in range(4):
            self.env.create(self.a, self.pid, name=f"e{i}")
        self.assertEqual(code(self.env.create, self.a, self.pid, name="one-too-many")[0], "ENV_LIMIT")
        self.assertEqual(code(self.env.create, self.a, self.pid, name="py", python_version="2.7")[1], 422)
        os.environ.pop("PREDICTAI_DEV_WHEELHOUSE")
        self.d.h.settings = replace(self.d.h.settings, dev_pip_index_urls="")
        self.assertEqual(code(self.d.svc(EnvService).rebuild, self.a, self.pid, e["id"])[0], "NO_PACKAGE_SOURCE")
        self.d.h.settings = replace(self.d.h.settings, dev_execution="off")
        self.assertEqual(code(self.d.svc(EnvService).rebuild, self.a, self.pid, e["id"])[0], "EXECUTION_DISABLED")

    def test_delete_needs_the_name(self):
        e = self.build(self.env.create(self.a, self.pid, name="main"))
        self.assertEqual(code(self.env.delete, self.a, self.pid, e["id"], "wrong")[1], 422)
        self.env.delete(self.a, self.pid, e["id"], "main")
        self.assertEqual(self.env.list(self.a, self.pid)["items"], [])
        self.assertFalse(os.path.exists(os.path.join(self.d.root, self.pid, "envs", e["id"])))


if __name__ == "__main__":
    unittest.main()

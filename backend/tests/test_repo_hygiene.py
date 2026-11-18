"""Packaging guard rails: templates hold placeholders only, secrets stay out of version control."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRET_VARS = ("SECRET_KEY", "PASSWORD", "CLIENT_SECRET", "CLIENT_ID", "USERNAME")


def assignments(path: Path):
    """Active (uncommented) KEY=value lines of an env template."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            out[match.group(1)] = match.group(2).split("#")[0].strip()
    return out


class EnvTemplateTests(unittest.TestCase):
    def test_root_template_matches_the_config_template(self):
        self.assertEqual((ROOT / ".env.example").read_text(), (ROOT / "config" / "env.example").read_text())

    def test_templates_contain_no_real_secrets(self):
        for name in (".env.example", "config/env.example", "config/env.production.example"):
            for key, value in assignments(ROOT / name).items():
                if any(token in key for token in SECRET_VARS):
                    self.assertTrue(value == "" or value.startswith("<"), f"{name}: {key} must be empty or a <placeholder>")

    # A developer's own .env, data/ and databases are legitimate in a working directory. What must never happen is that they are
    # tracked by git or packaged into a release, so that is what these tests check (they pass with or without a local .env).
    def test_local_secrets_are_ignored_by_git(self):
        import shutil
        import subprocess
        if shutil.which("git") and (ROOT / ".git").exists():
            for candidate in (".env", ".env.local", "data/predictai.db", "data/dev-secret-key", "backend/data/x.db", "mail.eml"):
                done = subprocess.run(["git", "check-ignore", "-q", candidate], cwd=ROOT)
                self.assertEqual(done.returncode, 0, f"{candidate} is not ignored by git")
            for template in (".env.example", "config/env.example", "config/env.production.example"):
                done = subprocess.run(["git", "check-ignore", "-q", template], cwd=ROOT)
                self.assertNotEqual(done.returncode, 0, f"{template} must stay trackable")

    def test_no_secret_or_database_file_is_tracked_by_git(self):
        import shutil
        import subprocess
        if not (shutil.which("git") and (ROOT / ".git").exists()):
            return                                              # not a git checkout (for example an extracted release): the archive test below covers it
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
        forbidden = [f for f in tracked if re.search(r"(^|/)(\.env(\..*)?|dev-secret-key|[^/]*\.(db|db-wal|db-shm|eml|pem|key))$", f) and not f.endswith(".example")]
        forbidden += [f for f in tracked if f.startswith("data/")]
        self.assertEqual(forbidden, [])

    def test_release_archive_never_contains_local_config_or_data(self):
        import importlib.util
        import tempfile
        import zipfile
        spec = importlib.util.spec_from_file_location("package_release", ROOT / "scripts" / "package_release.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "proj"
            files = {".env": "PREDICTAI_SECRET_KEY=real-looking-secret\n", ".env.local": "X=1\n", ".env.example": "A=<x>\n", "config/env.example": "A=<x>\n",
                     "data/predictai.db": "db", "data/dev-secret-key": "k", "backend/data/x.db": "db", "mail/a.eml": "m", "certs/server.pem": "p", "id.key": "k",
                     "frontend/node_modules/a/index.js": "x", "backend/app/__pycache__/a.pyc": "x", "frontend/dist/app.js": "x", ".git/config": "x", ".DS_Store": "x",
                     "backend/app/main.py": "print(1)\n", "README.md": "# hi\n", "frontend/src/main.jsx": "x"}
            for name, text in files.items():
                path = project / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            before = (project / ".env").read_text()
            out = Path(tmp) / "release.zip"
            module.build_archive(project, out)
            names = zipfile.ZipFile(out).namelist()
            self.assertEqual(sorted(n.split("/", 1)[1] for n in names), sorted(["README.md", "backend/app/main.py", "config/env.example", ".env.example", "frontend/src/main.jsx"]))
            self.assertEqual((project / ".env").read_text(), before)            # the user's .env was neither deleted nor altered
            self.assertTrue((project / "data" / "predictai.db").exists())
            self.assertFalse(any("secret" in zipfile.ZipFile(out).read(n).decode("utf-8", "ignore").lower() for n in names))

    def test_the_real_project_tree_would_package_without_secrets(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("package_release", ROOT / "scripts" / "package_release.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        names = [str(p.relative_to(ROOT)) for p in module.release_files(ROOT)]
        for name in names:
            base = name.rsplit("/", 1)[-1]
            self.assertFalse(base == ".env" or (base.startswith(".env.") and not base.endswith(".example")), name)
            self.assertFalse(name.startswith("data/") or "/data/" in name or base.endswith((".db", ".eml", ".pem", ".key")) or base == "dev-secret-key", name)
        self.assertIn(".env.example", names)

    def test_gitignore_protects_secrets_but_keeps_templates(self):
        text = (ROOT / ".gitignore").read_text()
        for needed in (".env", "data/", "*.db", "node_modules/", "__pycache__/", ".venv/", "dist/"):
            self.assertIn(needed, text)
        self.assertIn("!.env.example", text)


if __name__ == "__main__":
    unittest.main()

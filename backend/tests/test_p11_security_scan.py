import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("security_scan", Path(__file__).resolve().parents[2] / "scripts" / "security_scan.py")
S = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(S)


class Scan(unittest.TestCase):
    def test_the_real_tree_has_no_high_or_medium_findings(self):
        res = S.scan(S.ROOT)
        bad = [f for f in res["findings"] if f["severity"] in ("HIGH", "MEDIUM")]
        self.assertEqual(bad, [])
        self.assertEqual(res["unbounded_dependencies"], [])

    def test_every_reviewed_exception_has_a_reason(self):
        for rule, frag, why in S.ALLOW:
            self.assertGreater(len(why.strip()), 3, (rule, frag))

    def test_the_scanner_actually_catches_problems(self):
        d = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, d, True)
        (d / "backend").mkdir(); (d / "config").mkdir()
        (d / "backend" / "a.py").write_text('import subprocess, pickle\nsubprocess.run("ls", shell=True)\nx = eval(input())\npickle.loads(b"")\nrequests.get(u, verify=False)\n'
                                             'key = "AKI' + 'AABCDEFGHIJKLMNOP"\n')
        (d / "backend" / "requirements.txt").write_text("somepkg>=1.0\nboundedpkg>=1,<2\n")
        (d / "frontend").mkdir(); (d / "frontend" / "x.jsx").write_text("const a = <div dangerouslySetInnerHTML={{__html: h}} />; localStorage.setItem('token', t);")
        (d / ".env").write_text("A=1"); (d / "id_rsa").write_text("x")
        (d / "config" / "env.production.example").write_text("PREDICTAI_DEBUG=true\nPREDICTAI_CORS_ORIGINS=*\n")
        rules = {f["rule"] for f in S.scan(d)["findings"]}
        for want in ("shell=True", "eval()", "pickle load", "TLS verification disabled", "AWS access key id", "dangerouslySetInnerHTML / innerHTML",
                     "token kept in browser storage", "file that should never be committed", "unsafe configuration template"):
            self.assertIn(want, rules)
        self.assertEqual(S.scan(d)["unbounded_dependencies"], ["requirements.txt: somepkg>=1.0"])


if __name__ == "__main__":
    unittest.main()

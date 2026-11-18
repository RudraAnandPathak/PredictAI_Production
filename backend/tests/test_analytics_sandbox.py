"""Verifies what the process controls actually do on THIS machine (real child processes), plus the logic that cannot be exercised on
every OS (injected memory readings, a pretend platform without kernel memory limits)."""
import os
import pathlib
import sys
import time
import unittest
from unittest import mock

from app.modules.analytics import executor, sandbox

PY = [sys.executable, "-I", "-c"]
ENV = {"PATH": "/usr/bin:/bin"}
HUNGRY = "b=bytearray(400*1024*1024)\nfor i in range(0,len(b),4096): b[i]=1\nimport time; time.sleep(30)"


class MeasuringMemory(unittest.TestCase):
    def test_resident_memory_of_a_live_process_is_readable(self):
        value = sandbox.rss_bytes(os.getpid())
        self.assertIsNotNone(value)
        self.assertGreater(value, 5 * 1024 * 1024)

    def test_ps_fallback_works_where_ps_exists(self):
        if not os.path.exists("/bin/ps"):
            self.skipTest("no /bin/ps on this system (the /proc reader is used instead)")
        value = sandbox._rss_ps(os.getpid())
        self.assertIsNotNone(value)
        self.assertGreater(value, 5 * 1024 * 1024)

    def test_peak_is_reported_in_bytes(self):
        peak = sandbox.peak_rss_bytes()
        self.assertGreater(peak, 5 * 1024 * 1024)          # would be ~1000x too small if kilobytes were mistaken for bytes
        self.assertLess(peak, 64 * 1024 ** 3)

    def test_a_gone_process_reads_as_none_not_zero(self):
        self.assertIsNone(sandbox.rss_bytes(2 ** 22 + 12345))


class WatchdogKillsRealProcesses(unittest.TestCase):
    def test_child_exceeding_memory_is_killed_and_reported(self):
        t0 = time.monotonic()
        out = sandbox.run_limited(PY + [HUNGRY], cwd="/tmp", env=ENV, timeout=25, memory_bytes=150 * 1024 * 1024)
        self.assertEqual(out.killed, "memory")
        self.assertNotEqual(out.returncode, 0)
        self.assertLess(time.monotonic() - t0, 15)          # stopped by the watchdog, not by waiting out the child's 30 s sleep
        self.assertGreater(out.peak_rss, 150 * 1024 * 1024)

    def test_child_within_the_limit_finishes_normally(self):
        out = sandbox.run_limited(PY + ["print('fine')"], cwd="/tmp", env=ENV, timeout=20, memory_bytes=400 * 1024 * 1024)
        self.assertIsNone(out.killed)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, b"fine"))

    def test_wall_clock_timeout_kills_the_child(self):
        out = sandbox.run_limited(PY + ["import time; time.sleep(30)"], cwd="/tmp", env=ENV, timeout=0.5, memory_bytes=400 * 1024 * 1024)
        self.assertEqual(out.killed, "time")

    def test_grandchildren_die_with_the_process_group(self):
        code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); time.sleep(30)"
        out = sandbox.run_limited(PY + [code], cwd="/tmp", env=ENV, timeout=0.8, memory_bytes=400 * 1024 * 1024)
        self.assertEqual(out.killed, "time")

    def test_large_output_does_not_deadlock(self):
        out = sandbox.run_limited(PY + ["import sys; sys.stdout.write('x'*5_000_000)"], cwd="/tmp", env=ENV, timeout=20, memory_bytes=400 * 1024 * 1024)
        self.assertEqual((out.killed, len(out.stdout)), (None, 5_000_000))

    def test_decision_logic_with_injected_readings_matches_a_platform_without_kernel_limits(self):
        # Simulates macOS: the kernel never refuses the allocation, so only the sampled readings can stop the child.
        readings = iter([10 * 1024 * 1024, 30 * 1024 * 1024, 500 * 1024 * 1024])
        out = sandbox.run_limited(PY + ["import time; time.sleep(30)"], cwd="/tmp", env=ENV, timeout=25, memory_bytes=100 * 1024 * 1024, sampler=lambda pid: next(readings, 500 * 1024 * 1024))
        self.assertEqual(out.killed, "memory")

    def test_unreadable_memory_never_kills_a_healthy_child(self):
        out = sandbox.run_limited(PY + ["import time; time.sleep(0.3); print('ok')"], cwd="/tmp", env=ENV, timeout=20, memory_bytes=1024, sampler=lambda pid: None)
        self.assertEqual((out.killed, out.returncode), (None, 0))


class KernelLimitProbe(unittest.TestCase):
    def test_probe_matches_reality_on_this_machine(self):
        claimed = sandbox.hard_memory_limit_supported()
        child = ("import resource,sys\nlim=512*1024*1024\n"
                 "try:\n    resource.setrlimit(resource.RLIMIT_AS,(lim,lim))\nexcept Exception:\n    print('unsettable'); sys.exit(0)\n"
                 "try:\n    bytearray(2*lim)\n    print('allowed')\nexcept MemoryError:\n    print('refused')\n")
        said = sandbox.run_limited(PY + [child], cwd="/tmp", env=ENV, timeout=20, memory_bytes=2 * 1024 ** 3).stdout.strip()
        self.assertEqual(claimed, said == b"refused")
        if sys.platform.startswith("linux"):
            self.assertTrue(claimed)                         # Linux enforces RLIMIT_AS; a False here would mean the probe is broken
        if sys.platform == "darwin":
            self.assertFalse(claimed)                        # documented platform behaviour: macOS does not refuse allocations under RLIMIT_AS

    def test_a_platform_without_a_kernel_limit_is_reported_as_monitored_not_hard(self):
        with mock.patch.object(sandbox, "hard_memory_limit_supported", return_value=False):
            info = sandbox.describe_memory(1536)
        self.assertEqual(info["mode"], "monitored")
        self.assertIn("Best effort", info["note"])
        self.assertIn("unavailable", info["kernel_limit"])
        with mock.patch.object(sandbox, "hard_memory_limit_supported", return_value=True):
            self.assertEqual(sandbox.describe_memory(1536)["mode"], "hard")

    def test_the_whole_analysis_is_still_stopped_when_the_kernel_limit_is_missing(self):
        # Pretend to be macOS end to end: no kernel limit, so the watchdog alone must stop an over-limit analysis and the report must not claim otherwise.
        import analytics_support as sup
        lim = dict(executor.limits_for(False), memory_mb=40)
        with mock.patch.object(sandbox, "hard_memory_limit_supported", return_value=False), mock.patch.object(executor.sandbox, "hard_memory_limit_supported", return_value=False):
            with self.assertRaises(Exception) as cm:
                sup.run_engine(sup.synth(n=500), [{"op": "dataset_summary", "params": {}}], limits=lim, isolation="subprocess")
        self.assertEqual(getattr(cm.exception, "code", ""), "ANALYSIS_MEMORY")


class WorkerLauncher(unittest.TestCase):
    def test_limits_are_applied_before_heavy_imports_and_secrets_are_not_inherited(self):
        probe = ("import sys\nsys.argv=['x']\nimport os\nprint(sorted(k for k in os.environ if 'SECRET' in k or 'KEY' in k))")
        with mock.patch.dict(os.environ, {"PREDICTAI_SECRET_KEY": "s3cret"}):
            out = sandbox.run_limited(PY + [probe], cwd="/tmp", env=ENV, timeout=20, memory_bytes=400 * 1024 * 1024)
        self.assertEqual(out.stdout.strip(), b"[]")
        src = (pathlib.Path(executor.__file__).with_name("_boot.py")).read_text(encoding="utf-8")
        self.assertLess(src.index("RLIMIT_AS"), src.index("import worker"))
        self.assertIn("if kernel_as", src)
        for name in ("RLIMIT_CPU", "RLIMIT_FSIZE", "RLIMIT_NOFILE", "RLIMIT_CORE"):
            self.assertIn(name, src)


if __name__ == "__main__":
    unittest.main()

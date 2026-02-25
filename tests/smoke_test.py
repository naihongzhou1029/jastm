"""
Smoke tests for jastm. Run from project root: python -m pytest tests/smoke_test.py -v
Or: python tests/smoke_test.py
Implements tests from tests/smoke_test.md.
"""

import csv
import glob
import os
import re
import signal
import subprocess
import sys
import time
import unittest

# Project root: parent of tests/
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
JASTM_PY = os.path.join(PROJECT_ROOT, "jastm.py")
FIXTURES_DIR = os.path.join(TESTS_DIR, "fixtures")
SAMPLE_CSV = os.path.join(FIXTURES_DIR, "smoke_sample.csv")

REQUIRED_CLI_OPTIONS = [
    "--parse-file",
    "--process-name",
    "--process-id",
    "--program",
    "--sample-rate",
    "--summary",
    "--metrices-window",
    "--cpu-peak-percentage",
    "--ram-peak-percentage",
]


def run_jastm(args, cwd=None, timeout=None, capture=True):
    """Run jastm.py with given args. Returns (returncode, stdout, stderr)."""
    cmd = [sys.executable, JASTM_PY] + args
    kw = {"cwd": cwd or PROJECT_ROOT, "capture_output": capture, "text": True}
    if timeout:
        kw["timeout"] = timeout
    try:
        r = subprocess.run(cmd, **kw)
        return (r.returncode, r.stdout or "", r.stderr or "")
    except subprocess.TimeoutExpired as e:
        proc = getattr(e, "process", None)
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        return (-1, getattr(e, "stdout", "") or "", getattr(e, "stderr", "") or "")


def find_recent_monitor_csv(cwd, within_seconds=30, name_contains=None):
    """Return path to a *_monitor.csv in cwd modified within within_seconds. Optionally filter by name_contains."""
    pattern = os.path.join(cwd, "*_monitor.csv")
    now = time.time()
    candidates = [
        p for p in glob.glob(pattern)
        if now - os.path.getmtime(p) <= within_seconds
    ]
    if name_contains:
        candidates = [p for p in candidates if name_contains in os.path.basename(p)]
    return max(candidates, key=os.path.getmtime) if candidates else None


def cleanup_monitor_csvs_created_after(cwd, after_timestamp):
    """Remove *_monitor.csv in cwd that were modified after after_timestamp (e.g. created during this test run)."""
    pattern = os.path.join(cwd, "*_monitor.csv")
    for path in glob.glob(pattern):
        try:
            if os.path.getmtime(path) >= after_timestamp:
                os.remove(path)
        except OSError:
            pass


class TestHelpAndCLI(unittest.TestCase):
    """Spec section 1: Help and CLI."""

    def test_1_1_help_output(self):
        """Exit 0; usage and required options visible."""
        code, out, err = run_jastm(["--help"])
        self.assertEqual(code, 0, f"Expected exit 0, got {code}. stderr: {err}")
        combined = out + err
        for opt in REQUIRED_CLI_OPTIONS:
            self.assertIn(opt, combined, f"Help should mention {opt}")


class TestOptionValidation(unittest.TestCase):
    """Spec section 2: Option validation."""

    def test_2_1_reject_sample_rate_zero(self):
        code, _, err = run_jastm(["--sample-rate", "0"])
        self.assertNotEqual(code, 0)
        self.assertIn("--sample-rate", err)

    def test_2_2_reject_sample_rate_negative(self):
        code, _, err = run_jastm(["--sample-rate", "-1"])
        self.assertNotEqual(code, 0)
        self.assertIn("--sample-rate", err)

    def test_2_3_reject_analysis_plus_process_name(self):
        code, _, err = run_jastm(["--parse-file", "x.csv", "--process-name", "python.exe"])
        self.assertNotEqual(code, 0)
        self.assertIn("Analysis Mode", err)

    def test_2_4_reject_analysis_plus_process_id(self):
        code, _, err = run_jastm(["--parse-file", "x.csv", "--process-id", "12345"])
        self.assertNotEqual(code, 0)
        self.assertIn("Analysis Mode", err)

    def test_2_5_reject_analysis_plus_program(self):
        code, _, err = run_jastm(["--parse-file", "x.csv", "--program", "notepad.exe"])
        self.assertNotEqual(code, 0)
        self.assertIn("Analysis Mode", err)

    def test_2_6_reject_empty_program(self):
        code, _, err = run_jastm(["--program"])
        self.assertNotEqual(code, 0)
        self.assertIn("--program", err)


class TestDataCollection(unittest.TestCase):
    """Spec section 3: Data collection (short runs). Process started then terminated after a few samples."""

    def run_collection_for_seconds(self, args, seconds=2.5):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            [sys.executable, "-u", JASTM_PY] + args,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        try:
            out, _ = proc.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                out, _ = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate(timeout=1)
        return out or "", proc.returncode

    def test_3_1_system_wide_collection_starts(self):
        """Logging message and CSV with header + at least one data row."""
        out, _ = self.run_collection_for_seconds(["--sample-rate", "0.5"])
        if out:
            self.assertIn("Logging to:", out)
        path = find_recent_monitor_csv(PROJECT_ROOT)
        self.assertIsNotNone(path, "Expected a recent *_monitor.csv in project root")
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
        self.assertGreaterEqual(len(rows), 2, "CSV should have header + at least one data row")
        self.assertEqual(rows[0], ["Timestamp", "CPU_Usage_%", "Memory_MB"])

    def test_3_2_process_name_filter(self):
        """Log filename includes process name (e.g. python_…_monitor.csv)."""
        out, _ = self.run_collection_for_seconds(["--process-name", "python.exe", "--sample-rate", "0.5"])
        if out:
            self.assertIn("Logging to:", out)
        path = find_recent_monitor_csv(PROJECT_ROOT, name_contains="python")
        self.assertIsNotNone(path, "Expected a recent python_*_monitor.csv in project root")
        basename = os.path.basename(path)
        self.assertIn("python", basename.lower(), f"Log filename should contain process name: {basename}")

    def test_3_3_pid_filter(self):
        """Log filename includes PID<id>."""
        pid = os.getpid()
        out, _ = self.run_collection_for_seconds(["--process-id", str(pid), "--sample-rate", "0.5"])
        if out:
            self.assertIn("Logging to:", out)
        path = find_recent_monitor_csv(PROJECT_ROOT, name_contains=f"PID{pid}")
        self.assertIsNotNone(path, f"Expected a recent PID{pid}_*_monitor.csv in project root")
        basename = os.path.basename(path)
        self.assertIn(f"PID{pid}", basename, f"Log filename should contain PID{pid}: {basename}")

    def test_3_4_csv_format(self):
        """Header and row format: ISO timestamp, float CPU, float Memory_MB."""
        out, _ = self.run_collection_for_seconds(["--sample-rate", "0.5"])
        path = find_recent_monitor_csv(PROJECT_ROOT)
        self.assertIsNotNone(path)
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["Timestamp", "CPU_Usage_%", "Memory_MB"])
        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        for row in rows[1:]:
            self.assertEqual(len(row), 3, row)
            self.assertTrue(iso_re.match(row[0]), f"Timestamp should be ISO format: {row[0]}")
            float(row[1])
            float(row[2])


class TestAnalysisMode(unittest.TestCase):
    """Spec section 4: Analysis mode. Uses tests/fixtures/smoke_sample.csv."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(SAMPLE_CSV):
            raise unittest.SkipTest(f"Fixture not found: {SAMPLE_CSV}")

    def test_4_1_summary_only(self):
        """Exit 0; duration; min/max/avg; peak tables."""
        code, out, err = run_jastm(["--parse-file", SAMPLE_CSV, "--summary"])
        self.assertEqual(code, 0, err or out)
        combined = out + err
        self.assertIn("Duration", combined)
        self.assertIn("CPU", combined)
        self.assertIn("Memory", combined)

    def test_4_2_metrics_window_only(self):
        """Exit 0; chart opens without crash (run with short timeout then terminate)."""
        code, out, err = run_jastm(
            ["--parse-file", SAMPLE_CSV, "--metrices-window"],
            timeout=2,
        )
        # Timeout or normal exit; we only require no crash (run_jastm returns)
        self.assertIn(code, (0, -1), "Process should exit or be terminated without crash")

    def test_4_3_summary_and_metrics_window(self):
        """Summary printed then chart (timeout after 2s); no crash."""
        code, out, err = run_jastm(
            ["--parse-file", SAMPLE_CSV, "--summary", "--metrices-window"],
            timeout=2,
        )
        self.assertIn(code, (0, -1), "Process should exit or be terminated without crash")
        if code == 0:
            self.assertIn("Duration", out + err)

    def test_4_4_analysis_no_action(self):
        """Message: use --summary or --metrices-window."""
        code, out, err = run_jastm(["--parse-file", SAMPLE_CSV])
        self.assertEqual(code, 0)
        combined = out + err
        self.assertIn("no action specified", combined)
        self.assertIn("--summary", combined)
        self.assertIn("--metrices-window", combined)

    def test_4_5_missing_file(self):
        """Exit 1; error about file."""
        code, _, err = run_jastm(["--parse-file", "nonexistent.csv", "--summary"])
        self.assertEqual(code, 1)
        self.assertTrue(bool(err.strip()), "Expected error message on stderr")

    def test_4_6_custom_peak_thresholds(self):
        """Exit 0; summary with custom peak params."""
        code, out, err = run_jastm([
            "--parse-file", SAMPLE_CSV, "--summary",
            "--cpu-peak-percentage", "50", "--ram-peak-percentage", "30",
        ])
        self.assertEqual(code, 0, err or out)
        self.assertIn("Duration", out + err)


def run_tests():
    start_time = time.time()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestHelpAndCLI, TestOptionValidation, TestAnalysisMode, TestDataCollection]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    cleanup_monitor_csvs_created_after(PROJECT_ROOT, start_time - 1)
    return result


if __name__ == "__main__":
    sys.exit(0 if run_tests().wasSuccessful() else 1)

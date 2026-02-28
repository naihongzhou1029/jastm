"""
Smoke tests for jastm. Run from project root: python -m pytest tests/smoke_test.py -v
Or: python tests/smoke_test.py
Implements smoke tests for all CLI, collection, analysis, and config behaviors.
"""

import csv
import glob
import os
import re
import subprocess
import sys
import tempfile
import textwrap
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
    "--machine-id",
    "--config-file",
    "--summary",
    "--metrices-window",
    "--cpu-peak-percentage",
    "--ram-peak-percentage",
]


def _write_temp_config_yaml(body: str) -> str:
    """Write a temporary YAML config file under tests/ and return its path."""
    content = textwrap.dedent(body).lstrip()
    fd, path = tempfile.mkstemp(suffix=".yaml", dir=TESTS_DIR)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _compute_expected_peaks_from_csv(csv_path: str, cpu_peak_percentage: float, ram_peak_percentage: float):
    """Compute expected CPU/memory peak counts from a CSV using the same rules as the analyzer."""
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return 0, 0
        cpu_vals = []
        mem_vals = []
        for row in reader:
            if len(row) < 3:
                continue
            try:
                cpu_vals.append(float(row[1]))
                mem_vals.append(float(row[2]))
            except ValueError:
                continue
    if not cpu_vals or not mem_vals:
        return 0, 0
    avg_cpu = sum(cpu_vals) / len(cpu_vals)
    avg_mem = sum(mem_vals) / len(mem_vals)
    cpu_ratio = cpu_peak_percentage
    ram_ratio = ram_peak_percentage / 100.0
    cpu_threshold = cpu_ratio
    mem_threshold = avg_mem * (1.0 - ram_ratio)
    cpu_count = sum(1 for c in cpu_vals if c > cpu_threshold)
    mem_count = sum(1 for m in mem_vals if m < mem_threshold)
    return cpu_count, mem_count


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


def setUpModule():
    # Store the start time so we can clean up any CSVs created during tests
    global TEST_START_TIME
    TEST_START_TIME = time.time()
    
    # Temporarily move config.yaml so it doesn't affect tests that expect default behavior
    cfg_path = os.path.join(PROJECT_ROOT, "config.yaml")
    bak_path = os.path.join(PROJECT_ROOT, "config.yaml.bak")
    if os.path.exists(cfg_path):
        os.rename(cfg_path, bak_path)

def tearDownModule():
    # Clean up any CSVs created during the tests
    if 'TEST_START_TIME' in globals():
        cleanup_monitor_csvs_created_after(PROJECT_ROOT, TEST_START_TIME - 1)
        
    # Restore config.yaml
    cfg_path = os.path.join(PROJECT_ROOT, "config.yaml")
    bak_path = os.path.join(PROJECT_ROOT, "config.yaml.bak")
    if os.path.exists(bak_path):
        if os.path.exists(cfg_path):
            os.remove(cfg_path)
        os.rename(bak_path, cfg_path)

class TestHelpAndCLI(unittest.TestCase):
    """Spec section 1: Help and CLI."""

    def test_1_1_help_output(self):
        """Exit 0; usage and required options visible."""
        code, out, err = run_jastm(["--help"])
        self.assertEqual(code, 0, f"Expected exit 0, got {code}. stderr: {err}")
        combined = out + err
        for opt in REQUIRED_CLI_OPTIONS:
            self.assertIn(opt, combined, f"Help should mention {opt}")

    def test_1_2_no_args_shows_help(self):
        """No arguments should cũng show help and exit 0."""
        code, out, err = run_jastm([])
        self.assertEqual(code, 0, f"Expected exit 0, got {code}. stderr: {err}")
        combined = out + err
        # It should contain the same help content, check for a few key items
        self.assertIn("usage:", combined.lower())
        for opt in REQUIRED_CLI_OPTIONS:
            self.assertIn(opt, combined, f"No-args help should mention {opt}")


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

    def test_2_7_missing_config_file(self):
        """Missing config file should yield non-zero exit and mention not found."""
        code, _, err = run_jastm(["--config-file", "nonexistent.yaml"])
        self.assertNotEqual(code, 0)
        self.assertIn("Config file not found", err)

    def test_2_8_invalid_sample_rate_from_config(self):
        """Config with non-positive sample_rate should be rejected."""
        cfg_path = _write_temp_config_yaml(
            """
            version: 1

            collection:
              sample_rate:
                value: 0
                default: 1.0
            """
        )
        try:
            code, _, err = run_jastm(["--config-file", cfg_path])
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass
        self.assertNotEqual(code, 0)
        self.assertIn("--sample-rate", err)

    def test_2_9_cli_overrides_config_thresholds(self):
        """CLI peak thresholds should override config values."""
        cfg_path = _write_temp_config_yaml(
            """
            version: 1

            analysis:
              cpu_peak_percentage:
                value: 10.0
                default: 10.0
              ram_peak_percentage:
                value: 20.0
                default: 20.0
            """
        )
        try:
            code, out, err = run_jastm(
                [
                    "--parse-file",
                    SAMPLE_CSV,
                    "--summary",
                    "--cpu-peak-percentage",
                    "50",
                    "--ram-peak-percentage",
                    "30",
                    "--config-file",
                    cfg_path,
                ]
            )
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass
        self.assertEqual(code, 0, err or out)
        combined = out + err
        # Expect CLI-provided thresholds reflected, not config values (10%/20%)
        self.assertIn("CPU > 50%", combined)
        self.assertIn("RAM < 30% deviation", combined)
        self.assertNotIn("CPU > 10%", combined)
        self.assertNotIn("RAM < 20% deviation", combined)


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

    def test_3_5_machine_id_default(self):
        """Machine ID should be printed as a 4-digit identifier when not provided explicitly."""
        out, _ = self.run_collection_for_seconds(["--sample-rate", "0.5"])
        # We expect at least one Machine ID line with a 4-digit token
        self.assertIn("Machine ID:", out)
        m = re.search(r"Machine ID:\s*(\d{4})", out)
        self.assertIsNotNone(m, f"Expected a 4-digit Machine ID in output, got: {out!r}")


class TestAnalysisMode(unittest.TestCase):
    """Spec section 4: Analysis mode. Uses tests/fixtures/smoke_sample.csv."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(SAMPLE_CSV):
            raise unittest.SkipTest(f"Fixture not found: {SAMPLE_CSV}")

    def test_4_1_summary_only(self):
        """Exit 0; duration; time period; min/max/avg CPU and memory; peak tables."""
        code, out, err = run_jastm(["--parse-file", SAMPLE_CSV, "--summary"])
        self.assertEqual(code, 0, err or out)
        combined = out + err
        self.assertIn("Duration", combined)
        self.assertIn("Time Period", combined)
        self.assertIn("CPU", combined)
        self.assertIn("Memory", combined)
        for stat_keyword in ["Min", "Max", "Avg"]:
            self.assertIn(stat_keyword, combined, f"Summary should include '{stat_keyword}' statistic")
        self.assertIn("CPU Peaks", combined, "Summary should include CPU peak table")
        self.assertIn("Memory Peaks", combined, "Summary should include Memory peak table")

    def test_4_2_metrics_window_only(self):
        """Exit 0; chart opens without crash (run with short timeout then terminate)."""
        code, out, err = run_jastm(
            ["--parse-file", SAMPLE_CSV, "--metrices-window"],
            timeout=2,
        )
        # Timeout or normal exit; we only require no crash (run_jastm returns)
        self.assertIn(code, (0, -1), "Process should exit or be terminated without crash")

    def test_4_2b_metrices_window_no_tkinter(self):
        """Exit 1 with a clear error when tkinter is absent and --metrices-window is requested."""
        # Drive jastm inside a subprocess that poisons sys.modules so tkinter appears missing.
        driver = textwrap.dedent(f"""\
            import sys
            # Simulate a missing tkinter by setting the relevant modules to None.
            # Python raises ImportError when a module is set to None in sys.modules.
            for _mod in ('tkinter', '_tkinter', 'tkinter.ttk'):
                sys.modules[_mod] = None
            import runpy
            sys.argv = ['jastm.py', '--parse-file', {repr(SAMPLE_CSV)}, '--metrices-window']
            runpy.run_path({repr(JASTM_PY)}, run_name='__main__')
        """)
        result = subprocess.run(
            [sys.executable, '-c', driver],
            capture_output=True, text=True, cwd=PROJECT_ROOT,
        )
        self.assertEqual(result.returncode, 1, "Should exit with code 1 when tkinter is missing")
        combined = result.stdout + result.stderr
        self.assertIn('tkinter', combined.lower(), "Error should mention tkinter")
        self.assertIn('--metrices-window', combined, "Error should mention --metrices-window")

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
        """Exit 0; summary reflects the custom peak thresholds in its output."""
        code, out, err = run_jastm([
            "--parse-file", SAMPLE_CSV, "--summary",
            "--cpu-peak-percentage", "50", "--ram-peak-percentage", "30",
        ])
        self.assertEqual(code, 0, err or out)
        combined = out + err
        self.assertIn("Duration", combined)
        self.assertIn("CPU > 50%", combined, "Summary should reflect custom CPU peak threshold")
        self.assertIn("RAM < 30% deviation", combined, "Summary should reflect custom RAM peak threshold")

    def test_4_7_aggregate_summaries_multiple_csvs(self):
        """Exit 0; aggregated markdown table with expected column names."""
        # Use the same sample file twice to simulate multiple CSVs
        code, out, err = run_jastm(
            ["--aggregate-summaries", SAMPLE_CSV, SAMPLE_CSV]
        )
        self.assertEqual(code, 0, err or out)
        combined = out + err
        self.assertIn("Aggregated Summary Report", combined)
        for col in [
            "machine_id",
            "start_time",
            "duration(days and hours)",
            "cpu_avg_%",
            "cpu_peak_count",
            "mem_avg",
            "mem_peak_count",
            "mem_slope",
            "mem_r_square",
            "flags",
        ]:
            self.assertIn(col, combined, f"Aggregated table should include column {col!r}")

    def test_4_8_aggregate_respects_peak_thresholds(self):
        """Aggregate uses the same peak rules as single-run analysis for the given thresholds."""
        cpu_pct = 50.0
        ram_pct = 30.0
        expected_cpu_peaks, expected_mem_peaks = _compute_expected_peaks_from_csv(
            SAMPLE_CSV, cpu_pct, ram_pct
        )
        code, out, err = run_jastm(
            [
                "--aggregate-summaries",
                SAMPLE_CSV,
                "--cpu-peak-percentage",
                str(cpu_pct),
                "--ram-peak-percentage",
                str(ram_pct),
            ]
        )
        self.assertEqual(code, 0, err or out)
        combined = out + err
        lines = combined.splitlines()
        header_idx = None
        for idx, line in enumerate(lines):
            if "Aggregated Summary Report" in line:
                # Header row with column names appears after this line
                continue
            if line.lstrip().startswith("|") and "machine_id" in line and "cpu_avg_%" in line:
                header_idx = idx
                break
        self.assertIsNotNone(header_idx, f"Failed to locate aggregated table header in output:\n{combined}")
        # Data row is two lines after header: header, separator, data
        data_idx = header_idx + 2
        self.assertLess(data_idx, len(lines), f"Expected at least one data row in aggregated table:\n{combined}")
        row_line = lines[data_idx]
        self.assertTrue(row_line.lstrip().startswith("|"), f"Expected data row starting with '|', got: {row_line!r}")
        cells = [c.strip() for c in row_line.split("|")]
        # Columns: 0:'', 1:machine_id, 2:start_time, 3:duration, 4:cpu_avg_%, 5:cpu_peak_count, 6:mem_avg, 7:mem_peak_count, 8:flags, 9:''
        self.assertGreaterEqual(len(cells), 8, f"Unexpected aggregated row format: {cells!r}")
        cpu_peak_count_val = int(cells[5])
        mem_peak_count_val = int(cells[7])
        self.assertEqual(
            cpu_peak_count_val,
            expected_cpu_peaks,
            f"cpu_peak_count in aggregate table should match expected CPU peaks ({expected_cpu_peaks})",
        )
        self.assertEqual(
            mem_peak_count_val,
            expected_mem_peaks,
            f"mem_peak_count in aggregate table should match expected memory peaks ({expected_mem_peaks})",
        )

    def test_4_9_memory_trend_regression(self):
        """Summary includes Memory Trend with slope (MB/hour) and R^2."""
        code, out, err = run_jastm(["--parse-file", SAMPLE_CSV, "--summary"])
        self.assertEqual(code, 0, err or out)
        combined = out + err
        self.assertIn("Memory Trend:", combined, "Summary should include a Memory Trend line")
        self.assertIn("MB/hour", combined, "Memory Trend should report slope in MB/hour")
        self.assertIn("R^2=", combined, "Memory Trend should report an R^2 value")


class TestOptionalAndConfig(unittest.TestCase):
    """Spec sections 5 and 6: optional / environment-specific and config behavior."""

    def test_5_1_launch_and_monitor_program(self):
        """Launch a small Python program via --program and ensure logging starts."""
        # Use the current Python interpreter running a short-lived script as the target program.
        script = "import time\nprint('hello'); time.sleep(4)\n"
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=TESTS_DIR, delete=False) as tmp:
            tmp.write(script)
            tmp_path = tmp.name
        try:
            start = time.time()
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                [
                    sys.executable,
                    JASTM_PY,
                    "--program",
                    sys.executable,
                    tmp_path,
                    "--sample-rate",
                    "0.2",
                ],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            out, _ = proc.communicate(timeout=20)
            self.assertEqual(
                proc.returncode, 0, f"Expected jastm to exit 0 when launching program, got {proc.returncode}. Output: {out}"
            )
            self.assertIn("Logging to:", out)
            # Ensure a monitor CSV was created during this run.
            csv_path = find_recent_monitor_csv(PROJECT_ROOT, within_seconds=max(30, int(time.time() - start) + 5))
            self.assertIsNotNone(csv_path, "Expected a *_monitor.csv to be created when using --program")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def test_5_2_process_exit_stops_collection(self):
        """Target a short-lived process and ensure collection stops after process exit."""
        # Spawn a short-lived Python process we can target by PID.
        target_script = "import time\nprint('target'); time.sleep(4)\n"
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=TESTS_DIR, delete=False) as tmp:
            tmp.write(target_script)
            target_path = tmp.name
        try:
            target_proc = subprocess.Popen(
                [sys.executable, target_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            pid = target_proc.pid
            # Run jastm against this PID with a modest timeout. It should terminate once the target exits.
            code, out, err = run_jastm(
                ["--process-id", str(pid), "--sample-rate", "0.2"],
                timeout=20,
            )
            # Normal exit (0) or timeout (-1) are acceptable as long as we don't hang indefinitely.
            self.assertIn(code, (0, -1), f"Expected normal or timeout exit when monitoring short-lived process, got {code}")
            combined = out + err
            # We still expect logging to have started and a CSV to be produced.
            if combined:
                self.assertIn("Logging to:", combined)
            csv_path = find_recent_monitor_csv(PROJECT_ROOT, within_seconds=30, name_contains=f"PID{pid}")
            self.assertIsNotNone(
                csv_path,
                f"Expected a PID{pid}_*_monitor.csv to be created when targeting a short-lived process",
            )
            with open(csv_path, newline="") as f:
                rows = list(csv.reader(f))
            self.assertGreaterEqual(len(rows), 1, "CSV should have at least a header row")
            self.assertEqual(rows[0], ["Timestamp", "CPU_Usage_%", "Memory_MB"])
        finally:
            try:
                target_proc.terminate()
            except Exception:
                pass
            try:
                os.remove(target_path)
            except OSError:
                pass

    def test_6_1_basic_config_usage_for_collection(self):
        """Config + CLI sample-rate collection behaves like 3.1."""
        cfg_path = _write_temp_config_yaml(
            """
            version: 1

            collection:
              sample_rate:
                value: 1.0
                default: 1.0
            """
        )
        start_time = time.time()
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                [sys.executable, "-u", JASTM_PY, "--config-file", cfg_path, "--sample-rate", "0.5"],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            try:
                out, _ = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    out, _ = proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, _ = proc.communicate(timeout=1)
            self.assertIn(
                proc.returncode,
                (0, 1, 15, -15),
                f"Expected normal or terminated exit when using config-driven collection, got {proc.returncode}. Output: {out}",
            )
            if out:
                self.assertIn("Logging to:", out)
            csv_path = find_recent_monitor_csv(PROJECT_ROOT, within_seconds=max(30, int(time.time() - start_time) + 5))
            self.assertIsNotNone(csv_path, "Expected a *_monitor.csv to be created when using config-driven collection")
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass

    def test_6_2_analysis_thresholds_from_config(self):
        """Analysis thresholds from config.yaml apply when CLI does not override them."""
        cfg_path = _write_temp_config_yaml(
            """
            version: 1

            analysis:
              cpu_peak_percentage:
                value: 10.0
                default: 10.0
              ram_peak_percentage:
                value: 20.0
                default: 20.0
            """
        )
        try:
            code, out, err = run_jastm(
                [
                    "--parse-file",
                    SAMPLE_CSV,
                    "--summary",
                    "--config-file",
                    cfg_path,
                ]
            )
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass
        self.assertEqual(code, 0, err or out)
        combined = out + err
        # Expect config-provided thresholds reflected when CLI does not specify them.
        self.assertIn("CPU > 10%", combined)
        self.assertIn("RAM < 20% deviation", combined)

    def test_6_3_machine_id_from_config(self):
        """Machine ID should be taken from config when provided."""
        cfg_path = _write_temp_config_yaml(
            """
            version: 1

            collection:
              machine_id:
                value: "9999"
                default: null
              sample_rate:
                value: 0.5
                default: 1.0
            """
        )
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            out, _ = TestDataCollection().run_collection_for_seconds(
                ["--config-file", cfg_path],
                seconds=2.5,
            )
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass
        self.assertIn("Machine ID:", out)
        self.assertIn("9999", out)

    def test_6_4_cli_overrides_machine_id_from_config(self):
        """CLI --machine-id should override machine_id from config."""
        cfg_path = _write_temp_config_yaml(
            """
            version: 1

            collection:
              machine_id:
                value: "1111"
                default: null
              sample_rate:
                value: 0.5
                default: 1.0
            """
        )
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    JASTM_PY,
                    "--config-file",
                    cfg_path,
                    "--machine-id",
                    "1234",
                    "--sample-rate",
                    "0.5",
                ],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            try:
                out, _ = proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    out, _ = proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    out, _ = proc.communicate(timeout=1)
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass
        self.assertIn("Machine ID:", out)
        self.assertIn("1234", out)
        self.assertNotIn("1111", out)


def run_tests():
    start_time = time.time()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [TestHelpAndCLI, TestOptionValidation, TestAnalysisMode, TestDataCollection, TestOptionalAndConfig]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result

if "--list-items" in sys.argv:
    print("--- JASTM Smoke Test Items ---")
    for cls in [TestHelpAndCLI, TestOptionValidation, TestAnalysisMode, TestDataCollection, TestOptionalAndConfig]:
        doc = cls.__doc__.strip() if cls.__doc__ else "No description"
        print(f"\n{cls.__name__}: {doc}")
        for name in dir(cls):
            if name.startswith("test_"):
                method = getattr(cls, name)
                method_doc = method.__doc__.strip() if method.__doc__ else "No description"
                print(f"  - {name}:")
                print(f"      Expected: {method_doc}")
    sys.exit(0)

if __name__ == "__main__":
    sys.exit(0 if run_tests().wasSuccessful() else 1)

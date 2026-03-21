"""
Smoke tests for jastm. Run from project root: python -m pytest tests/smoke_test.py -v
Or: python tests/smoke_test.py
Implements smoke tests for all CLI, collection, analysis, and config behaviors.
"""

import csv
import glob
import os
import re
import shutil
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

# Known values for smoke_sample.csv:
#   CPU  : 5.5, 12.3, 8.0, 95.0, 6.0
#   Mem  : 2048.00, 2000.50, 1950.25, 1800.00, 2100.00  avg ≈ 1979.75
#   cpu_peak_count at threshold=50 : values > 50 → [95.0]         → 1
#   mem_peak_count at threshold=30 : avg*(1-0.30)≈1385.8, no row below → 0
SAMPLE_CPU_PEAKS_AT_50 = 1
SAMPLE_MEM_PEAKS_AT_30 = 0

REQUIRED_CLI_OPTIONS = [
    "--parse-file",
    "--process-name",
    "--process-id",
    "--program",
    "--sample-rate",
    "--config-file",
    "--summary",
    "--metrices-window",
    "--cpu-peak-percentage",
    "--ram-peak-percentage",
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _write_temp_config_ini(body: str) -> str:
    """Write a temporary INI config file under tests/ and return its path."""
    content = textwrap.dedent(body).lstrip()
    fd, path = tempfile.mkstemp(suffix=".ini", dir=TESTS_DIR)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def run_jastm(args, cwd=None, timeout=None, capture=True):
    """Run jastm.py with given args. Returns (returncode, stdout, stderr)."""
    cmd = [sys.executable, JASTM_PY] + args
    kw = {"cwd": cwd or PROJECT_ROOT, "capture_output": capture, "text": True, "stdin": subprocess.DEVNULL}
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


def run_collection_for_seconds(args, seconds=2.5):
    """Start jastm in collection mode, let it run for *seconds*, then terminate.

    Returns (stdout+stderr combined, returncode).
    """
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


def find_recent_monitor_csv(cwd, within_seconds=30, name_contains=None):
    """Return path to the most recent *_monitor.csv in cwd modified within within_seconds."""
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
    """Remove *_monitor.csv files in cwd created after after_timestamp."""
    pattern = os.path.join(cwd, "*_monitor.csv")
    for path in glob.glob(pattern):
        try:
            if os.path.getmtime(path) >= after_timestamp:
                os.remove(path)
        except OSError:
            pass


def setUpModule():
    global TEST_START_TIME
    TEST_START_TIME = time.time()
    # Temporarily move config.ini so tests that expect default behaviour are not affected
    cfg_path = os.path.join(PROJECT_ROOT, "config.ini")
    bak_path = os.path.join(PROJECT_ROOT, "config.ini.bak")
    if os.path.exists(cfg_path):
        os.rename(cfg_path, bak_path)


def tearDownModule():
    if "TEST_START_TIME" in globals():
        cleanup_monitor_csvs_created_after(PROJECT_ROOT, TEST_START_TIME - 1)
    cfg_path = os.path.join(PROJECT_ROOT, "config.ini")
    bak_path = os.path.join(PROJECT_ROOT, "config.ini.bak")
    if os.path.exists(bak_path):
        if os.path.exists(cfg_path):
            os.remove(cfg_path)
        os.rename(bak_path, cfg_path)


# ---------------------------------------------------------------------------
# Section 1 – Help and CLI
# ---------------------------------------------------------------------------

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
        """No arguments should also show help and exit 0."""
        code, out, err = run_jastm([])
        self.assertEqual(code, 0, f"Expected exit 0, got {code}. stderr: {err}")
        combined = out + err
        self.assertIn("usage:", combined.lower())
        for opt in REQUIRED_CLI_OPTIONS:
            self.assertIn(opt, combined, f"No-args help should mention {opt}")


# ---------------------------------------------------------------------------
# Section 2 – Option validation
# ---------------------------------------------------------------------------

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
        self.assertTrue("--program" in err or "No selection made" in err)

    def test_2_7_missing_config_file(self):
        """Missing config file should yield non-zero exit and mention not found."""
        code, _, err = run_jastm(["--config-file", "nonexistent.ini"])
        self.assertNotEqual(code, 0)
        self.assertIn("Config file not found", err)

    def test_2_8_invalid_sample_rate_from_config(self):
        """Config with non-positive sample_rate should be rejected."""
        cfg_path = _write_temp_config_ini(
            """
            [collection]
            sample_rate = 0
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
        cfg_path = _write_temp_config_ini(
            """
            [analysis]
            cpu_peak_percentage = 10.0
            ram_peak_percentage = 20.0
            """
        )
        try:
            code, out, err = run_jastm(
                [
                    "--parse-file", SAMPLE_CSV, "--summary",
                    "--cpu-peak-percentage", "50",
                    "--ram-peak-percentage", "30",
                    "--config-file", cfg_path,
                ]
            )
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass
        self.assertEqual(code, 0, err or out)
        combined = out + err
        self.assertIn("CPU > 50%", combined)
        self.assertIn("RAM < 30% deviation", combined)
        self.assertNotIn("CPU > 10%", combined)
        self.assertNotIn("RAM < 20% deviation", combined)

    def test_2_10_reject_parse_file_with_aggregate_summaries(self):
        """--parse-file and --aggregate-summaries are mutually exclusive."""
        code, _, err = run_jastm(
            ["--parse-file", SAMPLE_CSV, "--aggregate-summaries", SAMPLE_CSV, "--summary"]
        )
        self.assertNotEqual(code, 0)
        self.assertTrue(
            "--parse-file" in err or "--aggregate-summaries" in err,
            f"Error should mention the conflicting flags; got: {err!r}",
        )

    def test_2_11_reject_process_name_with_process_id(self):
        """--process-name and --process-id are mutually exclusive."""
        code, _, err = run_jastm(["--process-name", "python", "--process-id", "123"])
        self.assertNotEqual(code, 0)

    def test_2_12_reject_process_name_with_program(self):
        """--process-name and --program are mutually exclusive."""
        code, _, err = run_jastm(["--process-name", "python", "--program", "echo", "hi"])
        self.assertNotEqual(code, 0)

    def test_2_17_reject_invalid_ini_config(self):
        """Config file with invalid INI syntax (no section headers) should yield non-zero exit."""
        cfg_path = _write_temp_config_ini("cpu_peak_percentage = 90\n")
        try:
            code, _, err = run_jastm(["--config-file", cfg_path])
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass
        self.assertNotEqual(code, 0)
        self.assertTrue(
            "config" in err.lower() or "parse" in err.lower(),
            f"Error should mention config or parse; got: {err!r}",
        )


# ---------------------------------------------------------------------------
# Section 3 – Data collection
# ---------------------------------------------------------------------------

class TestDataCollection(unittest.TestCase):
    """Spec section 3: Data collection (short runs)."""

    def test_3_1_system_wide_collection_starts(self):
        """Logging message and CSV with header + at least one data row."""
        out, _ = run_collection_for_seconds(["--sample-rate", "0.5"])
        if out:
            self.assertIn("Logging to:", out)
        path = find_recent_monitor_csv(PROJECT_ROOT)
        self.assertIsNotNone(path, "Expected a recent *_monitor.csv in project root")
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
        self.assertGreaterEqual(len(rows), 2, "CSV should have header + at least one data row")
        self.assertEqual(rows[0], ["Timestamp", "CPU_Usage_%", "Memory_MB", "VMS_MB", "RSS_MB"])

    def test_3_2_process_name_filter(self):
        """Log filename includes process name (e.g. python_…_monitor.csv)."""
        out, _ = run_collection_for_seconds(["--process-name", "python.exe", "--sample-rate", "0.5"])
        if out:
            self.assertIn("Logging to:", out)
        path = find_recent_monitor_csv(PROJECT_ROOT, name_contains="python")
        self.assertIsNotNone(path, "Expected a recent python_*_monitor.csv in project root")
        self.assertIn("python", os.path.basename(path).lower())

    def test_3_3_pid_filter(self):
        """Log filename includes PID<id>."""
        pid = os.getpid()
        out, _ = run_collection_for_seconds(["--process-id", str(pid), "--sample-rate", "0.5"])
        if out:
            self.assertIn("Logging to:", out)
        path = find_recent_monitor_csv(PROJECT_ROOT, name_contains=f"PID{pid}")
        self.assertIsNotNone(path, f"Expected a recent PID{pid}_*_monitor.csv in project root")
        self.assertIn(f"PID{pid}", os.path.basename(path))

    def test_3_4_csv_format(self):
        """Header, ISO timestamps, CPU in [0, 100], positive Memory_MB."""
        out, _ = run_collection_for_seconds(["--sample-rate", "0.5"])
        path = find_recent_monitor_csv(PROJECT_ROOT)
        self.assertIsNotNone(path)
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["Timestamp", "CPU_Usage_%", "Memory_MB", "VMS_MB", "RSS_MB"])
        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        for row in rows[1:]:
            self.assertEqual(len(row), 5, row)
            self.assertTrue(iso_re.match(row[0]), f"Timestamp should be ISO format: {row[0]}")
            cpu = float(row[1])
            mem = float(row[2])
            self.assertGreaterEqual(cpu, 0.0, f"CPU should be >= 0, got {cpu}")
            self.assertLessEqual(cpu, 100.0, f"CPU should be <= 100, got {cpu}")
            self.assertGreater(mem, 0.0, f"Memory_MB should be positive, got {mem}")

    def test_3_9_vas_metrics_present_for_process(self):
        """VMS and RSS should be numeric when monitoring a specific process."""
        pid = os.getpid()
        out, _ = run_collection_for_seconds(["--process-id", str(pid), "--sample-rate", "0.2"], seconds=2.0)
        path = find_recent_monitor_csv(PROJECT_ROOT, name_contains=f"PID{pid}")
        self.assertIsNotNone(path)
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
        for row in rows[1:]:
            self.assertEqual(len(row), 5)
            self.assertNotEqual(row[3], "N/A")
            self.assertNotEqual(row[4], "N/A")
            vms = float(row[3])
            rss = float(row[4])
            self.assertGreater(vms, 0)
            self.assertGreater(rss, 0)

    def test_3_10_vas_metrics_na_for_system_wide(self):
        """VMS and RSS should be N/A when monitoring system-wide."""
        out, _ = run_collection_for_seconds(["--sample-rate", "0.2"], seconds=2.0)
        path = find_recent_monitor_csv(PROJECT_ROOT)
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
        for row in rows[1:]:
            self.assertEqual(len(row), 5)
            self.assertEqual(row[3], "N/A")
            self.assertEqual(row[4], "N/A")

    def test_3_6_process_name_not_found(self):
        """Non-existent process name should exit non-zero and mention the name in the error."""
        code, _, err = run_jastm(["--process-name", "__no_such_process_xyz__"])
        self.assertNotEqual(code, 0, "Expected non-zero exit when process name not found")
        self.assertIn("__no_such_process_xyz__", err, "Error should mention the missing process name")

    def test_3_8_csv_filename_timestamp_format(self):
        """CSV filename should contain a YYYYMMDD_HHMMSS timestamp."""
        run_collection_for_seconds(["--sample-rate", "0.5"])
        path = find_recent_monitor_csv(PROJECT_ROOT)
        self.assertIsNotNone(path)
        self.assertRegex(
            os.path.basename(path),
            re.compile(r"\d{8}_\d{6}"),
            f"Filename should contain a YYYYMMDD_HHMMSS timestamp: {os.path.basename(path)}",
        )


# ---------------------------------------------------------------------------
# Section 4 – Analysis mode
# ---------------------------------------------------------------------------

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
        """Exit 1; error mentions the filename or 'not found'."""
        code, _, err = run_jastm(["--parse-file", "nonexistent.csv", "--summary"])
        self.assertEqual(code, 1)
        self.assertTrue(
            "nonexistent.csv" in err or "not found" in err.lower() or "no such" in err.lower(),
            f"Error should mention the missing file or 'not found'; got: {err!r}",
        )

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
        code, out, err = run_jastm(["--aggregate-summaries", SAMPLE_CSV, SAMPLE_CSV])
        self.assertEqual(code, 0, err or out)
        combined = (out + err).replace("<br>", " ")
        self.assertIn("Aggregated Summary Report", combined)
        for col in [
            "Start Time", "Duration", "CPU(%)",
            "CPU Peak", "RAM(MB)", "RAM Peak", "RAM Slope", "RAM R-Square",
        ]:
            self.assertIn(col, combined, f"Aggregated table should include column {col!r}")

    def test_4_8_aggregate_respects_peak_thresholds(self):
        """Aggregate peak counts match expected values for smoke_sample.csv at known thresholds."""
        # smoke_sample.csv facts (see SAMPLE_CPU_PEAKS_AT_50 / SAMPLE_MEM_PEAKS_AT_30 constants):
        #   cpu_peak_count at threshold=50 → 1   |   mem_peak_count at threshold=30 → 0
        code, out, err = run_jastm([
            "--aggregate-summaries", SAMPLE_CSV,
            "--cpu-peak-percentage", "50",
            "--ram-peak-percentage", "30",
        ])
        self.assertEqual(code, 0, err or out)
        combined = (out + err).replace("<br>", " ")
        lines = combined.splitlines()

        # Locate header row dynamically by column names (not by line index)
        header_idx = next(
            (i for i, line in enumerate(lines)
             if line.lstrip().startswith("|") and "CPU Peak" in line and "RAM Peak" in line),
            None,
        )
        self.assertIsNotNone(header_idx, f"Could not find aggregate table header:\n{combined}")

        header_cells = [c.strip() for c in lines[header_idx].split("|")]
        cpu_col = next(i for i, h in enumerate(header_cells) if h == "CPU Peak")
        mem_col = next(i for i, h in enumerate(header_cells) if h == "RAM Peak")

        data_idx = header_idx + 2  # skip separator row
        self.assertLess(data_idx, len(lines), "Expected at least one data row after the header")
        data_cells = [c.strip() for c in lines[data_idx].split("|")]
        self.assertEqual(int(data_cells[cpu_col]), SAMPLE_CPU_PEAKS_AT_50)
        self.assertEqual(int(data_cells[mem_col]), SAMPLE_MEM_PEAKS_AT_30)

    def test_4_9_memory_trend_regression(self):
        """Summary includes Memory Trend with slope (MB/hour) and R^2."""
        code, out, err = run_jastm(["--parse-file", SAMPLE_CSV, "--summary"])
        self.assertEqual(code, 0, err or out)
        combined = out + err
        self.assertIn("Memory Trend:", combined, "Summary should include a Memory Trend line")
        self.assertIn("MB/hour", combined, "Memory Trend should report slope in MB/hour")
        self.assertIn("R^2=", combined, "Memory Trend should report an R^2 value")

    def test_4_10_header_only_csv_rejected(self):
        """CSV with header but no data rows should exit 1 with a non-empty error."""
        header_only = os.path.join(FIXTURES_DIR, "header_only.csv")
        code, _, err = run_jastm(["--parse-file", header_only, "--summary"])
        self.assertEqual(code, 1, f"Expected exit 1 for header-only CSV; got {code}. stderr: {err}")
        self.assertTrue(err.strip(), "Expected a non-empty error message on stderr")

    def test_4_11_malformed_rows_skipped(self):
        """CSV with some non-numeric rows should skip them and still produce a valid summary."""
        malformed = os.path.join(FIXTURES_DIR, "malformed_rows.csv")
        code, out, err = run_jastm(["--parse-file", malformed, "--summary"])
        self.assertEqual(code, 0, f"Expected exit 0; valid rows should be processed. stderr: {err}")
        self.assertIn("Duration", out + err, "Summary should be produced from the valid rows")

    def test_4_12_aggregate_single_file(self):
        """--aggregate-summaries with one file should exit 0 and produce exactly one data row."""
        code, out, err = run_jastm(["--aggregate-summaries", SAMPLE_CSV])
        self.assertEqual(code, 0, err or out)
        combined = out + err
        self.assertIn("Aggregated Summary Report", combined)
        lines = combined.splitlines()
        table_lines = [l for l in lines if l.lstrip().startswith("|")]
        # table_lines = [header, separator, data_row, ...]
        data_rows = table_lines[2:]
        self.assertEqual(len(data_rows), 1, f"Expected exactly 1 data row for 1 input file, got {len(data_rows)}")

    def test_4_13_aggregate_missing_file(self):
        """--aggregate-summaries with a non-existent file should exit non-zero."""
        code, _, err = run_jastm(["--aggregate-summaries", "nonexistent_run.csv"])
        self.assertNotEqual(code, 0, "Expected non-zero exit for a missing aggregate file")
        self.assertTrue(
            "nonexistent_run.csv" in err or "not found" in err.lower(),
            f"Error should mention the missing file; got: {err!r}",
        )

    def test_4_15_summary_no_cpu_peaks(self):
        """Summary with a threshold above the data maximum should report no CPU peaks."""
        # smoke_sample.csv max CPU is 95.0, so threshold=99 yields zero peaks
        code, out, err = run_jastm([
            "--parse-file", SAMPLE_CSV, "--summary", "--cpu-peak-percentage", "99",
        ])
        self.assertEqual(code, 0, err or out)
        self.assertIn("No cpu peaks detected", out + err)

    def test_4_16_summary_no_memory_peaks(self):
        """Summary with default thresholds on smoke_sample.csv should report no memory peaks."""
        # avg_mem ≈ 1979.75; 50 % deviation threshold ≈ 989.9; all sample values are above that
        code, out, err = run_jastm(["--parse-file", SAMPLE_CSV, "--summary"])
        self.assertEqual(code, 0, err or out)
        self.assertIn("No memory peaks detected", out + err)

    def test_4_17_aggregate_flags_column(self):
        """flags column should contain CPU_PEAKS when CPU peaks exist."""
        # At threshold=50, smoke_sample.csv has 1 CPU peak
        code, out, err = run_jastm([
            "--aggregate-summaries", SAMPLE_CSV, "--cpu-peak-percentage", "50",
        ])
        self.assertEqual(code, 0, err or out)
        self.assertIn("CPU_PEAKS", out + err, "flags column should contain CPU_PEAKS when peaks are found")

    def test_4_18_vas_analysis_summary(self):
        """Summary should include VMS and RSS stats if present in CSV."""
        vas_csv = os.path.join(FIXTURES_DIR, "vas_sample.csv")
        code, out, err = run_jastm(["--parse-file", vas_csv, "--summary"])
        self.assertEqual(code, 0, err or out)
        combined = out + err
        self.assertIn("Process VAS Stats:", combined)
        self.assertIn("VMS (Virtual Size):", combined)
        self.assertIn("RSS (Working Set):", combined)
        self.assertIn("VMS Trend:", combined)
        self.assertIn("RSS Trend:", combined)

    def test_4_19_fragmentation_risk_detection(self):
        """Flag fragmentation risk if VMS grows much faster than RSS."""
        # Create a specific case for fragmentation risk
        # VMS: 100 -> 200 (slope 100)
        # RSS: 50 -> 51 (slope 1)
        vas_csv = os.path.join(FIXTURES_DIR, "vas_sample.csv")
        code, out, err = run_jastm(["--parse-file", vas_csv, "--summary"])
        self.assertEqual(code, 0, err or out)
        self.assertIn("FRAGMENTATION RISK DETECTED", out + err)
        self.assertIn("VMS is growing steadily while RSS is relatively flat", out + err)


# ---------------------------------------------------------------------------
# Section 5 & 6 – Program launch and config
# ---------------------------------------------------------------------------

class TestOptionalAndConfig(unittest.TestCase):
    """Spec sections 5 and 6: optional / environment-specific and config behavior."""

    def test_5_1_launch_and_monitor_program(self):
        """Launch a small Python program via --program and ensure logging starts."""
        script = "import time\nprint('hello'); time.sleep(4)\n"
        with tempfile.NamedTemporaryFile("w", suffix=".py", dir=TESTS_DIR, delete=False) as tmp:
            tmp.write(script)
            tmp_path = tmp.name
        try:
            start = time.time()
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                [sys.executable, JASTM_PY, "--program", sys.executable, tmp_path, "--sample-rate", "0.2"],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            out, _ = proc.communicate(timeout=20)
            self.assertEqual(proc.returncode, 0, f"Expected jastm to exit 0, got {proc.returncode}. Output: {out}")
            self.assertIn("Logging to:", out)
            csv_path = find_recent_monitor_csv(PROJECT_ROOT, within_seconds=max(30, int(time.time() - start) + 5))
            self.assertIsNotNone(csv_path, "Expected a *_monitor.csv when using --program")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def test_5_2_process_exit_stops_collection(self):
        """Collection must stop on its own when the target process exits (no timeout kill)."""
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
            code, out, err = run_jastm(
                ["--process-id", str(pid), "--sample-rate", "0.2"],
                timeout=20,
            )
            # timeout (-1) means jastm never stopped — that is the bug being tested
            self.assertEqual(code, 0, f"Expected jastm to exit 0 after target died, not timeout. got {code}")
            csv_path = find_recent_monitor_csv(PROJECT_ROOT, within_seconds=30, name_contains=f"PID{pid}")
            self.assertIsNotNone(csv_path, f"Expected a PID{pid}_*_monitor.csv")
            with open(csv_path, newline="") as f:
                rows = list(csv.reader(f))
            self.assertGreaterEqual(len(rows), 1)
            self.assertEqual(rows[0], ["Timestamp", "CPU_Usage_%", "Memory_MB", "VMS_MB", "RSS_MB"])
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
        """Config-driven collection starts and produces a CSV log."""
        cfg_path = _write_temp_config_ini(
            """
            [collection]
            sample_rate = 1.0
            """
        )
        try:
            out, code = run_collection_for_seconds(
                ["--config-file", cfg_path, "--sample-rate", "0.5"], seconds=3
            )
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass
        # 0 = natural exit; -15/15 = SIGTERM (Unix); 1 = TerminateProcess (Windows)
        self.assertIn(code, (0, 1, -15, 15), f"Unexpected exit code: {code}")
        csv_path = find_recent_monitor_csv(PROJECT_ROOT, within_seconds=30)
        self.assertIsNotNone(csv_path, "Expected a *_monitor.csv when using config-driven collection")
        if out:
            self.assertIn("Logging to:", out)

    def test_6_2_analysis_thresholds_from_config(self):
        """Analysis thresholds from config.ini apply when CLI does not override them."""
        cfg_path = _write_temp_config_ini(
            """
            [analysis]
            cpu_peak_percentage = 10.0
            ram_peak_percentage = 20.0
            """
        )
        try:
            code, out, err = run_jastm(
                ["--parse-file", SAMPLE_CSV, "--summary", "--config-file", cfg_path]
            )
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass
        self.assertEqual(code, 0, err or out)
        combined = out + err
        self.assertIn("CPU > 10%", combined)
        self.assertIn("RAM < 20% deviation", combined)

    def test_6_5_auto_detect_config_from_script_dir(self):
        """config.ini in the script directory is auto-loaded when --config-file is not given."""
        cfg_path = os.path.join(PROJECT_ROOT, "config.ini")
        try:
            with open(cfg_path, "w") as f:
                f.write(textwrap.dedent("""\
                    [analysis]
                    cpu_peak_percentage = 55.0
                """))
            code, out, err = run_jastm(["--parse-file", SAMPLE_CSV, "--summary"])
            self.assertEqual(code, 0, err or out)
            self.assertIn(
                "CPU > 55%", out + err,
                "Auto-detected config.ini should apply cpu_peak_percentage=55",
            )
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Section 7 – Tkinter lazy loading
# ---------------------------------------------------------------------------

class TestTkinterLazyLoading(unittest.TestCase):
    """Spec section 7: tkinter is only required when --metrices-window is used."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(SAMPLE_CSV):
            raise unittest.SkipTest(f"Fixture not found: {SAMPLE_CSV}")
        # A fake tkinter.py that always raises ImportError, placed first in PYTHONPATH
        cls.fake_tkinter_dir = tempfile.mkdtemp()
        with open(os.path.join(cls.fake_tkinter_dir, "tkinter.py"), "w") as f:
            f.write('raise ImportError("tkinter is not available (simulated for testing)")\n')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fake_tkinter_dir, ignore_errors=True)

    def _run_without_tkinter(self, args, timeout=None):
        """Run jastm.py with tkinter shadowed by a fake that raises ImportError."""
        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = self.fake_tkinter_dir + (os.pathsep + existing if existing else "")
        cmd = [sys.executable, JASTM_PY] + args
        kw = {"env": env, "cwd": PROJECT_ROOT, "capture_output": True, "text": True}
        if timeout:
            kw["timeout"] = timeout
        try:
            r = subprocess.run(cmd, **kw)
            return r.returncode, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired as e:
            proc = getattr(e, "process", None)
            if proc:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
            raw_out = getattr(e, "stdout", None) or b""
            raw_err = getattr(e, "stderr", None) or b""
            if isinstance(raw_out, bytes):
                raw_out = raw_out.decode("utf-8", errors="replace")
            if isinstance(raw_err, bytes):
                raw_err = raw_err.decode("utf-8", errors="replace")
            return -1, raw_out, raw_err

    def test_7_1_summary_works_without_tkinter(self):
        """--summary must succeed even when tkinter is not installed."""
        code, out, err = self._run_without_tkinter(["--parse-file", SAMPLE_CSV, "--summary"])
        self.assertEqual(code, 0, f"--summary should not require tkinter. stderr: {err}")
        self.assertIn("Duration", out + err)

    def test_7_2_metrices_window_fails_gracefully_without_tkinter(self):
        """--metrices-window must exit non-zero with a clear error when tkinter cannot be installed."""
        code, out, err = self._run_without_tkinter(
            ["--parse-file", SAMPLE_CSV, "--metrices-window"],
            timeout=15,
        )
        self.assertNotEqual(code, 0, "--metrices-window should fail when tkinter is unavailable")
        self.assertIn("tkinter", (out + err).lower(), "Error output should mention tkinter")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_ALL_TEST_CLASSES = [
    TestHelpAndCLI,
    TestOptionValidation,
    TestDataCollection,
    TestAnalysisMode,
    TestOptionalAndConfig,
    TestTkinterLazyLoading,
]


def run_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in _ALL_TEST_CLASSES:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    return runner.run(suite)


if "--list-items" in sys.argv:
    print("--- JASTM Smoke Test Items ---")
    for cls in _ALL_TEST_CLASSES:
        doc = cls.__doc__.strip() if cls.__doc__ else "No description"
        print(f"\n{cls.__name__}: {doc}")
        for name in sorted(n for n in dir(cls) if n.startswith("test_")):
            method = getattr(cls, name)
            method_doc = method.__doc__.strip() if method.__doc__ else "No description"
            print(f"  - {name}:")
            print(f"      Expected: {method_doc}")
    sys.exit(0)

if __name__ == "__main__":
    sys.exit(0 if run_tests().wasSuccessful() else 1)

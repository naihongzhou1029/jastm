# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the full smoke test suite
python -m unittest tests.smoke_test -v

# Run a single test class
python -m unittest tests.smoke_test.TestAnalysisMode -v

# Run a single test case
python -m unittest tests.smoke_test.TestAnalysisMode.test_4_1_summary_only -v

# Run the happy path tests
python tests/happy_path.py

# List all happy path items with descriptions
python tests/happy_path.py --list-items

# List all smoke test items with descriptions
python tests/smoke_test.py --list-items

# Install Python dependencies (auto-installed on first run, but can be done manually)
pip install psutil matplotlib

# Run jastm directly
python jastm.py --help
python jastm.py --parse-file <csv> --summary
python jastm.py --aggregate-summaries run1.csv run2.csv
```

There is no build step, linter config, or formatter configured in this project.

## Architecture

The entire application lives in a single file: `jastm.py`. There are no packages or modules beyond it.

### Two runtime modes

**Collection mode** (default): samples CPU and memory on an interval, writes rows to a timestamped `*_monitor.csv` file, and stops when the process exits or after 10 consecutive metric failures. The `DataCollector` class owns this path. Its `run()` method is headless; the `setup_gui()` method exists but is unused in the current active code path.

**Analysis mode** (`--parse-file` or `--aggregate-summaries`): reads one or more existing CSVs and produces a text summary and/or an interactive chart. The `DataAnalyzer` class owns this path.

### Key data flow

```
parse_arguments()
  └─ _resolve_effective_options()   ← merges CLI + config.ini values
       └─ main()
            ├─ DataCollector.run()         (collection mode)
            ├─ DataAnalyzer + show_summary()  (--parse-file --summary)
            ├─ DataAnalyzer + show_metrics_window()  (--parse-file --metrices-window)
            └─ aggregate_summaries()       (--aggregate-summaries)
```

### Config file merging

CLI args always win over `config.ini`. `config.ini` is auto-detected from the script's own directory if `--config-file` is not given. Config uses standard INI format (`[collection]` and `[analysis]` sections); all values are strings — numeric options are cast to `float()` in `_resolve_effective_options()`. Process targeting (`--process-name`, `--process-id`, `--program`) is CLI-only and not read from config.

### Lazy tkinter

`tkinter` and the `matplotlib` TkAgg backend are **not** imported at module level. They are imported inside `DataAnalyzer.show_metrics_window()` only, after `_ensure_tkinter()` confirms (or attempts to install) the dependency. All other commands work without tkinter.

The flag is spelled `--metrices-window` (not `--metrics-window`) throughout the codebase, including in argparse and in tests. Do not "fix" this typo — it would break all tests and user scripts that rely on it.

`DataCollector.setup_gui()` is dead code: it references `tk`, `FigureCanvasTkAgg`, and `NavigationToolbar2Tk` that are never imported in collection mode. It is never called by `run()`.

### Peak detection rules

- **CPU peak**: `sample > cpu_peak_percentage` (absolute %, default 90)
- **Memory peak**: `sample < avg_mem * (1 - ram_peak_percentage / 100)` (deviation from average, default 50%)
- No range validation is applied — values outside [0, 100] are accepted (e.g. CPU > 100% on multi-core systems).

### CSV format and log file naming

Log files use the pattern `{process_name|PID{id}|timestamp}_{YYYYMMDD_HHMMSS}_monitor.csv`.

CSV columns: `Timestamp` (ISO `YYYY-MM-DD HH:MM:SS`), `CPU_Usage_%` (float), `Memory_MB` (float), `VMS_MB` (float or `N/A`), `RSS_MB` (float or `N/A`).

- **CPU**: per-process CPU % when a process is targeted; system-wide CPU % otherwise. First sample after start may be 0 (priming).
- **Memory**: system-wide **available** memory in MB (`psutil.virtual_memory().available`), not process RSS.
- **VMS_MB / RSS_MB**: platform-specific — `psutil.memory_info()` fields differ between Linux and Windows:
  - **Linux**: `vms` = total virtual address space (always ≥ RSS); `rss` = resident set (physical pages in RAM).
  - **Windows**: `vms` is `PagefileUsage` (pages on disk only, typically < RSS). jastm corrects this by using `private` (Private Bytes = total committed private virtual memory) for `VMS_MB` on Windows, so `VMS_MB` is semantically equivalent to Linux VMS. `RSS_MB` = Working Set (physical pages, including shared DLLs).

Analysis also computes a linear regression of `Memory_MB` over elapsed time, reported as slope (MB/hour) and R² to indicate potential memory leaks.

### CLI mutual exclusions

- `--parse-file` and `--aggregate-summaries` cannot be used together.
- `--parse-file` / `--aggregate-summaries` cannot be combined with `--process-name`, `--process-id`, or `--program`.
- `--process-name`, `--process-id`, and `--program` are mutually exclusive (only one process target at a time).

### Machine ID inference (aggregate mode)

`_infer_machine_id_from_path()` uses `(?<!\d)(\d{4})(?!\d)` to extract the first 4-digit token from the CSV basename that is not surrounded by other digits. Falls back to the NIC-MAC-derived ID if no token is found.

`--machine-id` is also a CLI option (and can be set under `[collection]` in config.ini); it provides the fallback 4-digit ID when filename inference fails.

### Aggregate output columns

`--aggregate-summaries` prints a markdown table with one row per CSV file:

`machine_id`, `start_time`, `duration(days and hours)`, `cpu_avg_%`, `cpu_peak_count`, `mem_avg`, `mem_peak_count`, `mem_slope`, `mem_r_square`, `flags`

### Metrics window interactivity

`--metrices-window` renders CPU (scaled ×20) and available memory over elapsed time. Features: scroll to zoom, hover for interpolated values, arrow-key cursor, scatter overlay for CPU (red) and memory (orange) peaks, average labels on the right axis.

### Test suite layout

```
tests/
  smoke_test.py      — 54 unittest cases, organised by spec section (1–7)
  happy_path.py      — end-to-end workflow tests
  fixtures/
    smoke_sample.csv      — 5-row reference CSV used by analysis tests
    header_only.csv       — header with no data rows (edge-case fixture)
    malformed_rows.csv    — mix of valid and non-numeric rows (edge-case fixture)
```

`smoke_test.py` backs up `config.ini` in `setUpModule` and restores it in `tearDownModule` so that tests are not affected by a developer's local config. The module-level `run_collection_for_seconds(args, seconds)` helper starts jastm, waits, terminates it, and returns `(stdout+stderr, returncode)`. On Windows, `proc.terminate()` exits with code `1` (via `TerminateProcess`); tests that check the exit code must include `1` alongside `(0, -15, 15)`.

### Known output strings tests rely on

| Condition | String asserted |
|---|---|
| No CPU peaks found | `"No cpu peaks detected"` |
| No memory peaks found | `"No memory peaks detected"` |
| Peaks report header | `"### Peaks Report (CPU > X%, RAM < Y% deviation)"` |
| Missing aggregate file | `"Error: File not found: <path>"` |
| Missing config file | `"Config file not found"` |
| tkinter unavailable | `"tkinter"` in stderr |
| Collection started | `"Logging to:"` |
| Machine ID | `"Machine ID: NNNN"` |

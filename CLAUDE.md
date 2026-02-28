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

# List all smoke test items with descriptions
python tests/smoke_test.py --list-items

# Install Python dependencies (auto-installed on first run, but can be done manually)
pip install psutil matplotlib pyyaml

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
  └─ _resolve_effective_options()   ← merges CLI + config.yaml values
       └─ main()
            ├─ DataCollector.run()         (collection mode)
            ├─ DataAnalyzer + show_summary()  (--parse-file --summary)
            ├─ DataAnalyzer + show_metrics_window()  (--parse-file --metrices-window)
            └─ aggregate_summaries()       (--aggregate-summaries)
```

### Config file merging

CLI args always win over `config.yaml`. `config.yaml` is auto-detected from the script's own directory if `--config-file` is not given. Config values may be plain scalars or `{value: ..., default: ...}` mappings; `_get_config_option()` normalises both forms.

### Lazy tkinter

`tkinter` and the `matplotlib` TkAgg backend are **not** imported at module level. They are imported inside `DataAnalyzer.show_metrics_window()` only, after `_ensure_tkinter()` confirms (or attempts to install) the dependency. All other commands work without tkinter.

### Peak detection rules

- **CPU peak**: `sample > cpu_peak_percentage` (absolute %, default 90)
- **Memory peak**: `sample < avg_mem * (1 - ram_peak_percentage / 100)` (deviation from average, default 50%)
- Both thresholds are validated to [0, 100] in `main()`.

### Machine ID inference (aggregate mode)

`_infer_machine_id_from_path()` uses `(?<!\d)(\d{4})(?!\d)` to extract the first 4-digit token from the CSV basename that is not surrounded by other digits. Falls back to the NIC-MAC-derived ID if no token is found.

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

`smoke_test.py` backs up `config.yaml` in `setUpModule` and restores it in `tearDownModule` so that tests are not affected by a developer's local config. The module-level `run_collection_for_seconds(args, seconds)` helper starts jastm, waits, terminates it, and returns `(stdout+stderr, returncode)`.

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

# jastm

Just Another Soak Testing Monitor — real-time system monitoring and post-run analysis for soak/stability testing.

## Overview

- **`monitor` subcommand**: Samples CPU and memory at a configurable interval, optionally launching and scoping to a specific program. Writes a CSV log for later analysis.
- **`analyze` subcommand**: Loads collected CSVs, computes statistics and peak detection, aggregates multiple runs into a summary table, or generates a Windows Event Log report.

## Project structure

- `jastm.py`: Main CLI entrypoint and implementation of data collection and analysis.
- `README.md`: Usage guide and high-level documentation.
- `wiki/journal.md`: Developer journal recording technical decisions, implementation phases, and insights.
- `tests/`: Automated test suite ensuring correctness of all features.
  - `smoke_test.py`: Comprehensive test suite covering CLI parsing, validation, data collection, and post-run analysis.
  - `happy_path.py`: High-level tests verifying core end-to-end user workflows.

## Requirements

- **Python**: 3.x
- **Dependencies**: `psutil`, `matplotlib` (with TkAgg backend), `tkinter` (usually bundled with Python)

Install:

```bash
pip install psutil matplotlib
```

## Usage

### `monitor` — collect metrics

- **System-wide** (no process filter):
  `python jastm.py monitor`
- **Launch and monitor a specific program**:
  `python jastm.py monitor --program myapp.exe [args...]`

`monitor` options:

| Option | Description | Default |
|--------|-------------|---------|
| `--program` | Program command to launch and monitor | *(none — system-wide)* |
| `--sample-rate` | Sampling interval in seconds | `1.0` |
| `--config-file` | Path to INI config file | *(auto-detected)* |

Log file is created automatically using the pattern `{program_stem|timestamp}_{YYYYMMDD_HHMMSS}_monitor.csv`. The log contains these columns: `Timestamp`, `CPU_Usage_%`, `Memory_MB`, `VMS_MB`, and `RSS_MB`. Data collection stops after 10 consecutive metric failures (such as process exit).

### `analyze` — post-run analysis

**Single file summary or chart:**

- **Summary only**:
  `python jastm.py analyze --parse-file 20231025_100000_monitor.csv --summary`
- **Interactive chart**:
  `python jastm.py analyze --parse-file 20231025_100000_monitor.csv --metrices-window`
- **Both**: use `--summary` and `--metrices-window` together.

**Aggregate multiple runs:**

When you have soak logs from multiple machines (or multiple runs) and want a **single, human-readable overview**:

- `python jastm.py analyze --aggregate-summaries machineA_20231025_100000_monitor.csv machineB_20231025_110000_monitor.csv`
- `python jastm.py analyze --aggregate-summaries *.csv --cpu-peak-percentage 80 --ram-peak-percentage 40`

This prints a markdown table, one row per input CSV, with the following columns: `Machine ID`, `Start Time`, `Duration`, `CPU(%)`, `CPU Peak`, `RAM(MB)`, `RAM Peak`, `RAM Slope`, `RAM R-Square`, `Warnings`.

**Events Report (Windows only):**

Collect all Warning, Error, and Critical events from the Windows Event Log (System and Application channels) over the last 24 hours:

- **Auto-named output**:
  `python jastm.py analyze --events-report`
  Writes to `events_report_YYYYMMDD_HHMMSS.md` in the current directory.
- **Custom output path**:
  `python jastm.py analyze --events-report my_report.md`

`analyze` options:

| Option | Description | Default |
|--------|-------------|---------|
| `--parse-file` | Input CSV file to analyse | — |
| `--aggregate-summaries` | One or more CSV files to aggregate | — |
| `--events-report` | Generate Windows Event Log Markdown report | — |
| `--summary` | Show text summary (requires `--parse-file`) | — |
| `--metrices-window` | Open interactive chart (requires `--parse-file`) | — |
| `--cpu-peak-percentage` | CPU peak threshold (%) | `90.0` |
| `--ram-peak-percentage` | Memory peak threshold, deviation % (0–100) | `50.0` |
| `--config-file` | Path to INI config file | *(auto-detected)* |

`--parse-file`, `--aggregate-summaries`, and `--events-report` are mutually exclusive.

### Config file (`config.ini`)

- **Purpose**: Centralize default values for most CLI options.
- **Enabling**: Pass `--config-file path/to/config.ini` to load an INI config. If a `config.ini` exists in the same directory as `jastm.py`, it is loaded automatically (no flag needed).
- **Precedence**:
  - Command-line arguments **override** values from `config.ini`.
  - `config.ini` values override built-in defaults.
- **Config-managed options**:
  - Collection: `sample_rate`
  - Analysis: `cpu_peak_percentage`, `ram_peak_percentage`
- **CLI-only options (not stored in config)**:
  - `--parse-file`, `--aggregate-summaries`, `--events-report`
  - `--summary`, `--metrices-window`
  - `--program`
- **`analyze` subcommand behavior**:
  - Collection settings from `config.ini` are ignored.
  - Analysis thresholds from `config.ini` still apply unless overridden on the CLI.

Example `config.ini`:

```ini
[collection]
sample_rate = 1.0

[analysis]
cpu_peak_percentage = 90.0
ram_peak_percentage = 50.0
```

Empty or commented-out values are treated as not set. Process targeting (`--program`) is CLI-only and cannot be set in the config file.

## Collected metrics

- **CPU**: Per-process CPU % (when a process is targeted) or system-wide CPU % (when no process is specified). First sample after start may be 0 (priming).
- **Memory (System)**: System-wide **available** memory in MB (`psutil.virtual_memory().available`).
- **Virtual Address Space (Process-specific)**:
  - **VMS**: Virtual Memory Size (total address space reserved).
  - **RSS**: Resident Set Size (actual physical RAM used).

## CSV format

- **Header**: `Timestamp`, `CPU_Usage_%`, `Memory_MB`, `VMS_MB`, `RSS_MB`
- **Timestamp**: ISO format `YYYY-MM-DD HH:MM:SS`
- **CPU_Usage_%**: Float (e.g. 6 decimals)
- **Memory_MB**: Float (2 decimals)
- **VMS_MB / RSS_MB**: Float (2 decimals, or `N/A` if no process specified)

## Analysis behavior

- **Duration**: From first to last timestamp in the CSV.
- **Stats**: Min / max / average for CPU (%) and memory (MB).
- **Peak detection**:
  - **CPU peak**: Sample where `CPU_Usage_% > cpu_peak_percentage`.
  - **Memory peak**: Sample where `Memory_MB < avg_memory * (1 - ram_peak_percentage/100)` (low available RAM).
- **Memory trend (R² & slope)**: Linear regression of `Memory_MB` over elapsed time is computed to show a **slope in MB/hour** and an **R-squared index** (`R^2`) indicating how well a linear trend explains memory behavior.
- **VAS Analysis & Fragmentation Risk**: 
  - Calculates the overall trend (slope) for **VMS**, **RSS**, and the **Fragmentation Gap** (VMS - RSS).
  - **Fragmentation Risk Alert**: Triggered if the `VMS / RSS` ratio exceeds 1.5x, or if VMS shows a steady upward slope while RSS remains relatively flat (indicating potential address space exhaustion).
- **Summary output example**:
  ```
  === Summary Report ===
  Duration: 0.06 hours = 0.00 days
  Time Period: 2023-10-25 10:00:00 ~ 2023-10-25 10:00:05
  CPU Stats: Avg=25.36% | Min=5.50% | Max=95.00%
  Memory Stats: Avg=1980.15 MB | Min=1800.00 MB | Max=2100.00 MB
  System Memory Trend: slope=-34740.00 MB/hour | R^2=0.018 (decreasing)

  Process VAS Stats:
    VMS (Virtual Size): Min=100.00 MB | Max=200.00 MB
    RSS (Working Set):  Min=50.00 MB | Max=54.00 MB
    VMS Trend: slope=72000.00 MB/hour | R^2=0.850
    RSS Trend: slope=2880.00 MB/hour | R^2=0.980
    Fragmentation Gap Trend: slope=69120.00 MB/hour

    [!] FRAGMENTATION RISK DETECTED:
        - VMS is growing steadily while RSS is relatively flat.

  #### CPU Peaks (> 90.00%)
  | Timestamp | CPU (%) | Memory (MB) |
  | :--- | :--- | :--- |
  | 2023-10-25 10:00:03 | 95.00% | 1800.00 |
  ```

  (Peak rows show only samples exceeding peak thresholds. Tables may be empty if no peaks detected.)
- **Metrics window**: Chart of elapsed time vs. scaled CPU (×20) and available memory (MB); scatter overlay for CPU (red) and memory (orange) peaks; zoom (scroll), hover (interpolated values), arrow-key cursor; average CPU and average memory labels on the right.
![The Metrics Window](images/matrices_window.png)

## Testing

JASTM includes an automated testing suite under the `tests/` directory to ensure all features work reliably without regressions.

To run the tests:

- **Happy Path Tests** (`tests/happy_path.py`): Verifies the core, end-to-end workflows (system-wide monitoring, launching a program, generating summaries, and aggregating multiple CSVs). Run it via:
  ```bash
  python tests/happy_path.py
  ```
  *Tip: You can list all the testing items and their expected results by passing the `--list-items` flag:*
  ```bash
  python tests/happy_path.py --list-items
  ```

- **Smoke Tests** (`tests/smoke_test.py`): A comprehensive `unittest` suite that rigorously tests edge cases, parameter validation, process termination behaviors, and detailed output parsing. Run it via:
  ```bash
  python -m unittest tests.smoke_test -v
  ```
  *Tip: You can list all the testing items and their expected results by passing the `--list-items` flag:*
  ```bash
  python tests/smoke_test.py --list-items
  ```

Both test scripts automatically clean up any generated `*_monitor.csv` artifacts after execution to keep your directory tidy.

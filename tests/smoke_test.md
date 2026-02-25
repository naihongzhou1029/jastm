# jastm Smoke Test Spec

Smoke tests based on [README.md](../README.md). Run these using `python` in an environment where `psutil` and `matplotlib` are installed.

---

## Prerequisites

- Python 3.x
- Dependencies: `pip install psutil matplotlib`
- Working directory: project root (so `python jastm.py` and paths to CSV resolve correctly)

---

## 1. Help and CLI

| ID   | Description | Command | Expected |
|------|-------------|---------|----------|
| 1.1  | Help output | `python jastm.py --help` | Exit 0; usage, `--parse-file`, `--process-name`, `--process-id`, `--program`, `--sample-rate`, `--summary`, `--metrices-window`, `--cpu-peak-percentage`, `--ram-peak-percentage` visible |

---

## 2. Option validation

| ID   | Description | Command | Expected |
|------|-------------|---------|----------|
| 2.1  | Reject non-positive sample rate | `python jastm.py --sample-rate 0` | Exit non-zero; error mentioning `--sample-rate` |
| 2.2  | Reject negative sample rate | `python jastm.py --sample-rate -1` | Exit non-zero; error mentioning `--sample-rate` |
| 2.3  | Reject analysis + process name | `python jastm.py --parse-file x.csv --process-name "python.exe"` | Exit non-zero; "Cannot specify process/program when in Analysis Mode" (or equivalent) |
| 2.4  | Reject analysis + process ID | `python jastm.py --parse-file x.csv --process-id 12345` | Exit non-zero; same as 2.3 |
| 2.5  | Reject analysis + program | `python jastm.py --parse-file x.csv --program notepad.exe` | Exit non-zero; same as 2.3 |
| 2.6  | Reject empty --program | `python jastm.py --program` | Exit non-zero; "Error: --program requires a command to execute" (or equivalent) |

---

## 3. Data collection (short runs)

Use short runs (e.g. a few samples) and optional early exit (e.g. Ctrl+C or process exit) to keep tests fast.

| ID   | Description | Command | Expected |
|------|-------------|---------|----------|
| 3.1  | System-wide collection starts | `python jastm.py --sample-rate 0.5` | Exit 0 after manual stop (e.g. Ctrl+C); message "Logging to: …"; CSV created with header `Timestamp`, `CPU_Usage_%`, `Memory_MB` and at least one data row |
| 3.2  | Process name filter | `python jastm.py --process-name "python.exe" --sample-rate 0.5` | Same as 3.1; log filename includes process name (e.g. `python_…_monitor.csv`) |
| 3.3  | PID filter | `python jastm.py --process-id <current_python_pid> --sample-rate 0.5` | Same as 3.1; log filename includes `PID<id>` (e.g. `PID12345_…_monitor.csv`). Use a known-running process PID. |
| 3.4  | CSV format | Inspect CSV from 3.1 | Header: `Timestamp`, `CPU_Usage_%`, `Memory_MB`. Timestamp ISO `YYYY-MM-DD HH:MM:SS`; CPU float; Memory_MB float (e.g. 2 decimals) |

---

## 4. Analysis mode

Use the sample CSV below (or a CSV produced by a 3.x run) for file-based tests.

**Sample CSV** (save as `tests/fixtures/smoke_sample.csv` or pass path from repo root). Header must be exactly `Timestamp`, `CPU_Usage_%`, `Memory_MB`:

```csv
Timestamp,CPU_Usage_%,Memory_MB
2023-10-25 10:00:00,5.5,2048.00
2023-10-25 10:00:01,12.3,2000.50
2023-10-25 10:00:02,8.0,1950.25
2023-10-25 10:00:03,95.0,1800.00
2023-10-25 10:00:04,6.0,2100.00
```

| ID   | Description | Command | Expected |
|------|-------------|---------|----------|
| 4.1  | Summary only | `python jastm.py --parse-file <path_to_sample.csv> --summary` | Exit 0; duration; min/max/avg CPU and memory; tables for CPU peaks and memory peaks (if any) |
| 4.2  | Metrics window only | `python jastm.py --parse-file <path_to_sample.csv> --metrices-window` | Exit 0; interactive chart opens (manual close); no crash |
| 4.3  | Summary + metrics window | `python jastm.py --parse-file <path_to_sample.csv> --summary --metrices-window` | Exit 0; summary printed; then chart opens |
| 4.4  | Analysis with no action | `python jastm.py --parse-file <path_to_sample.csv>` | Exit 0; message "Analysis mode selected but no action specified. Use --summary or --metrices-window." |
| 4.5  | Missing/invalid file | `python jastm.py --parse-file nonexistent.csv --summary` | Exit 1; error about file load/failure |
| 4.6  | Custom peak thresholds | `python jastm.py --parse-file <path_to_sample.csv> --summary --cpu-peak-percentage 50 --ram-peak-percentage 30` | Exit 0; summary reflects different peak detection (inspect output for peak tables) |

---

## 5. Optional / environment-specific

| ID   | Description | Command | Expected |
|------|-------------|---------|----------|
| 5.1  | Launch and monitor | `python jastm.py --program <path_to_small_gui_or_cli_app> --sample-rate 1` | Program starts; monitor logs; exit 0 after program exit or manual stop. Skip on CI if no suitable test executable. |
| 5.2  | Process exit stops collection | Run 3.2 or 3.3 targeting a process that exits during the run | Collection stops after up to 10 consecutive metric failures; CSV contains data up to that point |

---

## Sample CSV fixture

Create `tests/fixtures/smoke_sample.csv` with the following content for analysis smoke tests (4.x):

```csv
Timestamp,CPU_Usage_%,Memory_MB
2023-10-25 10:00:00,5.5,2048.00
2023-10-25 10:00:01,12.3,2000.50
2023-10-25 10:00:02,8.0,1950.25
2023-10-25 10:00:03,95.0,1800.00
2023-10-25 10:00:04,6.0,2100.00
```

---

## Run order suggestion

1. **1.x** — Verify CLI and help.
2. **2.x** — Verify validation (no collection/analysis side effects).
3. **4.x** — Analysis with fixture CSV (no long-running collection).
4. **3.x** — Short data collection runs (optional 5.x if environment permits).

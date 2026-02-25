# jastm

Just Another Soak Testing Monitor — real-time system monitoring and post-run analysis for soak/stability testing.

## Overview

- **Data Collection Mode**: Samples CPU and memory at a configurable interval, optionally scoped to a process (by name, PID, or launched program). Writes a CSV log and supports an optional in-process GUI for live charts.
- **Analysis Mode**: Loads a collected CSV, computes statistics and peak detection, and optionally shows a summary report and/or an interactive metrics chart.

## Requirements

- **Python**: 3.x
- **Dependencies**: `psutil`, `matplotlib` (with TkAgg backend), `tkinter` (usually bundled with Python)

Install:

```bash
pip install psutil matplotlib
```

## Usage

### Data collection (soak run)

- **System-wide** (no process filter):  
  `python jastm.py`
- **By process name**:  
  `python jastm.py --process-name "python.exe"`
- **By PID**:  
  `python jastm.py --process-id 12345`
- **Launch and monitor a program**:  
  `python jastm.py --program myapp.exe [args...]`

Options:

| Option | Description | Default |
|--------|-------------|---------|
| `--sample-rate` | Sampling interval in seconds | `1.0` |

Log file is created automatically, using the pattern: `{process_name|PID{id}|timestamp}_{YYYYMMDD_HHMMSS}_monitor.csv` (for example, `chrome_PID1234_20231025_100000_monitor.csv`). The log contains these columns: `Timestamp`, `CPU_Usage_%`, and `Memory_MB`. Data collection stops after 10 consecutive metric failures (such as process exit).

### Analysis (post-run)

- **Summary only**:  
  `python jastm.py --parse-file 20231025_100000_monitor.csv --summary`
- **Interactive chart**:  
  `python jastm.py --parse-file 20231025_100000_monitor.csv --metrices-window`
- **Both**: use `--summary` and `--metrices-window` together.

Analysis options:

| Option | Description | Default |
|--------|-------------|---------|
| `--cpu-peak-percentage` | CPU peak = value above average by this % (e.g. 90 → 1.9× avg) | `90.0` |
| `--ram-peak-percentage` | Memory “peak” = available MB below average by this % (0–100) | `50.0` |

`--parse-file` cannot be combined with `--process-name`, `--process-id`, or `--program`.

## Collected metrics

- **CPU**: Per-process CPU % (when a process is targeted) or system-wide CPU % (when no process is specified). First sample after start may be 0 (priming).
- **Memory**: System-wide **available** memory in MB (`psutil.virtual_memory().available`).

## CSV format

- **Header**: `Timestamp`, `CPU_Usage_%`, `Memory_MB`
- **Timestamp**: ISO format `YYYY-MM-DD HH:MM:SS`
- **CPU_Usage_%**: Float (e.g. 6 decimals)
- **Memory_MB**: Float (2 decimals)

## Analysis behavior

- **Duration**: From first to last timestamp in the CSV.
- **Stats**: Min / max / average for CPU (%) and memory (MB).
- **Peak detection**:
  - **CPU peak**: Sample where `CPU_Usage_% > avg_cpu * (1 + cpu_peak_percentage/100)`.
  - **Memory peak**: Sample where `Memory_MB < avg_memory * (1 - ram_peak_percentage/100)` (low available RAM).
- **Summary output**: Duration (hours/days), CPU and memory stats, and markdown tables of CPU peaks and memory peaks with timestamp, CPU %, and memory MB.
- **Metrics window**: Chart of elapsed time vs. scaled CPU (×20) and available memory (MB); scatter overlay for CPU (red) and memory (orange) peaks; zoom (scroll), hover (interpolated values), arrow-key cursor; average CPU and average memory labels on the right.
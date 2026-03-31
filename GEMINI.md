# JASTM (Just Another Soak Testing Monitor) - Project Context

Welcome to the `jastm` project. This document provides essential context for AI agents working on this codebase.

## Project Overview

`jastm` is a specialized toolset for system stability and soak testing. It focuses on real-time resource monitoring (CPU, Memory) and comprehensive post-test analysis.

- **Main Tool (`jastm.py`)**: A CLI tool with two primary modes:
    - `monitor`: Samples system or process-specific metrics (CPU %, RSS, VMS, etc.) and logs them to CSV.
    - `analyze`: Processes logs to generate summaries, detect peaks, calculate trends (linear regression), and visualize data using an interactive `matplotlib` window. It can also generate Windows Event Log reports.
- **Companion Tool (`mmc.py`)**: A Windows-only utility for configuring display topology (Extend, Clone, etc.) and per-monitor resolutions/refresh rates.

## Technologies and Dependencies

- **Language**: Python 3.x
- **Core Dependencies**:
    - `psutil`: For system and process metric collection.
    - `matplotlib`: For GUI-based metrics visualization.
    - `tkinter`: Required for the interactive chart window.
- **System APIs**: Uses `ctypes` to interact with Windows User32 and GDI32 for display configuration (`mmc.py`).

## Key Commands

### Development and Testing
- **Install Dependencies**: `pip install psutil matplotlib`
- **Run Smoke Tests**: `python -m unittest tests.smoke_test -v` or `python tests/smoke_test.py`
- **Run Happy Path Tests**: `python tests/happy_path.py`
- **List Test Items**: `python tests/smoke_test.py --list-items`

### Monitoring
- **System-wide Monitoring**: `python jastm.py monitor`
- **Targeted Monitoring**: `python jastm.py monitor --program "path/to/app.exe" --sample-rate 0.5`

### Analysis
- **Summary and Chart**: `python jastm.py analyze --parse-file <logfile.csv> --summary --metrices-window`
- **Aggregate Multiple Logs**: `python jastm.py analyze --aggregate-summaries *.csv`
- **Windows Event Report**: `python jastm.py analyze --events-report`

### Configuration
- **Multi-Monitor**: `python mmc.py --config-file mmc.ini`

## Project Structure and Conventions

- **`jastm.py`**: Contains the `DataCollector` class for monitoring and various analysis functions. It includes self-bootstrapping dependency checks (`_ensure_dependency`).
- **`mmc.py`**: Windows-specific low-level display logic.
- **`tests/`**: Contains automated tests and sample fixtures.
    - `smoke_test.py`: Rigorous unit and integration tests.
    - `happy_path.py`: End-to-end workflow verification.
- **`wiki/journal.md`**: **Critical Reference.** This file records the development history, technical decisions, and current implementation phase. Always check this before starting new tasks.
- **Configuration**: Uses `.ini` files (`config.ini`, `mmc.ini`). CLI arguments generally override config file values.

## Architecture and Patterns

- **Data Logging**: Metrics are stored in CSV format with ISO timestamps.
- **Analysis Logic**: Includes linear regression for memory leak detection (slope/R²) and fragmentation risk analysis (VMS vs RSS trend).
- **GUI Visualization**: The `MetricesWindow` class in `jastm.py` provides a custom interactive chart with zooming and hover support.
- **Error Handling**: Monitoring is robust against transient failures; it stops after 10 consecutive sample errors.

## AI Agent Guidelines

1.  **Safety First**: `mmc.py` and `--events-report` are Windows-only. Guard system-specific logic appropriately.
2.  **Validation**: Every feature or fix MUST be accompanied by updates to `smoke_test.py` or a new test case. Use the existing test helpers (`run_jastm`, `_write_temp_config_ini`) to maintain consistency.
3.  **Journaling**: When completing a task, update `wiki/journal.md` with your changes, decisions, and any new insights.
4.  **Dependencies**: Respect the `_ensure_dependency` pattern for new third-party libraries.

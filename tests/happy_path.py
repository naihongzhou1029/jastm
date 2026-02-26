import os
import subprocess
import sys
import time
import glob
import csv

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)
JASTM_PY = os.path.join(PROJECT_ROOT, "jastm.py")
SAMPLE_CSV = os.path.join(TESTS_DIR, "fixtures", "smoke_sample.csv")

def print_result(name, success, message=""):
    status = "OK" if success else "FAILED"
    print(f"[{status}] {name}")
    if message:
        print(f"      {message}")

def find_recent_csv(pattern="*_monitor.csv", within_seconds=10, name_contains=None):
    now = time.time()
    candidates = [p for p in glob.glob(os.path.join(PROJECT_ROOT, pattern)) if now - os.path.getmtime(p) <= within_seconds]
    if name_contains:
        candidates = [p for p in candidates if name_contains.lower() in os.path.basename(p).lower()]
    return max(candidates, key=os.path.getmtime) if candidates else None

def clean_up_csvs(pattern="*_monitor.csv"):
    for p in glob.glob(os.path.join(PROJECT_ROOT, pattern)):
        try:
            os.remove(p)
        except:
            pass

def path_1_system_wide():
    print("Testing Path 1: System-wide monitoring (no args)...")
    cmd = [sys.executable, JASTM_PY, "--sample-rate", "0.5"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    time.sleep(3)
    proc.terminate()
    try:
        stdout, stderr = proc.communicate(timeout=2)
    except:
        proc.kill()
        stdout, stderr = proc.communicate()
    
    csv_file = find_recent_csv()
    success = csv_file is not None and os.path.exists(csv_file)
    if success:
        with open(csv_file, 'r') as f:
            lines = f.readlines()
            success = len(lines) > 1
    
    print_result("Path 1: System-wide monitoring", success, f"Log: {csv_file}" if success else "CSV not found or empty")
    return success

def path_2_process_name():
    print("Testing Path 2: Monitoring by process name (python.exe)...")
    # Using python.exe or python depending on OS, but README says "python.exe" for example.
    # We'll use the current process name.
    proc_name = os.path.basename(sys.executable)
    cmd = [sys.executable, JASTM_PY, "--process-name", proc_name, "--sample-rate", "0.5"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    time.sleep(3)
    proc.terminate()
    try:
        stdout, stderr = proc.communicate(timeout=2)
    except:
        proc.kill()
        stdout, stderr = proc.communicate()
    
    csv_file = find_recent_csv(name_contains=proc_name.split('.')[0] if '.' in proc_name else proc_name)
    success = csv_file is not None
    print_result("Path 2: Monitoring by process name", success, f"Log: {csv_file}" if success else "CSV not found")
    return success

def path_3_launch_program():
    print("Testing Path 3: Launch and monitor program...")
    # Use python to sleep for 5 seconds (must be > 3s because jastm.py waits 3s before checking if proc is alive)
    cmd = [sys.executable, "-u", JASTM_PY, "--program", sys.executable, "-c", "import time; time.sleep(5)"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    success = result.returncode == 0
    # Log filename should contain the program stem ('python'), extend search window to 30s
    # because jastm waits 3s + program runs 5s + teardown before the CSV is available
    proc_name = os.path.splitext(os.path.basename(sys.executable))[0]
    csv_file = find_recent_csv(name_contains=proc_name, within_seconds=30)
    success = success and csv_file is not None
    
    print_result("Path 3: Launch and monitor program", success, f"Log: {csv_file}" if success else f"Command failed or CSV missing. returncode={result.returncode}, stderr={result.stderr.strip()}")
    return success

def path_4_analysis_summary():
    print("Testing Path 4: Analysis mode (summary)...")
    cmd = [sys.executable, "-u", JASTM_PY, "--parse-file", SAMPLE_CSV, "--summary"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # Actual output uses "Duration:" and "CPU Stats:"
    success = result.returncode == 0 and "Duration:" in result.stdout and "CPU Stats:" in result.stdout
    print_result("Path 4: Analysis mode (summary)", success, "Summary output verified" if success else "Output missing expected keywords")
    return success

def path_5_aggregate_summaries():
    print("Testing Path 5: Aggregate summaries...")
    cmd = [sys.executable, JASTM_PY, "--aggregate-summaries", SAMPLE_CSV, SAMPLE_CSV]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    success = result.returncode == 0 and "Aggregated Summary Report" in result.stdout and "|" in result.stdout
    print_result("Path 5: Aggregate summaries", success, "Aggregated report generated" if success else "Markdown table not found")
    return success

def path_6_config_file_override():
    print("Testing Path 6: Config file override (machine_id)...")
    temp_cfg = os.path.join(PROJECT_ROOT, "temp_test_config.yaml")
    with open(temp_cfg, "w") as f:
        f.write("version: 1\ncollection:\n  machine_id:\n    value: '8888'\n")
    
    # We need --sample-rate because collection mode requires a loop, and we want to terminate it
    cmd = [sys.executable, "-u", JASTM_PY, "--config-file", temp_cfg, "--sample-rate", "0.5"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    time.sleep(3)
    proc.terminate()
    try:
        stdout, stderr = proc.communicate(timeout=3)
    except:
        proc.kill()
        stdout, stderr = proc.communicate()
    
    # Print for debugging if it fails
    # print(f"STDOUT: {stdout}")
    success = "Machine ID: 8888" in stdout
    
    if os.path.exists(temp_cfg):
        os.remove(temp_cfg)
        
    print_result("Path 6: Config file override", success, "Detected Machine ID: 8888 from config" if success else "Config machine_id not found in output")
    return success

def list_items():
    print("--- JASTM Happy Path Test Items ---")
    items = [
        ("Path 1: System-wide monitoring", "Run jastm.py with no args.", "A *_monitor.csv is created and populated with data."),
        ("Path 2: Monitoring by process name", "Run jastm.py with --process-name python.exe.", "A <process>_*_monitor.csv is created and populated."),
        ("Path 3: Launch and monitor program", "Run jastm.py with --program.", "JASTM successfully launches the program, logs to a CSV, and exits 0."),
        ("Path 4: Analysis mode (summary)", "Run jastm.py with --parse-file <csv> --summary.", "Prints a textual summary with Duration and CPU Stats, exits 0."),
        ("Path 5: Aggregate summaries", "Run jastm.py with --aggregate-summaries <csv1> <csv2>.", "Prints a Markdown table with 'Aggregated Summary Report'."),
        ("Path 6: Config file override", "Run jastm.py with --config-file.", "The output correctly logs using the machine_id from the config.")
    ]
    for name, action, expected in items:
        print(f"\n{name}")
        print(f"  Action:   {action}")
        print(f"  Expected: {expected}")
    sys.exit(0)

def main():
    if "--list-items" in sys.argv:
        list_items()
        
    print("--- Running JASTM Happy Path Tests ---")
    results = []
    
    # Pre-test cleanup
    clean_up_csvs()
    
    # Temporarily move config.yaml so it doesn't affect tests that expect default behavior
    cfg_path = os.path.join(PROJECT_ROOT, "config.yaml")
    bak_path = os.path.join(PROJECT_ROOT, "config.yaml.bak")
    if os.path.exists(cfg_path):
        os.rename(cfg_path, bak_path)
    
    try:
        results.append(path_1_system_wide())
        results.append(path_2_process_name())
        results.append(path_3_launch_program())
        results.append(path_4_analysis_summary())
        results.append(path_5_aggregate_summaries())
        results.append(path_6_config_file_override())
        
        print("\n--- Summary ---")
        all_ok = all(results)
        if all_ok:
            print("ALL HAPPY PATHS PASSED!")
        else:
            print(f"SOME PATHS FAILED: {results.count(False)}/{len(results)}")
    finally:
        # Post-test cleanup
        clean_up_csvs()
        temp_cfg = os.path.join(PROJECT_ROOT, "temp_test_config.yaml")
        if os.path.exists(temp_cfg):
            try:
                os.remove(temp_cfg)
            except:
                pass
                
        # Restore config.yaml
        if os.path.exists(bak_path):
            if os.path.exists(cfg_path):
                os.remove(cfg_path)
            os.rename(bak_path, cfg_path)

    sys.exit(0 if all_ok else 1)

if __name__ == "__main__":
    main()

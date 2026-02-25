#!/usr/bin/env python3
"""
Robustness Monitor - Real-time system monitoring tool with GUI visualization.
Monitors CPU usage and system memory, displaying data in a live line chart.
"""

import argparse
import csv
import os
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta
from typing import Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None


DEFAULT_SAMPLE_RATE = 1.0
DEFAULT_CPU_PEAK_PERCENTAGE = 90.0
DEFAULT_RAM_PEAK_PERCENTAGE = 50.0

try:
    import psutil
except ImportError:
    print("Error: psutil is required. Install it with: pip install psutil", file=sys.stderr)
    sys.exit(1)

try:
    import matplotlib
    matplotlib.use('TkAgg')
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.figure import Figure
    import matplotlib.pyplot as plt
except ImportError:
    print("Error: matplotlib is required. Install it with: pip install matplotlib", file=sys.stderr)
    sys.exit(1)

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    print("Error: tkinter is required. It should be included with Python.", file=sys.stderr)
    sys.exit(1)


class DataCollector:
    """Main monitoring application class."""
    
    def __init__(self, process_name: Optional[str] = None, process_id: Optional[int] = None,
                 sample_rate: float = 1.0):
        """Initialize the monitor with process identification and configuration."""
        self.process_name = process_name
        self.process_id = process_id
        self.sample_rate = sample_rate
        
        # Generate log filename automatically
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.process_name:
            clean_name = "".join(c for c in self.process_name if c.isalnum() or c in ('-', '_', '.'))
            self.log_file = f"{clean_name}_{timestamp_str}_monitor.csv"
        elif self.process_id:
             self.log_file = f"PID{self.process_id}_{timestamp_str}_monitor.csv"
        else:
            self.log_file = f"{timestamp_str}_monitor.csv"
            
        print(f"Logging to: {self.log_file}")
        
        # Process object
        self.process: Optional[psutil.Process] = None
        
        # Data storage (rolling buffer)
        self.max_samples = 1000
        self.timestamps = deque(maxlen=self.max_samples)
        self.cpu_data = deque(maxlen=self.max_samples)
        self.memory_data = deque(maxlen=self.max_samples)
        
        # Total elapsed time tracking
        self.total_elapsed_time = 0.0
        
        # GUI components
        self.root: Optional[tk.Tk] = None
        self.fig: Optional[Figure] = None
        self.ax: Optional[plt.Axes] = None
        self.canvas: Optional[FigureCanvasTkAgg] = None
        self.cpu_line = None
        self.memory_line = None
        self.hover_line = None  # Vertical line for hover
        self.cpu_label = None  # Label for CPU value at hover
        self.memory_label = None  # Label for Memory value at hover
        self.sample_rate_var: Optional[tk.StringVar] = None
        self.sample_rate_entry: Optional[ttk.Entry] = None
        self.x_scrollbar: Optional[ttk.Scale] = None
        
        # X-axis interaction state
        self.auto_x = True  # When True, x-axis auto-fits incoming data
        self.x_window_size: Optional[float] = None  # Current visible window width in seconds
        
        # CPU scaling factor for visualization (makes CPU trend more visible)
        self.cpu_scale_factor = 20.0  # Scale CPU % by this factor for rendering
        
        # Threading
        self.monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()
        self.consecutive_failures = 0
        self.max_consecutive_failures = 10
        
        # CSV file handle
        self.csv_writer = None
        self.csv_file = None
        self._init_csv_logging()
        
        # Launched process reference (for --program option)
        self.launched_process = None
    
    def _init_csv_logging(self):
        """Initialize CSV logging file."""
        try:
            self.csv_file = open(self.log_file, 'w', newline='')
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(['Timestamp', 'CPU_Usage_%', 'Memory_MB'])
            self.csv_file.flush()
        except IOError as e:
            print(f"Warning: Could not open log file {self.log_file}: {e}", file=sys.stderr)
            self.log_file = None
    
    def get_process(self) -> bool:
        """Resolve process from name or PID. Returns True if successful. If no process specified, returns True for system-wide monitoring."""
        try:
            if self.process_id:
                self.process = psutil.Process(self.process_id)
            elif self.process_name:
                # Find process by name
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        if proc.info['name'] == self.process_name:
                            print(f"Monitoring Process: {proc.info['name']} (PID: {proc.info['pid']})")
                            self.process = psutil.Process(proc.info['pid'])
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                
                if self.process is None:
                    print(f"Error: Process '{self.process_name}' not found.", file=sys.stderr)
                    return False
            
            # If no process specified, monitor system-wide (self.process remains None)
            if self.process is None:
                return True
            
            # Verify process is accessible
            _ = self.process.status()
            return True
        except psutil.NoSuchProcess:
            print(f"Error: Process not found (PID: {self.process_id}, Name: {self.process_name}).", file=sys.stderr)
            return False
        except psutil.AccessDenied:
            print(f"Error: Access denied to process (PID: {self.process_id}, Name: {self.process_name}).", file=sys.stderr)
            return False
        except Exception as e:
            print(f"Error: Failed to get process: {e}", file=sys.stderr)
            return False
    
    def collect_metrics(self) -> Tuple[float, float]:
        """Collect CPU usage and memory metrics. Returns (cpu_percent, memory_mb)."""
        try:
            # CPU usage
            if self.process:
                # Use interval=None for non-blocking since we manage sleep in loop
                cpu_percent = self.process.cpu_percent(interval=None)
            else:
                cpu_percent = psutil.cpu_percent(interval=None)
            
            # System-wide free memory in MB
            memory_mb = psutil.virtual_memory().available / (1024 * 1024)
            
            return cpu_percent, memory_mb
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Process terminated or access denied
            return None, None
        except Exception as e:
            print(f"Warning: Error collecting metrics: {e}", file=sys.stderr)
            return None, None
    
    def write_log(self, timestamp: datetime, cpu_percent: float, memory_mb: float):
        """Write metrics to CSV log file if enabled."""
        if self.csv_writer and self.csv_file:
            try:
                self.csv_writer.writerow([
                    timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                    f"{cpu_percent:.6f}",
                    f"{memory_mb:.2f}"
                ])
                self.csv_file.flush()
            except IOError as e:
                print(f"Warning: Error writing to log file: {e}", file=sys.stderr)
    
    def setup_gui(self):
        """Initialize GUI components."""
        self.root = tk.Tk()
        self.root.title("Robustness Monitor")
        self.root.geometry("1000x700")
        
        # Create matplotlib figure
        self.fig = Figure(figsize=(10, 6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        
        # Initialize empty plots
        self.cpu_line, = self.ax.plot([], [], label='CPU Usage (%)', color='blue', linewidth=2)
        self.memory_line, = self.ax.plot([], [], label='Memory (MB)', color='red', linewidth=2)
        
        # Initialize hover elements (initially hidden)
        # Use plot() for hover line so we can easily update its position
        self.hover_line, = self.ax.plot([0, 0], [0, 1], color='gray', linestyle='--', linewidth=1, alpha=0.7, visible=False)
        self.cpu_label = self.ax.text(0, 0, '', fontsize=9, bbox=dict(boxstyle='round,pad=0.3', facecolor='lightblue', alpha=0.8), visible=False)
        self.memory_label = self.ax.text(0, 0, '', fontsize=9, bbox=dict(boxstyle='round,pad=0.3', facecolor='lightcoral', alpha=0.8), visible=False)
        
        self.ax.set_xlabel('Time')
        self.ax.set_ylabel('Value')
        
        # Set title with process information if monitoring a specific process
        title = 'System Monitoring'
        if self.process is not None:
            try:
                # Prefer process name, fall back to PID
                proc_name = self.process.name()
                title += f' ({proc_name})'
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # If we can't get the name, use PID or original process_name/process_id
                if self.process_id:
                    title += f' (PID: {self.process_id})'
                elif self.process_name:
                    title += f' ({self.process_name})'
        elif self.process_name:
            # Process not resolved yet, but we have a name
            title += f' ({self.process_name})'
        elif self.process_id:
            # Process not resolved yet, but we have a PID
            title += f' (PID: {self.process_id})'
        
        self.ax.set_title(title)
        self.ax.legend(loc='upper left')
        self.ax.grid(True, alpha=0.3)
        
        # Hide y-axis tick labels (values shown in hover labels instead)
        self.ax.set_yticklabels([])
        
        # Embed matplotlib in tkinter
        self.canvas = FigureCanvasTkAgg(self.fig, self.root)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        
        # Add mouse wheel zoom for x-axis
        def on_scroll(event):
            if event.inaxes != self.ax:
                return
            
            # Resolve data bounds to constrain zoom/pan
            with self.lock:
                if not self.timestamps:
                    return
                times = list(self.timestamps)
            data_min = min(times)
            data_max = max(times)
            
            # Get current x-axis limits
            xlim = self.ax.get_xlim()
            x_range = xlim[1] - xlim[0]
            if x_range <= 0:
                x_range = max(data_max - data_min, 1.0)
            
            # Zoom factor
            zoom_factor = 1.1 if event.button == 'up' else 1 / 1.1
            
            # Calculate new visible range
            new_range = x_range * zoom_factor
            # Prevent pathological zero-width ranges
            min_range = max((data_max - data_min) * 0.01, 0.1)
            if new_range < min_range:
                new_range = min_range
            
            # Center zoom on mouse position when possible
            xdata = event.xdata
            if xdata is not None:
                center = xdata
            else:
                center = (xlim[0] + xlim[1]) / 2
            
            # Compute tentative new limits
            left = center - new_range / 2
            right = center + new_range / 2
            
            # Clamp to available data range when data range is valid
            if data_max > data_min:
                if left < data_min:
                    left = data_min
                    right = left + new_range
                if right > data_max:
                    right = data_max
                    left = right - new_range
            
            # Enter manual zoom / navigation mode
            self.auto_x = False
            self.x_window_size = right - left
            self.ax.set_xlim(left, right)
            
            # Enable x-axis scrollbar when in manual mode
            if self.x_scrollbar is not None:
                self.x_scrollbar.state(['!disabled'])
                self._sync_x_scrollbar(data_min, data_max)
            
            self.canvas.draw_idle()
        
        self.canvas.mpl_connect('scroll_event', on_scroll)
        
        # Add mouse motion handler for hover feature
        def on_mouse_move(event):
            if event.inaxes != self.ax:
                # Mouse outside axes, hide hover elements
                self.hover_line.set_visible(False)
                self.cpu_label.set_visible(False)
                self.memory_label.set_visible(False)
                self.canvas.draw_idle()
                return
            
            if event.xdata is None:
                return
            
            # Get data with lock
            with self.lock:
                if not self.timestamps:
                    return
                times = list(self.timestamps)
                cpu_values = list(self.cpu_data)
                memory_values = list(self.memory_data)
            
            x_pos = event.xdata
            
            # Find interpolated values at x_pos
            cpu_val = self._interpolate_value(times, cpu_values, x_pos)
            memory_val = self._interpolate_value(times, memory_values, x_pos)
            
            if cpu_val is None or memory_val is None:
                # No valid data, hide hover elements
                self.hover_line.set_visible(False)
                self.cpu_label.set_visible(False)
                self.memory_label.set_visible(False)
                self.canvas.draw_idle()
                return
            
            # Update vertical line position (span full y-axis)
            ylim = self.ax.get_ylim()
            self.hover_line.set_data([x_pos, x_pos], [ylim[0], ylim[1]])
            self.hover_line.set_visible(True)
            
            # Get y-axis limits for label positioning
            ylim = self.ax.get_ylim()
            y_range = ylim[1] - ylim[0]
            label_offset = y_range * 0.02  # Small offset from the line
            
            # Position CPU label near the intersection point (use scaled value for position, original for text)
            scaled_cpu_val = cpu_val * self.cpu_scale_factor
            self.cpu_label.set_position((x_pos, scaled_cpu_val + label_offset))
            self.cpu_label.set_text(f'CPU: {cpu_val:.2f}%')
            self.cpu_label.set_visible(True)
            
            # Position Memory label near the intersection point (offset to avoid overlap)
            self.memory_label.set_position((x_pos, memory_val - label_offset))
            self.memory_label.set_text(f'Mem: {memory_val:.2f} MB')
            self.memory_label.set_visible(True)
            
            self.canvas.draw_idle()
        
        self.canvas.mpl_connect('motion_notify_event', on_mouse_move)
        
        # Add navigation toolbar
        toolbar = NavigationToolbar2Tk(self.canvas, self.root)
        toolbar.update()
        
        # Control panel
        control_frame = ttk.Frame(self.root)
        control_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        
        # Sample rate control
        ttk.Label(control_frame, text="Sample Rate (seconds):").pack(side=tk.LEFT, padx=5)
        self.sample_rate_var = tk.StringVar(value=str(self.sample_rate))
        self.sample_rate_entry = ttk.Entry(control_frame, textvariable=self.sample_rate_var, width=10)
        self.sample_rate_entry.pack(side=tk.LEFT, padx=5)
        self.sample_rate_entry.bind('<Return>', self.on_sample_rate_change)
        self.sample_rate_entry.bind('<FocusOut>', self.on_sample_rate_change)
        
        # Current values display
        self.status_label = ttk.Label(control_frame, text="CPU: 0.0% | Memory: 0.0 MB")
        self.status_label.pack(side=tk.LEFT, padx=20)
        
        # X-axis scroll bar for navigating in manual zoom mode
        self.x_scrollbar = ttk.Scale(
            control_frame,
            from_=0.0,
            to=1.0,
            orient=tk.HORIZONTAL,
            command=self.on_x_scroll
        )
        self.x_scrollbar.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=5)
        # Disabled until user enters manual zoom mode
        self.x_scrollbar.state(['disabled'])
        
        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def _format_elapsed_time(self, seconds: float) -> str:
        """Format elapsed seconds as DD:HH:MM:SS."""
        total_seconds = int(seconds)
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{days:02d}:{hours:02d}:{minutes:02d}:{secs:02d}"
    
    def _interpolate_value(self, times: list, values: list, x_pos: float) -> Optional[float]:
        """Interpolate value at x_pos from times and values lists. Returns None if no valid data."""
        if not times or not values or len(times) != len(values):
            return None
        
        # Check if x_pos is outside data range
        if x_pos < min(times) or x_pos > max(times):
            return None
        
        # Find the two closest points
        if len(times) == 1:
            return values[0]
        
        # Binary search for the interval containing x_pos
        left_idx = 0
        right_idx = len(times) - 1
        
        # Find the rightmost index where times[i] <= x_pos
        while left_idx < right_idx:
            mid = (left_idx + right_idx + 1) // 2
            if times[mid] <= x_pos:
                left_idx = mid
            else:
                right_idx = mid - 1
        
        # Now left_idx points to the point <= x_pos, check if we need to interpolate
        if left_idx == len(times) - 1:
            # x_pos is at or beyond the last point
            return values[left_idx]
        
        if times[left_idx] == x_pos:
            # Exact match
            return values[left_idx]
        
        # Interpolate between times[left_idx] and times[left_idx + 1]
        t0, t1 = times[left_idx], times[left_idx + 1]
        v0, v1 = values[left_idx], values[left_idx + 1]
        
        # Linear interpolation
        if t1 == t0:
            return v0
        
        ratio = (x_pos - t0) / (t1 - t0)
        interpolated = v0 + ratio * (v1 - v0)
        return interpolated
    
    def _sync_x_scrollbar(self, data_min: Optional[float] = None, data_max: Optional[float] = None):
        """Synchronize scrollbar position with current x-axis window."""
        if self.x_scrollbar is None or self.x_window_size is None:
            return
        
        # Derive data bounds if not supplied
        if data_min is None or data_max is None:
            with self.lock:
                if not self.timestamps:
                    return
                times = list(self.timestamps)
            data_min = min(times)
            data_max = max(times)
        
        total_span = data_max - data_min
        if total_span <= 0 or self.x_window_size is None or self.x_window_size >= total_span:
            # Nothing meaningful to scroll over
            self.x_scrollbar.set(0.0)
            return
        
        curr_left, curr_right = self.ax.get_xlim()
        visible_span = curr_right - curr_left
        if visible_span <= 0:
            return
        
        denom = total_span - self.x_window_size
        if denom <= 0:
            self.x_scrollbar.set(0.0)
            return
        
        value = (curr_left - data_min) / denom
        value = max(0.0, min(1.0, value))
        self.x_scrollbar.set(value)
    
    def on_x_scroll(self, value: str):
        """Handle x-axis scrollbar changes to pan the current zoom window."""
        if self.auto_x:
            # Ignore scrollbar in auto mode
            return
        
        try:
            slider_pos = float(value)
        except ValueError:
            return
        
        if self.x_window_size is None:
            return
        
        with self.lock:
            if not self.timestamps:
                return
            times = list(self.timestamps)
        data_min = min(times)
        data_max = max(times)
        
        total_span = data_max - data_min
        if total_span <= 0 or self.x_window_size >= total_span:
            # Nothing to pan
            return
        
        # Compute new window based on slider position
        max_offset = total_span - self.x_window_size
        offset = slider_pos * max_offset
        left = data_min + offset
        right = left + self.x_window_size
        
        self.ax.set_xlim(left, right)
        if self.canvas is not None:
            self.canvas.draw_idle()
    
    def on_sample_rate_change(self, event=None):
        """Handle sample rate change from text field."""
        try:
            new_rate = float(self.sample_rate_var.get())
            if new_rate > 0:
                with self.lock:
                    self.sample_rate = new_rate
            else:
                # Invalid value, revert to current
                self.sample_rate_var.set(str(self.sample_rate))
        except ValueError:
            # Invalid input, revert to current
            self.sample_rate_var.set(str(self.sample_rate))
    
    def update_chart(self):
        """Update the chart with current data."""
        # Update x-axis label with total elapsed time (even if no data yet)
        with self.lock:
            total_time = self.total_elapsed_time
        time_str = self._format_elapsed_time(total_time)
        self.ax.set_xlabel(f'Time ({time_str})')
        
        with self.lock:
            if not self.timestamps:
                return
            
            times = list(self.timestamps)
            cpu_values = list(self.cpu_data)
            memory_values = list(self.memory_data)
        
        # Update CPU line (scale CPU values for better visualization)
        if cpu_values:
            scaled_cpu_values = [v * self.cpu_scale_factor for v in cpu_values]
            self.cpu_line.set_data(times, scaled_cpu_values)
        
        # Update memory line
        if memory_values:
            self.memory_line.set_data(times, memory_values)
        
        # Auto-scale axes
        if times:
            t_min = min(times)
            t_max = max(times)
            
            # Hide hover elements if they're outside the current data range
            if self.hover_line and self.hover_line.get_visible():
                hover_xdata = self.hover_line.get_xdata()
                if len(hover_xdata) > 0:
                    hover_x = hover_xdata[0]
                    if hover_x < t_min or hover_x > t_max:
                        self.hover_line.set_visible(False)
                        if self.cpu_label:
                            self.cpu_label.set_visible(False)
                        if self.memory_label:
                            self.memory_label.set_visible(False)
            
            if self.auto_x:
                # In auto mode, always show full time range
                self.ax.set_xlim(t_min, t_max)
                self.x_window_size = t_max - t_min
                # Keep scrollbar logically at "end" but disabled
                if self.x_scrollbar is not None:
                    self.x_scrollbar.state(['disabled'])
                    self.x_scrollbar.set(1.0)
            else:
                # In manual mode, preserve current x-window but keep scrollbar in sync
                curr_left, curr_right = self.ax.get_xlim()
                self.x_window_size = curr_right - curr_left
                if self.x_scrollbar is not None:
                    self.x_scrollbar.state(['!disabled'])
                    self._sync_x_scrollbar(t_min, t_max)
            
            # Y-axis: combine both datasets for proper scaling (use scaled CPU values)
            all_values = []
            if cpu_values:
                scaled_cpu = [v * self.cpu_scale_factor for v in cpu_values]
                all_values.extend(scaled_cpu)
            if memory_values:
                all_values.extend(memory_values)
            
            if all_values:
                y_min = min(all_values)
                y_max = max(all_values)
                y_range = y_max - y_min
                if y_range > 0:
                    self.ax.set_ylim(y_min - y_range * 0.1, y_max + y_range * 0.1)
                else:
                    self.ax.set_ylim(y_min - 1, y_max + 1)
            
            # Update hover line y-data to span full y-axis if visible
            if self.hover_line and self.hover_line.get_visible():
                ylim = self.ax.get_ylim()
                hover_xdata = self.hover_line.get_xdata()
                if len(hover_xdata) > 0:
                    hover_x = hover_xdata[0]
                    self.hover_line.set_data([hover_x, hover_x], [ylim[0], ylim[1]])
        
        # Redraw
        self.fig.canvas.draw_idle()
    
    def monitoring_loop(self):
        """Main monitoring loop running in main thread (Headless)."""
        start_time = time.time()
        print(f"Monitoring started at {datetime.now().isoformat()}")
        print("Press Ctrl+C to stop.")
        
        try:
            while self.monitoring:
                loop_start = time.time()
                
                # Collect metrics
                cpu_percent, memory_mb = self.collect_metrics()
                
                if cpu_percent is not None and memory_mb is not None:
                    # Reset failure counter on success
                    self.consecutive_failures = 0
                    
                    current_time = time.time()
                    elapsed = current_time - start_time
                    
                    timestamp = datetime.now()
                    
                    # Store data
                    with self.lock:
                        self.timestamps.append(elapsed)
                        self.cpu_data.append(cpu_percent)
                        self.memory_data.append(memory_mb)
                        self.total_elapsed_time = elapsed
                    
                    # Write log (Always logging in default mode)
                    if self.log_file:
                        self.write_log(timestamp, cpu_percent, memory_mb)
                else:
                    # Process terminated or error
                    self.consecutive_failures += 1
                    print(f"Error: Process not accessible (failures: {self.consecutive_failures})")
                    
                    # Stop monitoring after too many consecutive failures
                    if self.consecutive_failures >= self.max_consecutive_failures:
                        print("Error: Process terminated or inaccessible. Stopping monitoring.")
                        break
                
                # Loop control
                
                loop_elapsed = time.time() - loop_start
                sleep_time = max(0, self.sample_rate - loop_elapsed)
                time.sleep(sleep_time)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_monitoring()
    
    def start_monitoring(self):
        """Start the monitoring thread."""
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self.monitoring_loop, daemon=True)
        self.monitor_thread.start()
    
    def stop_monitoring(self):
        """Stop the monitoring thread."""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2.0)
    
    def on_closing(self):
        """Handle cleanup."""
        self.monitoring = False
        if self.csv_file:
            try:
                self.csv_file.close()
            except:
                pass
        # Cleanup launched process if it exists
        if self.launched_process is not None:
            try:
                if self.launched_process.poll() is None:
                    self.launched_process.terminate()
                    self.launched_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.launched_process.kill()
            except Exception:
                pass
    
    def run(self):
        """Run the monitoring application in headless mode."""
        if not self.get_process():
            return False
        
        # Prime CPU percent measurement (first call with interval=None returns 0)
        try:
            if self.process:
                self.process.cpu_percent(interval=None)
            else:
                psutil.cpu_percent(interval=None)
        except:
            pass
            
        self.monitoring = True
        self.monitoring_loop()
        
        return True



class DataAnalyzer:
    """Analyzes and validates the collected metrics log file."""
    
    def __init__(self, filepath: str, cpu_peak_criteria: float = 0.9, ram_peak_criteria: float = 0.5):
        self.filepath = filepath
        self.cpu_peak_criteria = cpu_peak_criteria
        self.ram_peak_criteria = ram_peak_criteria
        self.timestamps = []
        self.cpu_data = []
        self.memory_data = []
        self.avg_cpu = 0.0
        self.avg_mem = 0.0
        self.duration_seconds = 0.0
        self.cpu_peaks = []
        self.memory_peaks = []
        # self.peaks = [] # Deprecated
        
        # GUI Components
        self.root = None
        self.fig = None
        self.ax = None
        self.canvas = None
        self.cpu_line = None
        self.memory_line = None
        self.hover_line = None
        self.cpu_label = None
        self.memory_label = None
        self.legend = None
        self.center_time_label = None
        
        # Zoom/Pan state
        self.x_window_size = None # Defaults to full range
        self.current_index = 0 # Track currently selected data point index
        
        # Start Time for absolute timestamps
        self.start_datetime = datetime.now()
    
    def load_data(self) -> bool:
        """Load and parse CSV data."""
        if not os.path.exists(self.filepath):
            print(f"Error: File not found: {self.filepath}", file=sys.stderr)
            return False
            
        try:
            with open(self.filepath, 'r') as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if not header:
                    print("Error: Empty file", file=sys.stderr)
                    return False
                
                start_time_val = None
                
                # Try to parse start time from first data row text for absolute reference
                # We do this by peeking or rewinding. simple way: read first row
                
                # Reset file? No, just continue reading
                
                for row in reader:
                    if len(row) < 3:
                        continue
                    
                    try:
                        ts_str, cpu_str, mem_str = row[0], row[1], row[2]
                        
                        # Parse ISO format timestamp
                        ts = datetime.fromisoformat(ts_str)
                        cpu = float(cpu_str)
                        mem = float(mem_str)
                        
                        if start_time_val is None:
                            start_time_val = ts
                            self.start_datetime = ts
                        
                        elapsed = (ts - start_time_val).total_seconds()
                        
                        self.timestamps.append(elapsed)
                        self.cpu_data.append(cpu)
                        self.memory_data.append(mem)
                        
                    except ValueError:
                        continue
                        
            if not self.timestamps:
                print("Error: No valid data found in file.", file=sys.stderr)
                return False
                
            # Pre-calculate stats
            self.duration_seconds = self.timestamps[-1] - self.timestamps[0]
            if self.cpu_data:
                self.avg_cpu = sum(self.cpu_data) / len(self.cpu_data)
            else:
                self.avg_cpu = 0.0
                
            if self.memory_data:
                self.avg_mem = sum(self.memory_data) / len(self.memory_data)
            else:
                self.avg_mem = 0.0
            
            # Identify peaks
            # CPU: High Usage -> Value > Avg * (1 + Criteria)
            # Memory (Available): Low Availability -> Value < Avg * (1 - Criteria)
            
            cpu_threshold = self.avg_cpu * (1.0 + self.cpu_peak_criteria)
            mem_threshold = self.avg_mem * (1.0 - self.ram_peak_criteria)
            
            self.cpu_peaks = []
            self.memory_peaks = []
            
            for i in range(len(self.timestamps)):
                t = self.timestamps[i]
                c = self.cpu_data[i]
                m = self.memory_data[i]
                
                if c > cpu_threshold:
                    self.cpu_peaks.append((t, c, m))
                    
                if m < mem_threshold:
                    self.memory_peaks.append((t, c, m))
                    
            # For backward compatibility / simplified logic, self.peaks could be CPU peaks?
            # Or remove self.peaks usage entirely in favor of specific lists.
            # I will remove self.peaks and update plotting code.
            
            return True
            
        except Exception as e:
            print(f"Error reading file: {e}", file=sys.stderr)
            return False

    def show_summary(self):
        """Print summary report."""
        hours = self.duration_seconds / 3600
        days = hours / 24
        
        min_cpu = min(self.cpu_data) if self.cpu_data else 0.0
        max_cpu = max(self.cpu_data) if self.cpu_data else 0.0
        min_mem = min(self.memory_data) if self.memory_data else 0.0
        max_mem = max(self.memory_data) if self.memory_data else 0.0
        
        print("\n=== Summary Report ===")
        print(f"Duration: {hours:.2f} hours = {days:.2f} days")
        if self.timestamps:
            start_dt = self.start_datetime + timedelta(seconds=self.timestamps[0])
            end_dt = self.start_datetime + timedelta(seconds=self.timestamps[-1])
            start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
            print(f"Time Period: {start_str} ~ {end_str}")
        print(f"CPU Stats: Avg={self.avg_cpu:.2f}% | Min={min_cpu:.2f}% | Max={max_cpu:.2f}%")
        print(f"Memory Stats: Avg={self.avg_mem:.2f} MB | Min={min_mem:.2f} MB | Max={max_mem:.2f} MB")
        
        print(f"\n### Peaks Report (CPU > {self.cpu_peak_criteria*100:.0f}%, RAM < {self.ram_peak_criteria*100:.0f}% deviation)")
        
        cpu_thresh_val = self.avg_cpu * (1+self.cpu_peak_criteria)
        print(f"\n#### CPU Peaks (> {cpu_thresh_val:.2f}%)")
        if not self.cpu_peaks:
            print("No cpu peaks detected.")
        else:
             print("| Timestamp | CPU (%) | Memory (MB) |")
             print("| :--- | :--- | :--- |")
             for t, c, m in self.cpu_peaks:
                 dt = self.start_datetime + timedelta(seconds=t)
                 ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                 print(f"| {ts_str} | {c:.2f}% | {m:.2f} |")

        mem_thresh_val = self.avg_mem * (1.0 - self.ram_peak_criteria)
        print(f"\n#### Memory Peaks (< {mem_thresh_val:.2f} MB)")
        if not self.memory_peaks:
            print("No memory peaks detected.")
        else:
             print("| Timestamp | CPU (%) | Memory (MB) |")
             print("| :--- | :--- | :--- |")
             for t, c, m in self.memory_peaks:
                 dt = self.start_datetime + timedelta(seconds=t)
                 ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                 print(f"| {ts_str} | {c:.2f}% | {m:.2f} |")
                
        print("======================\n")

    def show_metrics_window(self):
        """Launch the visualization window."""
        self.root = tk.Tk()
        self.root.title("Robustness Monitor - Analysis Mode")
        self.root.geometry("1200x800")
        
        self.fig = Figure(figsize=(10, 6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        
        # Plot Data
        # Green for Memory, Blue for CPU (per spec)
        # Using 20x scaling for CPU as per convention
        cpu_scale_factor = 20.0
        scaled_cpu = [c * cpu_scale_factor for c in self.cpu_data]
        
        self.cpu_line, = self.ax.plot(self.timestamps, scaled_cpu, label='CPU Usage (%) [x20]', color='blue', linewidth=1.5)
        self.memory_line, = self.ax.plot(self.timestamps, self.memory_data, label='Available Memory (MB)', color='green', linewidth=1.5)
        
        # Plot Peaks (Red)
        if self.cpu_peaks:
            c_peak_times = [p[0] for p in self.cpu_peaks]
            c_peak_vals = [p[1] * cpu_scale_factor for p in self.cpu_peaks]
            self.ax.scatter(c_peak_times, c_peak_vals, color='red', s=20, label='CPU Peaks', zorder=5)
            
        if self.memory_peaks:
            m_peak_times = [p[0] for p in self.memory_peaks]
            m_peak_vals = [p[2] for p in self.memory_peaks] # m is at index 2 now
            self.ax.scatter(m_peak_times, m_peak_vals, color='orange', s=20, label='Mem Peaks', zorder=5)
        
        # Format duration as DD:HH:MM:SS
        total_seconds = int(self.duration_seconds)
        days = total_seconds // 86400
        remaining = total_seconds % 86400
        hours = remaining // 3600
        remaining %= 3600
        minutes = remaining // 60
        seconds = remaining % 60
        duration_str = f"{days:02d}:{hours:02d}:{minutes:02d}:{seconds:02d}"
        
        self.ax.set_xlabel(f'Elapsed Time(s) (Total: {duration_str})')
        self.ax.set_ylabel('Value (Memory MB / Scaled CPU)')
        self.ax.grid(True, alpha=0.3)
        self.legend = self.ax.legend(loc='upper left')
        
        # Text annotation for center time
        self.center_time_label = self.ax.text(0.5, 0.02, "", transform=self.ax.transAxes, 
                                            ha='center', va='bottom', fontsize=10, 
                                            bbox=dict(facecolor='white', alpha=0.7, edgecolor='none'), visible=False) # Hiding per user request
                                            
        # Average Value Indicators on Right Y-Axis
        # CPU Average
        avg_cpu_scaled = self.avg_cpu * cpu_scale_factor
        self.ax.text(1.01, avg_cpu_scaled, f"Avg CPU: {self.avg_cpu:.2f}%", transform=self.ax.get_yaxis_transform(),
                    color='blue', fontsize=8, va='center', ha='left',
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='blue', alpha=0.8))
                    
        # Memory Average
        self.ax.text(1.01, self.avg_mem, f"Avg Mem: {self.avg_mem:.0f}MB", transform=self.ax.get_yaxis_transform(),
                    color='green', fontsize=8, va='center', ha='left',
                    bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='green', alpha=0.8))
        
        # Setup Hover
        self.hover_line, = self.ax.plot([0, 0], [0, 1], color='gray', linestyle='--', linewidth=1, alpha=0.7, visible=False)
        self.cpu_label = self.ax.text(0, 0, '', fontsize=9, bbox=dict(boxstyle='round,pad=0.3', facecolor='lightblue', alpha=0.8), visible=False)
        self.memory_label = self.ax.text(0, 0, '', fontsize=9, bbox=dict(boxstyle='round,pad=0.3', facecolor='lightgreen', alpha=0.8), visible=False)
        self.time_label = self.ax.text(0, 0, '', fontsize=9, bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor='red', linewidth=1.5, alpha=0.9), visible=False)
        
        self.canvas = FigureCanvasTkAgg(self.fig, self.root)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        
        # Toolbar
        toolbar = NavigationToolbar2Tk(self.canvas, self.root)
        toolbar.update()
        
        # Event Connections
        self.canvas.mpl_connect('scroll_event', self.on_scroll)
        self.canvas.mpl_connect('motion_notify_event', self.on_mouse_move)
        
        # Key bindings for navigation
        self.root.bind('<Left>', lambda e: self.move_cursor(-1))
        self.root.bind('<Right>', lambda e: self.move_cursor(1))
        
        self.update_center_label()
        
        self.root.mainloop()

    def update_center_label(self):
        """Update the center timestamp label based on current view."""
        xlim = self.ax.get_xlim()
        center_seconds = (xlim[0] + xlim[1]) / 2
        
        # Limit to data bounds
        if self.timestamps:
            center_seconds = max(min(center_seconds, self.timestamps[-1]), self.timestamps[0])
            
        current_dt = self.start_datetime + timedelta(seconds=center_seconds)
        time_str = current_dt.strftime("%Y/%m/%d, %H:%M:%S")
        self.center_time_label.set_text(time_str)

    def move_cursor(self, step):
        """Move the cursor indicator by 'step' records."""
        if not self.timestamps:
            return
            
        new_idx = self.current_index + step
        new_idx = max(0, min(new_idx, len(self.timestamps) - 1))
        
        if new_idx != self.current_index:
            self.current_index = new_idx
            self.draw_cursor_at_index(self.current_index)
            
            # Auto-pan if cursor goes out of view
            t = self.timestamps[self.current_index]
            xlim = self.ax.get_xlim()
            if t < xlim[0] or t > xlim[1]:
                # Center view on cursor
                window_width = xlim[1] - xlim[0]
                self.ax.set_xlim(t - window_width/2, t + window_width/2)
                self.canvas.draw_idle()

    def draw_cursor_at_index(self, idx):
        """Draw the cursor indicators at specific data index."""
        if idx < 0 or idx >= len(self.timestamps):
            return
            
        t = self.timestamps[idx]
        cpu = self.cpu_data[idx]
        mem = self.memory_data[idx]
        
        ylim = self.ax.get_ylim()
        
        self.hover_line.set_data([t, t], [ylim[0], ylim[1]])
        self.hover_line.set_visible(True)
        
        cpu_scale = 20.0
        self.cpu_label.set_position((t, cpu * cpu_scale))
        self.cpu_label.set_text(f"CPU: {cpu:.2f}%")
        self.cpu_label.set_visible(True)
        self.cpu_label.set_zorder(10)
        
        self.memory_label.set_position((t, mem))
        self.memory_label.set_text(f"Mem: {mem:.2f}MB")
        self.memory_label.set_visible(True)
        self.memory_label.set_zorder(10)
        
        # Timestamp label floating on the indicator line
        current_dt = self.start_datetime + timedelta(seconds=t)
        time_str = current_dt.strftime("%Y/%m/%d, %H:%M:%S")
        
        mid_y = (ylim[0] + ylim[1]) / 2
        self.time_label.set_position((t, mid_y))
        self.time_label.set_text(time_str)
        self.time_label.set_visible(True)
        self.time_label.set_zorder(10)
        
        self.canvas.draw_idle()

    def on_scroll(self, event):
        """Zoom on scroll."""
        if event.inaxes != self.ax: return
        
        xlim = self.ax.get_xlim()
        x_range = xlim[1] - xlim[0]
        zoom_factor = 1.1 if event.button == 'up' else 1/1.1
        
        new_range = x_range * zoom_factor
        center = event.xdata if event.xdata else (xlim[0] + xlim[1]) / 2
        
        left = center - new_range / 2
        right = center + new_range / 2
        
        self.ax.set_xlim(left, right)
        self.update_center_label()
        self.canvas.draw_idle()

    def on_mouse_move(self, event):
        """Handle hover effects: Crosshair and Legend movement."""
        if event.inaxes != self.ax:
            self.hover_line.set_visible(False)
            self.cpu_label.set_visible(False)
            self.memory_label.set_visible(False)
            self.time_label.set_visible(False)
            self.update_center_label() # Reset to center time
            self.canvas.draw_idle()
            return
            
        x_pos = event.xdata
        
        # Legend logic
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()
        
        range_x = xlim[1] - xlim[0]
        range_y = ylim[1] - ylim[0]
        
        if range_x > 0 and range_y > 0:
            rel_x = (x_pos - xlim[0]) / range_x
            rel_y = (event.ydata - ylim[0]) / range_y
            
            # If in top-left corner
            if rel_x < 0.2 and rel_y > 0.8:
                self.legend.set_loc('upper right')
            elif rel_x > 0.8 and rel_y > 0.8:
                self.legend.set_loc('upper left')
            # Else keep current
                     
        # Find closest index
        # Find closest index
        idx = self._find_nearest_index(self.timestamps, x_pos)
        if idx is not None:
             self.current_index = idx # Sync cursor state
             self.draw_cursor_at_index(idx)

    def _find_nearest_index(self, array, value):
        if not array: return None
        import bisect
        idx = bisect.bisect_left(array, value)
        if idx == 0: return 0
        if idx == len(array): return len(array) - 1
        before = array[idx - 1]
        after = array[idx]
        if after - value < value - before:
            return idx
        return idx - 1


def parse_arguments():
    """Parse and validate command-line arguments."""
    parser = argparse.ArgumentParser(
        description='jastm - Just Another Soak Testing Monitor',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Data Collection Mode
  %(prog)s
  %(prog)s --process-name "python.exe"
  %(prog)s --program myapp.exe
  
  # Analysis Mode
  %(prog)s --parse-file 20231025_100000_monitor.csv --summary
  %(prog)s --parse-file 20231025_100000_monitor.csv --metrices-window
        """
    )
    
    # Analysis Arguments
    parser.add_argument('--parse-file', type=str,
                           help='Input CSV file for analysis (switches to Analysis Mode)')
    parser.add_argument('--aggregate-summaries', metavar='CSV', nargs='+',
                       help='Aggregate summary table from multiple CSV log files (Analysis Mode only)')
    
    # Collection Arguments (Process identification)
    # Mutually exclusive group for process selection
    process_group = parser.add_mutually_exclusive_group(required=False)
    process_group.add_argument('--process-name', type=str,
                               help='Name of the process to monitor')
    process_group.add_argument('--process-id', type=int,
                               help='PID of the process to monitor')
    process_group.add_argument('--program', type=str, nargs=argparse.REMAINDER,
                               help='Program command to launch and monitor (command and arguments)')
    
    # General Options
    parser.add_argument('--sample-rate', type=float,
                       help=f'Sampling interval in seconds (default: {DEFAULT_SAMPLE_RATE}, Collection Mode only)')
    parser.add_argument('--machine-id', type=str,
                       help='4-digit machine identifier; default is derived from NIC MAC address')
    parser.add_argument('--config-file', type=str,
                       help='Path to YAML config file providing default option values')
    
    # Analysis Options
    parser.add_argument('--summary', action='store_true',
                       help='Show summary report (Analysis Mode only)')
    parser.add_argument('--metrices-window', action='store_true',
                       help='Open visualization tool (Analysis Mode only)')
    parser.add_argument('--cpu-peak-percentage', type=float,
                       help=f'Threshold percentage above average for CPU Peak detection (default: {DEFAULT_CPU_PEAK_PERCENTAGE})')
    parser.add_argument('--ram-peak-percentage', type=float,
                       help=f'Threshold percentage below average for RAM Peak detection (0-100, default: {DEFAULT_RAM_PEAK_PERCENTAGE})')
    
    args = parser.parse_args()
    
    if (args.parse_file or args.aggregate_summaries) and (args.process_name or args.process_id or args.program):
        parser.error("Cannot specify process/program when in Analysis Mode (--parse-file or --aggregate-summaries)")
    if args.parse_file and args.aggregate_summaries:
        parser.error("Cannot use --parse-file and --aggregate-summaries together")
        
    return args


def _load_config_file(path: Optional[str]) -> Optional[dict]:
    """Load YAML configuration file if provided."""
    if not path:
        return None
    if yaml is None:
        print("Error: PyYAML is required to use --config-file. Install it with: pip install pyyaml", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(path):
        print(f"Error: Config file not found: {path}", file=sys.stderr)
        sys.exit(1)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            print("Error: Config file must contain a YAML mapping at the top level.", file=sys.stderr)
            sys.exit(1)
        return data
    except Exception as e:
        print(f"Error: Failed to read config file {path}: {e}", file=sys.stderr)
        sys.exit(1)


def _get_config_option(config: Optional[dict], section: str, key: str):
    """Retrieve option value from config, supporting either plain values or {value, ...} mappings."""
    if not config:
        return None
    section_data = config.get(section)
    if not isinstance(section_data, dict):
        return None
    raw = section_data.get(key)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


def _derive_default_machine_id() -> str:
    """Derive a 4-digit machine ID from the NIC MAC address."""
    try:
        mac_int = uuid.getnode()
        if isinstance(mac_int, int):
            return f"{mac_int % 10000:04d}"
    except Exception:
        pass
    try:
        host = socket.gethostname()
        return f"{abs(hash(host)) % 10000:04d}"
    except Exception:
        return "0000"


def _infer_machine_id_from_path(path: str, default_machine_id: str) -> str:
    """
    Infer a 4-digit machine ID from the CSV filename.
    Falls back to default_machine_id if no suitable token is found.
    """
    base = os.path.basename(path)
    # Look for a standalone 4-digit sequence in the filename
    match = re.search(r'\b(\d{4})\b', base)
    if match:
        return match.group(1)
    return default_machine_id


def _format_duration_days_hours(duration_seconds: float) -> str:
    """Format duration in 'Xd Yh' using whole days and hours."""
    total_seconds = int(duration_seconds)
    days = total_seconds // 86400
    remaining = total_seconds % 86400
    hours = remaining // 3600
    return f"{days}d {hours}h"


def aggregate_summaries(filepaths, cpu_peak_criteria: float, ram_peak_criteria: float, default_machine_id: str) -> None:
    """
    Aggregate multiple CSV logs into a single markdown table for human review.

    Columns: machine_id, start_time, duration(days and hours),
    cpu_avg_%, cpu_peak_count, mem_avg, mem_peak_count, flags.
    """
    rows = []
    for path in filepaths:
        analyzer = DataAnalyzer(path, cpu_peak_criteria=cpu_peak_criteria, ram_peak_criteria=ram_peak_criteria)
        if not analyzer.load_data():
            print(f"Warning: Skipping file due to load error: {path}", file=sys.stderr)
            continue

        machine_id = _infer_machine_id_from_path(path, default_machine_id)

        if analyzer.timestamps:
            start_dt = analyzer.start_datetime + timedelta(seconds=analyzer.timestamps[0])
        else:
            start_dt = analyzer.start_datetime
        start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")

        duration_label = _format_duration_days_hours(analyzer.duration_seconds)

        cpu_avg = analyzer.avg_cpu
        cpu_peak_count = len(analyzer.cpu_peaks)
        mem_avg = analyzer.avg_mem
        mem_peak_count = len(analyzer.memory_peaks)

        flags = []
        if cpu_peak_count > 0:
            flags.append("CPU_PEAKS")
        if mem_peak_count > 0:
            flags.append("MEM_PEAKS")
        flags_str = ",".join(flags)

        rows.append({
            "machine_id": machine_id,
            "start_time": start_str,
            "duration": duration_label,
            "cpu_avg": cpu_avg,
            "cpu_peak_count": cpu_peak_count,
            "mem_avg": mem_avg,
            "mem_peak_count": mem_peak_count,
            "flags": flags_str,
            "source": path,
        })

    if not rows:
        print("No valid data loaded for aggregation.")
        return

    # Stable ordering: by machine_id then start_time
    rows.sort(key=lambda r: (r["machine_id"], r["start_time"], r["source"]))

    print("\n=== Aggregated Summary Report ===")
    print("| machine_id | start_time | duration(days and hours) | cpu_avg_% | cpu_peak_count | mem_avg | mem_peak_count | flags |")
    print("| :--- | :--- | :--- | ---: | ---: | ---: | ---: | :--- |")
    for r in rows:
        print(
            f"| {r['machine_id']} | {r['start_time']} | {r['duration']} | "
            f"{r['cpu_avg']:.2f} | {r['cpu_peak_count']} | "
            f"{r['mem_avg']:.2f} | {r['mem_peak_count']} | {r['flags']} |"
        )
    print()


def _resolve_effective_options(args: argparse.Namespace, config: Optional[dict]) -> argparse.Namespace:
    """Merge CLI args with config file values and built-in defaults."""
    is_analysis_mode = bool(args.parse_file or getattr(args, "aggregate_summaries", None))
    # Analysis thresholds
    cpu_peak_percentage = args.cpu_peak_percentage
    if cpu_peak_percentage is None:
        cfg_val = _get_config_option(config, "analysis", "cpu_peak_percentage")
        cpu_peak_percentage = cfg_val if cfg_val is not None else DEFAULT_CPU_PEAK_PERCENTAGE
    ram_peak_percentage = args.ram_peak_percentage
    if ram_peak_percentage is None:
        cfg_val = _get_config_option(config, "analysis", "ram_peak_percentage")
        ram_peak_percentage = cfg_val if cfg_val is not None else DEFAULT_RAM_PEAK_PERCENTAGE
    
    # Collection-related options
    process_name = args.process_name
    process_id = args.process_id
    program = args.program
    sample_rate = args.sample_rate
    machine_id = args.machine_id
    
    if not is_analysis_mode:
        # Only fall back to config when CLI did not select a process/program
        if process_name is None and process_id is None and program is None:
            cfg_proc_name = _get_config_option(config, "collection", "process_name")
            cfg_program = _get_config_option(config, "collection", "program")
            count = sum(v is not None for v in (cfg_proc_name, cfg_program))
            if count > 1:
                print("Error: Config file must not specify more than one of collection.process_name or collection.program.", file=sys.stderr)
                sys.exit(1)
            if cfg_proc_name is not None:
                process_name = str(cfg_proc_name)
            elif cfg_program is not None:
                if not isinstance(cfg_program, list):
                    print("Error: 'collection.program.value' in config must be a YAML list of command and arguments.", file=sys.stderr)
                    sys.exit(1)
                program = [str(p) for p in cfg_program]
        
        if sample_rate is None:
            cfg_rate = _get_config_option(config, "collection", "sample_rate")
            sample_rate = cfg_rate if cfg_rate is not None else DEFAULT_SAMPLE_RATE
        if machine_id is None:
            cfg_machine_id = _get_config_option(config, "collection", "machine_id")
            if cfg_machine_id is not None:
                machine_id = str(cfg_machine_id)
            else:
                machine_id = _derive_default_machine_id()
    else:
        # Analysis mode: collection sample-rate is not used, but keep a sane value
        if sample_rate is None:
            sample_rate = DEFAULT_SAMPLE_RATE
    if machine_id is None:
        # Ensure we always have a concrete machine_id value
        cfg_machine_id = _get_config_option(config, "collection", "machine_id")
        if cfg_machine_id is not None:
            machine_id = str(cfg_machine_id)
        else:
            machine_id = _derive_default_machine_id()
    
    merged = argparse.Namespace(**vars(args))
    merged.process_name = process_name
    merged.process_id = process_id
    merged.program = program
    merged.sample_rate = sample_rate
    merged.cpu_peak_percentage = cpu_peak_percentage
    merged.ram_peak_percentage = ram_peak_percentage
    merged.machine_id = machine_id
    return merged


def main():
    """Main entry point."""
    args = parse_arguments()
    
    # Merge config file with CLI options
    config_data = _load_config_file(getattr(args, "config_file", None))
    args = _resolve_effective_options(args, config_data)
    
    # Validate effective sample rate (only relevant for collection mode, but harmless elsewhere)
    if args.sample_rate is not None and args.sample_rate <= 0:
        print("Error: --sample-rate must be a positive number", file=sys.stderr)
        sys.exit(2)
    
    # Analysis Mode - single file
    if args.parse_file:
        # Convert percentages to ratios
        cpu_peak_ratio = args.cpu_peak_percentage / 100.0
        ram_peak_ratio = args.ram_peak_percentage / 100.0
        analyzer = DataAnalyzer(args.parse_file, cpu_peak_criteria=cpu_peak_ratio, ram_peak_criteria=ram_peak_ratio)
        if not analyzer.load_data():
            sys.exit(1)
            
        if args.summary:
            analyzer.show_summary()
            
        if args.metrices_window:
            analyzer.show_metrics_window()
            
        if not args.summary and not args.metrices_window:
            print("Analysis mode selected but no action specified. Use --summary or --metrices-window.")
        
        return

    # Analysis Mode - aggregate multiple CSV logs
    if getattr(args, "aggregate_summaries", None):
        cpu_peak_ratio = args.cpu_peak_percentage / 100.0
        ram_peak_ratio = args.ram_peak_percentage / 100.0
        aggregate_summaries(
            args.aggregate_summaries,
            cpu_peak_criteria=cpu_peak_ratio,
            ram_peak_criteria=ram_peak_ratio,
            default_machine_id=args.machine_id,
        )
        return

    # Data Collection Mode
    # Machine ID is resolved via CLI/config or derived from NIC MAC address
    if args.machine_id:
        print(f"Machine ID: {args.machine_id}")
    # Handle --program option: launch the program and get its process name
    launched_process = None
    process_name = args.process_name
    process_id = args.process_id
    
    if args.program is not None:
        if not args.program:
            print("Error: --program requires a command to execute.", file=sys.stderr)
            sys.exit(1)
        
        try:
            # Launch the program
            # Don't redirect stdout/stderr for GUI applications to prevent blocking
            # Set working directory to the executable's directory for resource loading
            program_path = args.program[0] if args.program else None
            cwd = os.path.dirname(os.path.abspath(program_path)) if program_path else None
            
            # Use Windows-specific flags to ensure GUI window appears
            creation_flags = 0
            if sys.platform == 'win32':
                # CREATE_NEW_PROCESS_GROUP allows the process to run independently
                # and ensures GUI windows are visible
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
            
            launched_process = subprocess.Popen(
                args.program,
                cwd=cwd,
                creationflags=creation_flags,
                # stdin=subprocess.DEVNULL, # Keep stdin/out for now unless requested otherwise
                # stdout=subprocess.DEVNULL,
                # stderr=subprocess.DEVNULL
            )
            
            # Wait a bit for the process to initialize
            time.sleep(3.0)
            
            if launched_process.poll() is not None:
                print(f"Error: Program exited immediately with code {launched_process.returncode}.", file=sys.stderr)
                sys.exit(1)
            
            process_id = launched_process.pid
            
            # Infer process name from program path for logging purposes
            if not process_name and program_path:
                 # Use stem of the filename (no extension) per user spec
                 process_name = os.path.splitext(os.path.basename(program_path))[0]
                 
            print(f"Launched program with PID: {process_id}")
            
        except Exception as e:
            print(f"Error launching program: {e}", file=sys.stderr)
            sys.exit(1)
    
    # Initialize Monitor (now DataCollector)
    app = DataCollector(
        process_name=process_name,
        process_id=process_id,
        sample_rate=args.sample_rate
    )
    
    app.launched_process = launched_process
    
    success = app.run()
    
    if not success:
        sys.exit(1)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(0)

# mouse_logger_gui.py
# FPS Mouse Test & Diagnosis
# Windows mouse event logger with simple GUI
# Features:
# - Default log filename includes human-readable timestamp suffix
# - Highlights wheel events "near" any button event within X ms (scroll_near_click flag)
# - CSV columns order: timestamp, x, y, dx, dy, ms_since_button_event, combat_state, scroll_near_click, event
#   Where:
#     x, y = cursor coordinates (when enabled)
#     dx = horizontal wheel delta (side scroll)
#     dy = vertical wheel delta (wheel up/down)
# - "Combat" heuristic
# - Toggle to include/exclude coordinates in log
# - Persists last settings to a JSON file in the user home folder, or APPDATA if on Windows
# - On Stop, prepares a new timestamped filename to avoid overwriting next session
#
# Requires: Python 3.9+ and pynput
# Build EXE: see build.bat

import queue
import time
from collections import deque
from datetime import datetime, timedelta
import csv
import os
import json
import tkinter as tk
import platform
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import ttk
from pynput import mouse

# --- App constants & settings path -----------------------------------------
APP_NAME = "FPS Mouse Test & Diagnosis"
DEFAULT_BASENAME = "mouse_events"


if platform.system() == "Windows":
    base_dir = os.getenv("APPDATA") or os.path.expanduser("~")
    settings_name = "fps_mouse_test_settings.json"   # no leading dot on Windows
else:
    base_dir = os.path.expanduser("~")
    settings_name = ".fps_mouse_test_settings.json"  # Linux/macOS style

SETTINGS_FILE = str(Path(base_dir) / settings_name)

# --- Defaults & thresholds --------------------------------------------------
NEAR_CLICK_MS_DEFAULT = 80     # window to consider a wheel tick "near" a button event
COMBAT_WINDOW_MS = 1000        # rolling window (ms) for CPS
COMBAT_CPS_DEFAULT = 2.0       # LMB-downs per second to call it "combat"
COORDS_ENABLED_DEFAULT = False # default off

# --- CSV schema -------------------------------------------------------------
HEADER = ["timestamp","ms_since_start","x","y","dx","dy","ms_since_button_event","combat_state","scroll_near_click","event",]

# --- Core: capture and logging backend -------------------------------------
class LoggerCore:
    def __init__(self, filepath, event_queue, near_click_ms, combat_cps, coords_enabled, ms_since_start=None):
        self.filepath = filepath
        self.event_queue = event_queue
        self.listener = None
        self._f = None
        self._csv = None
        self._last_flush = time.time()
        self._near_click_ms = int(near_click_ms)
        self._combat_cps = float(combat_cps)
        self._coords_enabled = bool(coords_enabled)
        self.start_ms = None

        self._last_btn_ts_ms = None
        self._lmb_down_times = deque()  # LMB downs only

    def _ts_iso(self):
        return datetime.now().isoformat(timespec='milliseconds')

    def _ts_ms(self):
        return int(time.time() * 1000)

    def _on_click(self, x, y, button, pressed):
    # Handle any mouse button press/release; track LMB downs for CPS
        evt = f"{button.name}{'Down' if pressed else 'Up'}"
        now_ms = self._ts_ms()
        ms_since_start = 0 if self.start_ms is None else (now_ms - self.start_ms)  # Ya missed this!

        if button.name == "left" and pressed:
            self._record_lmb_down(now_ms)

        self._last_btn_ts_ms = now_ms
        ms_since_button_event = 0
        scroll_near_click = 0
        combat_state = self._combat_state(now_ms)

        if not self._coords_enabled:
            x = y = dx = dy = ""
        else:
            dx = dy = 0

        self._write_row(self._ts_iso(), ms_since_start, x, y, dx, dy, evt, ms_since_button_event, scroll_near_click, combat_state)

    def _on_scroll(self, x, y, dx, dy):
    # Handle wheel scroll; mark as near-click when close in time to a button event
        evt = "WheelUp" if dy > 0 else "WheelDown" if dy < 0 else "Wheel"
        now_ms = self._ts_ms()
        ms_since_start = 0 if self.start_ms is None else (now_ms - self.start_ms)
        ms_since_button_event = 0 if self._last_btn_ts_ms is None else now_ms - self._last_btn_ts_ms
        scroll_near_click = 1 if (self._last_btn_ts_ms is not None and 0 <= ms_since_button_event <= self._near_click_ms) else 0
        combat_state = self._combat_state(now_ms)

        if not self._coords_enabled:
            x = y = dx = dy = ""

        self._write_row(self._ts_iso(), ms_since_start, x, y, dx, dy, evt, ms_since_button_event, scroll_near_click, combat_state)

    def _record_lmb_down(self, now_ms):
    # Keep only recent LMB-down timestamps inside the rolling window
        cutoff = now_ms - COMBAT_WINDOW_MS
        self._lmb_down_times.append(now_ms)
        while self._lmb_down_times and self._lmb_down_times[0] < cutoff:
            self._lmb_down_times.popleft()

    def _combat_state(self, now_ms):
    # Compute CPS and return "combat" if >= threshold, else "idle"
        cutoff = now_ms - COMBAT_WINDOW_MS
        while self._lmb_down_times and self._lmb_down_times[0] < cutoff:
            self._lmb_down_times.popleft()
        cps = len(self._lmb_down_times) / (COMBAT_WINDOW_MS / 1000.0)
        return "combat" if cps >= self._combat_cps else "idle"

    def _write_row(self, ts_iso, ms_since_start, x, y, dx, dy, event, ms_since_button_event, scroll_near_click, combat_state):
        # Append a CSV row and forward a tuple to the UI queue
        if not self._csv:
            return
        # This needs to match the HEADER constant or users get confused and sad.
        self._csv.writerow([ts_iso, ms_since_start, x, y, dx, dy, ms_since_button_event, combat_state, scroll_near_click, event])
        try:
            self.event_queue.put_nowait((ts_iso, ms_since_start, x, y, dx, dy, event, ms_since_button_event, scroll_near_click, combat_state))
        except queue.Full:
            pass
        t = time.time()
        if t - self._last_flush > 0.5:
            try:
                self._f.flush()
                os.fsync(self._f.fileno())
            except Exception:
                pass
            self._last_flush = t

    def start(self):
    # Open CSV (write header if new) and start the pynput listener
        if self.listener:
            return
        new_file = not os.path.exists(self.filepath) or os.path.getsize(self.filepath) == 0
        self._f = open(self.filepath, "a", newline="", encoding="utf-8")
        self._csv = csv.writer(self._f)
        if new_file:
            self._csv.writerow(HEADER)
            self._f.flush()
            
        self.start_ms = self._ts_ms()    
        
        self.listener = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        self.listener.start()

    def stop(self):
    # Stop listener and flush/close the CSV file
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
            self.listener = None
        if self._f:
            try:
                self._f.flush()
                os.fsync(self._f.fileno())
            except Exception:
                pass
            self._f.close()
            self._f = None
            self._csv = None

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("780x560")
        self.resizable(True, True)

        self.event_queue = queue.Queue(maxsize=2000)
        self.logger = None
        self.is_logging = False

        settings = self.load_settings()

        ts_suffix = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        default_name = f"{DEFAULT_BASENAME}_{ts_suffix}.csv"
        initial_dir = settings.get("last_log_dir") or os.getcwd()
        default_path = os.path.join(initial_dir, default_name)

        self.near_click_ms = tk.IntVar(value=settings.get("near_click_ms", NEAR_CLICK_MS_DEFAULT))
        self.combat_cps = tk.DoubleVar(value=settings.get("combat_cps", COMBAT_CPS_DEFAULT))
        self.coords_enabled = tk.BooleanVar(value=settings.get("coords_enabled", COORDS_ENABLED_DEFAULT))

        self.cps_display = tk.StringVar(value="CPS: 0.0")
        self.file_var = tk.StringVar(value=default_path)
        self.status_var = tk.StringVar(value="Idle")
        self.time_since_logging_started = tk.StringVar(value="0")
        self.count_var = tk.StringVar(value="0")

        self._build_ui()

        self.log_box.tag_configure("near", foreground="red")
        self.log_box.tag_configure("normal", foreground="black")
        self.log_box.tag_configure("combat", background="#f2dede")

        self.bind("<<PollQueue>>", self.on_poll_queue)
        self.after(100, self.poll_queue)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.event_count = 0

    def load_settings(self):
    # Read JSON settings from SETTINGS_FILE (if present)
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def save_settings(self):
    # Persist user-adjustable options and last log directory
        try:
            data = {
                "near_click_ms": int(self.near_click_ms.get()),
                "combat_cps": float(self.combat_cps.get()),
                "coords_enabled": bool(self.coords_enabled.get()),
                "last_log_dir": os.path.dirname(self.file_var.get().strip()) if self.file_var.get().strip() else ""
            }
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _build_ui(self):
    # Build all widgets and layout constraints
        # window sizing
        self.resizable(True, True)
        self.minsize(760, 520)

        frm = ttk.Frame(self, padding=10)
        frm.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # row 0: file selector
        ttk.Label(frm, text="Log file:").grid(row=0, column=0, sticky="w")
        self.file_entry = ttk.Entry(frm, textvariable=self.file_var)
        self.file_entry.grid(row=0, column=1, columnspan=2, sticky="we", padx=(6, 6))
        ttk.Button(frm, text="Browse...", command=self.choose_file).grid(row=0, column=3, sticky="e")

        # row 1: controls
        self.start_btn = ttk.Button(frm, text="Start", width=12, command=self.toggle_logging)
        self.start_btn.grid(row=1, column=0, pady=(10, 5), sticky="w")
        self.stop_btn = ttk.Button(frm, text="Stop", width=12, state="disabled", command=self.toggle_logging)
        self.stop_btn.grid(row=1, column=1, pady=(10, 5), sticky="w")

        # row 2: status
        ttk.Label(frm, text="Status:").grid(row=2, column=0, sticky="w")
        ttk.Label(frm, textvariable=self.status_var).grid(row=2, column=1, sticky="w")
        ttk.Label(frm, text="Events:").grid(row=2, column=2, sticky="e")
        ttk.Label(frm, textvariable=self.count_var).grid(row=2, column=3, sticky="w")
        ttk.Label(frm, text="Running for:").grid(row=1, column=3, sticky="e")
        ttk.Label(frm, textvariable=self.time_since_logging_started).grid(row=1, column=4, sticky="w")

        # row 3: near-click
        ttk.Label(frm, text="Near-click window (ms):").grid(row=3, column=0, sticky="w", pady=(8, 2))
        self.near_spin = ttk.Spinbox(
            frm, from_=20, to=500, increment=5, width=8,
            textvariable=self.near_click_ms, command=self.on_threshold_changed
        )
        self.near_spin.grid(row=3, column=1, sticky="w", pady=(8, 2))

        # row 4: combat cps
        ttk.Label(frm, text="Combat CPS threshold:").grid(row=4, column=0, sticky="w", pady=(2, 10))
        self.cps_spin = ttk.Spinbox(
            frm, from_=1.0, to=20.0, increment=0.5, width=8,
            textvariable=self.combat_cps, command=self.on_threshold_changed
        )
        self.cps_spin.grid(row=4, column=1, sticky="w", pady=(2, 10))

        # help text to the right
        help_text = ("Reference (approx): AK-47 auto ~10 shots/s (~100 ms), "
                    "Tec-9 semi ~6–7 shots/s (~150–170 ms), "
                    "Deagle accuracy reset ~450–500 ms.")
        ttk.Label(frm, text=help_text, wraplength=520, foreground="#444").grid(row=3, column=2, columnspan=2, sticky="w")

        # row 5: options
        self.coords_chk = ttk.Checkbutton(
            frm, text="Record cursor coordinates (x, y, dx, dy)",
            variable=self.coords_enabled, command=self.on_options_changed
        )
        self.coords_chk.grid(row=5, column=0, columnspan=3, sticky="w", pady=(2, 8))

        # row 7: log box
        self.log_box = tk.Text(frm, height=20, wrap="none")
        self.log_box.grid(row=7, column=0, columnspan=4, pady=(8, 0), sticky="nsew")

        # scrollbars
        yscroll = ttk.Scrollbar(frm, orient="vertical", command=self.log_box.yview)
        yscroll.grid(row=7, column=4, sticky="ns")
        self.log_box.configure(yscrollcommand=yscroll.set)

        xscroll = ttk.Scrollbar(frm, orient="horizontal", command=self.log_box.xview)
        xscroll.grid(row=8, column=0, columnspan=4, sticky="we")
        self.log_box.configure(xscrollcommand=xscroll.set)

        # layout weights
        frm.grid_columnconfigure(0, weight=0)
        frm.grid_columnconfigure(1, weight=1)   # entry grows
        frm.grid_columnconfigure(2, weight=0)
        frm.grid_columnconfigure(3, weight=0)
        frm.grid_columnconfigure(4, weight=0, minsize=14)  # keep y-scrollbar visible
        frm.grid_rowconfigure(7, weight=1)      # log row grows

    def on_threshold_changed(self):
    # When spinboxes change, update live thresholds and persist
        if self.logger:
            self.logger._near_click_ms = int(self.near_click_ms.get())
            self.logger._combat_cps = float(self.combat_cps.get())
        self.save_settings()

    def on_options_changed(self):
    # Toggle coordinate logging on/off and persist
        if self.logger:
            self.logger._coords_enabled = bool(self.coords_enabled.get())
        self.save_settings()

    def choose_file(self):
    # Choose/override the CSV output path (disabled while logging)
        path = filedialog.asksaveasfilename(
            title="Select log file",
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            initialfile=os.path.basename(self.file_var.get()),
            initialdir=os.path.dirname(self.file_var.get()) if self.file_var.get() else None
        )
        if path:
            if self.is_logging:
                messagebox.showwarning("Busy", "Stop logging before changing the file.")
                return
            self.file_var.set(path)
            self.save_settings()

    def _new_timestamped_path(self):
    # Generate a fresh timestamped filename in the current directory
        ts_suffix = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dirpath = os.path.dirname(self.file_var.get().strip()) or os.getcwd()
        new_name = f"{DEFAULT_BASENAME}_{ts_suffix}.csv"
        return os.path.join(dirpath, new_name)

    def toggle_logging(self):
    # Start/Stop logging and update UI state; rotate filename on stop
        if not self.is_logging:
            path = self.file_var.get().strip()
            if not path:
                messagebox.showerror("Error", "Select a log file path.")
                return
            try:
                self.logger = LoggerCore(path, self.event_queue,
                                         near_click_ms=self.near_click_ms.get(),
                                         combat_cps=self.combat_cps.get(),
                                         coords_enabled=self.coords_enabled.get())
                self.logger.start()
                self.is_logging = True
                self.status_var.set("Logging")
                self.start_btn.config(state="disabled")
                self.stop_btn.config(state="normal")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to start logging:\n{e}")
                self.logger = None
        else:
            try:
                if self.logger:
                    self.logger.stop()
                self.is_logging = False
                self.status_var.set("Idle")
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                self.file_var.set(self._new_timestamped_path())  # new name to avoid overwrite
                self.save_settings()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to stop logging:\n{e}")

    def poll_queue(self):
    # Periodic UI tick to drain event_queue without blocking the GUI loop
        self.event_generate("<<PollQueue>>", when="tail")
        self.after(100, self.poll_queue)

    def on_poll_queue(self, event=None):
    # Consume queued rows, update counters, and append ts_formatted lines to the Text box
        updated = False
        cps = 0.0
        while True:
            try:
                ts, ms_since_start, x, y, dx, dy, evt, ms_since_button_event, scroll_near_click, combat_state = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self.event_count += 1
            updated = True

            if self.logger:
                cps = len(self.logger._lmb_down_times) / (COMBAT_WINDOW_MS / 1000.0)

            combat_status_maker = ""
            tags = []
            if scroll_near_click == 1 and ("WheelUp" in evt or "WheelDown" in evt):
                combat_status_maker += "[SCROLL NEAR LMB]"
                tags.append("near")
            if combat_state == "combat":
                combat_status_maker += "[COMBAT]"
                tags.append("combat")
            if not tags:
                tags = ["normal"]

            seconds_since_start = ms_since_start // 1000
            minutes_since_start = seconds_since_start // 60
            hours_since_start = minutes_since_start // 60

            td = timedelta(milliseconds=ms_since_start)
            total_seconds = int(td.total_seconds())
            formatted_time_since_start = f"{total_seconds // 3600:02}:{(total_seconds % 3600) // 60:02}:{total_seconds % 60:02}.{int(td.microseconds/1000):03}"
            
            coord_part = f"x={x} y={y} dx={dx} dy={dy}" if self.coords_enabled.get() else ""
            ts_formatted = datetime.fromisoformat(ts).strftime("%H:%M:%S") 
            
            # If event is a SCROLL wheel event, add additional info    
            if "Wheel" in evt:
                line = f"[{ts_formatted}] Running for: {formatted_time_since_start}(HH:MM:SS) {coord_part} ms_since_button_event={ms_since_button_event} scroll_near_click={scroll_near_click} event={evt} {combat_status_maker}\n"
            else:
                line = f"[{ts_formatted}] Running for: {formatted_time_since_start}(HH:MM:SS) {coord_part} event={evt} {combat_status_maker}\n"

            self.log_box.config(state="normal")
            self.log_box.insert("end", line, tuple(tags))
            self.log_box.see("end")
            self.log_box.config(state="disabled")

        if updated:
            self.count_var.set(str(self.event_count))
            self.cps_display.set(f"CPS: {cps:.1f}")

    def on_close(self):
    # Graceful shutdown: stop listener, persist settings, close window
        try:
            if self.logger:
                self.logger.stop()
        except Exception:
            pass
        self.save_settings()
        self.destroy()

def main():
    # Entrypoint
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()

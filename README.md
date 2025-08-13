# FPS Mouse Test & Diagnosis — Detect and Diagnose Phantom Scroll Events

A global mouse event logger for Windows designed to identify unintended scrolls and clicks during FPS gameplay.
Provides detailed CSV logging and live visual feedback with configurable thresholds.

## Features
- Real-time visual feedback — near-click wheel events are highlighted in red.
- Combat context — flagged when LMB-down rate exceeds a threshold (no single-click false positives).
- Detailed CSV output — columns: `timestamp, x, y, dx, dy, ms_since_button_event, combat_state, scroll_near_click, event`.
	- x, y = cursor coordinates (when enabled)
	- dx = horizontal wheel delta (side scroll)
	- dy = vertical wheel delta (wheel up/down)
	- scroll_near_click = 1 if a wheel event occurred within the configured window of a button event, else 0
- Automatic file naming — timestamped log files for clear session tracking.
- Optional coordinate logging via checkbox (default off).
- Remembers last settings and log directory.
- Configurable thresholds — adjust near-click window and combat CPS threshold at runtime.

## Quick Start
```powershell
pip install -r requirements.txt
python .\fps_mouse_tester_and_diagnosis.py
```

1. Start the application.
2. Click **Start** to begin logging.
3. Play and reproduce the issue.
4. Watch for `[NEAR]` and `[COMBAT]` markers in the GUI; analyze the CSV for details.

## Building as an Executable (Windows ÖNLY)
```powershell
pip install pyinstaller
./build.bat
```
The executable will be at `dist/FPS_Mouse_Tester_and_Diagnosis.exe`.

## Notes
- Defaults are tuned for FPS (CS2 Specifically) contexts: near-click window = 80 ms; combat CPS = 2.0 (LMB downs/sec).
- No detection for long LMB holds (30 bullet spray) because if your scroll is broken like mine you will be dead by bullet 12 anyway, and adding it to a TODO that realistically will never be done is just silly.
- Reference fire rates (approximate, for context only): AK‑47 auto ≈ 10 shots/s (~100 ms), Tec‑9 semi ≈ 6–7 shots/s (~150–170 ms), Desert Eagle accuracy reset ≈ 450–500 ms after a shot.

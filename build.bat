@echo off
REM Build single-file EXE (no console) using PyInstaller
REM Install: pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --noconsole --name FPS_Mouse_Tester_and_Diagnosis fps_mouse_tester_and_diagnosis.py
echo Build complete. EXE in .\dist\FPS_Mouse_Tester_and_Diagnosis.exe
pause

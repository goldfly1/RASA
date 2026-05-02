@echo off
cd /d "%~dp0"
echo Starting RASA Command Center...
.venv\Scripts\python.exe launch_gui_native.py
pause

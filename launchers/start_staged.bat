@echo off
cd /d "%~dp0.."
title RASA - Staged Launch
echo RASA Staged Launcher — starts services in dependency order
echo.
.venv\Scripts\python scripts/launch.py
pause

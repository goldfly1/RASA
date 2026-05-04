@echo off
cd /d "%~dp0.."
title RASA - Dashboard (:8401)
echo Starting RASA NiceGUI Dashboard...
echo Open http://127.0.0.1:8401 in your browser.
echo.
.venv\Scripts\python -m rasa.gui_nice
pause

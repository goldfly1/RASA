@echo off
cd /d "%~dp0.."
title RASA - API Server (:8400)
echo Starting RASA API Server...
echo API available at http://127.0.0.1:8400
echo.
.venv\Scripts\python -m rasa.gui.server
pause

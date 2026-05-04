@echo off
cd /d "%~dp0.."
title RASA - Pool Controller
echo Starting RASA Pool Controller...
echo.
.venv\Scripts\python -m rasa.pool.controller --pool-file config/pool.yaml
pause

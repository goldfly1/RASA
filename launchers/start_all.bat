@echo off
cd /d "%~dp0.."
title RASA - All Services
echo Starting all RASA services...
echo This will launch: GUI server (:8400), Dashboard (:8401), Pool Controller, Sandbox, Agents
echo.
echo Press Ctrl+C to stop all services.
echo.
.venv\Scripts\honcho start
pause

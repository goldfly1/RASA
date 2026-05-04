@echo off
cd /d "%~dp0.."
title RASA - Run Tests
echo Running RASA test suite...
echo.
.venv\Scripts\pytest tests/ -v
pause

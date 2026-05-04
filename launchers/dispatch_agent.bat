@echo off
cd /d "%~dp0.."
title RASA - Agent Dispatcher
echo RASA Agent Dispatcher
echo.
echo Usage: drag a soul file onto this, or edit the path below
echo.
set /p SOUL=Enter soul name (e.g. coder-v2-dev):
set /p GOAL=Enter goal/instruction:
.venv\Scripts\python -m rasa.agent.dispatcher --soul %SOUL% --goal "%GOAL%"
pause

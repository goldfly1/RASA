@echo off
cd /d "%~dp0.."
title RASA - Ingest Lore
echo Ingesting all project documentation into lore store...
echo.
.venv\Scripts\python scripts/ingest_lore.py --all --embed --embed-model nomic-embed-text
pause

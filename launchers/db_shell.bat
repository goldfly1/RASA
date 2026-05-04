@echo off
cd /d "%~dp0.."
title RASA - DB Shell
echo Connecting to RASA databases...
echo.
echo Databases: rasa_orch, rasa_pool, rasa_policy, rasa_memory, rasa_eval, rasa_recovery
echo.
set PGPASSWORD=8764
echo --- rasa_orch ---
C:\"Program Files"\PostgreSQL\16\bin\psql.exe -U postgres -d rasa_orch
pause

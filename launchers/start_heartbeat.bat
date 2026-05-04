@echo off
cd /d "%~dp0.."
title RASA - Heartbeat Monitor
echo RASA Heartbeat Monitor — checking every 30s, restarting dead services
echo.
echo Log: logs\heartbeat.log
echo.
.venv\Scripts\python scripts/heartbeat_monitor.py --loop --interval 30
pause

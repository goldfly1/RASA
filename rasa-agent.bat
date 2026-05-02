@echo off
REM RASA agent dispatcher — one-liner to talk to any agent
REM Usage: rasa-agent --soul coder-v2-dev --goal "your goal"
set RASA_DB_PASSWORD=8764
"%~dp0.venv\Scripts\rasa-agent" %*

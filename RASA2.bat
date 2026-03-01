@echo off
title RASA Gitignore Setup
echo.
echo ==========================================
echo  RASA Section 1.3: Security Baseline
echo ==========================================
echo

set "RASA_ROOT=%USERPROFILE%\rasa"

echo [INFO] Target Directory: %RASA_ROOT%
echo

if not exist "%RASA_ROOT%" (
    echo [ERROR] RASA directory not found!
    echo [INFO] Run Section 1.1 (Directory Setup) first.
    goto :END
)

echo [ACTION] Creating .gitignore...

(
echo # RASA Security Baseline
echo # Generated: %DATE% %TIME%
echo.
echo # Secrets and Credentials
echo .env
echo .env.local
echo .env.*.local
echo *.pem
echo *.key
echo.
echo # Logs
echo *.log
echo logs/
echo.
echo # Workflow Exports (may contain credentials)
echo *.json
echo !workflows/template-*.json
echo.
echo # Database and Data
echo data/
echo *.db
echo *.sqlite
echo.
echo # System Files
echo .DS_Store
echo Thumbs.db
echo.
echo # Node.js
echo node_modules/
echo npm-debug.log
echo.
echo # PostgreSQL
echo *.sql.gz
echo dump_*.sql
) > "%RASA_ROOT%\.gitignore"

echo [OK] .gitignore created successfully.
echo.

echo [VERIFY] .gitignore contents:
echo ------------------------------------------
type "%RASA_ROOT%\.gitignore"
echo ------------------------------------------
echo.

cd /d "%RASA_ROOT%"

if not exist ".git" (
    echo [INFO] No Git repo found. Initializing...
    git init
    echo [OK] Git repository initialized.
) else (
    echo [OK] Git repository already exists.
)
echo.

echo [VERIFY] Git status:
git status --short
echo.

echo ==========================================
echo  Section 1.3 COMPLETE
echo ==========================================
echo.

:END
pause

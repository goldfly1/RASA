@'
@echo off
title RASA Directory Setup
echo.
echo ==========================================
echo  RASA Phase 0-1: Directory Setup
echo ==========================================
echo.

set "RASA_ROOT=%USERPROFILE%\rasa"

echo [INFO] Target Directory: %RASA_ROOT%
echo.
echo [ACTION] Creating directory structure...

mkdir "%RASA_ROOT%" 2>nul
mkdir "%RASA_ROOT%\schema" 2>nul
mkdir "%RASA_ROOT%\workflows" 2>nul
mkdir "%RASA_ROOT%\logs" 2>nul
mkdir "%RASA_ROOT%\data" 2>nul
mkdir "%RASA_ROOT%\scripts" 2>nul

echo [OK] Directories created.
echo.
echo [ACTION] Securing directory...

icacls "%RASA_ROOT%" /inheritance:r /grant:r "%USERNAME%:(F)" >nul 2>&1

echo [OK] Permissions secured.
echo.
echo [VERIFY] Listing structure...
dir /b "%RASA_ROOT%"
echo.
echo ==========================================
echo  Section 1.1 COMPLETE
echo ==========================================
pause
'@ | OutFile -FilePath "setup-dirs.bat" -Encoding ASCII

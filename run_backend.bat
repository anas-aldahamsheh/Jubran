@echo off
title Jubran Backend (FastAPI)
cd /d "%~dp0"
echo ==============================================
echo   Starting Jubran Backend on :8001 ...
echo ==============================================
rem Local run: development mode unless .env says otherwise (production refuses unsafe settings).
findstr /B /C:"ENVIRONMENT=" .env >nul 2>&1 || set ENVIRONMENT=development
rem --reload restarts the server when code changes: for development only. On a real server use the
rem Docker image (docker compose --profile app up -d --build) or run uvicorn without --reload.
rem New dependencies (e.g. tzdata) are installed once, the first time they are missing.
services\api\.venv\Scripts\python.exe -c "import tzdata, greenlet" >nul 2>&1 || services\api\.venv\Scripts\python.exe -m pip install -q -r services\api\requirements.txt
services\api\.venv\Scripts\python.exe -m uvicorn jubran.main:app --app-dir services/api/src --host 0.0.0.0 --port 8001 --reload
pause

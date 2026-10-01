@echo off
title Jubran - Public test link
rem Always work from the project folder (double-click, shortcut or another directory).
cd /d "%~dp0"
echo ==============================================
echo    Jubran - public test link (Tailscale)
echo ==============================================
rem Production mode with its own database and settings (.env.public); development is untouched.
rem The database container needs POSTGRES_USER/PASSWORD/DB; take them from .env (or from DATABASE_URL).
for /f "usebackq tokens=1,* delims==" %%A in (`services\api\.venv\Scripts\python.exe services\api\scripts\compose_env.py .env`) do if not defined %%A set "%%A=%%B"
docker compose up -d
services\api\.venv\Scripts\python.exe services\api\scripts\public_trial.py start %*
pause

@echo off
title Jubran Restaurant Launcher
rem Always work from the project folder (double-click, shortcut or another directory).
cd /d "%~dp0"
echo ==============================================
echo    Starting Jubran Restaurant Application
echo ==============================================
echo 1. Ensuring Database container is running...
rem The database container needs POSTGRES_USER/PASSWORD/DB; take them from .env (or from DATABASE_URL).
for /f "usebackq tokens=1,* delims==" %%A in (`services\api\.venv\Scripts\python.exe services\api\scripts\compose_env.py .env`) do if not defined %%A set "%%A=%%B"
docker compose up -d
echo 2. Launching Backend Server in a new window...
start "Jubran Backend (FastAPI)" cmd /k call "%~dp0run_backend.bat"
echo 3. Launching Frontend Server in a new window...
start "Jubran Frontend (Next.js)" cmd /k call "%~dp0run_frontend.bat"
echo.
echo ==============================================
echo  Servers are starting:
echo  - Customer Web App: http://localhost:3001
echo  - Admin Dashboard:  http://localhost:3001/admin/floor
echo  - Backend API Docs: http://localhost:8001/docs
echo ==============================================
echo.

@echo off
title Jubran Frontend (Next.js)
cd /d "%~dp0"
echo ==============================================
echo   Starting Jubran Frontend on :3001 ...
echo ==============================================
npm --prefix apps/web run dev
pause

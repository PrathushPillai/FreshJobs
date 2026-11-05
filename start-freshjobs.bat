@echo off
rem Starts FreshJobs: the backend (API + job pollers) and the frontend, each in its own window,
rem then opens the feed. Servers already running are left alone. Close a window to stop that server.
cd /d "%~dp0"

curl -s -o nul -m 3 http://localhost:8000/api/status
if errorlevel 1 (
  echo Starting the backend...
  start "FreshJobs backend" powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0scripts\run-backend.ps1"
) else (
  echo Backend already running.
)

curl -s -o nul -m 3 http://localhost:5173
if errorlevel 1 (
  echo Starting the frontend...
  start "FreshJobs frontend" cmd /k "cd /d ""%~dp0frontend"" && npm run dev -- --port 5173 --strictPort"
) else (
  echo Frontend already running.
)

echo Waiting for the site (the backend's first start after an update can take a few minutes)...
:wait
curl -s -o nul -m 3 http://localhost:8000/api/status
if errorlevel 1 (timeout /t 3 /nobreak >nul & goto wait)
curl -s -o nul -m 3 http://localhost:5173
if errorlevel 1 (timeout /t 3 /nobreak >nul & goto wait)

start "" http://localhost:5173
echo FreshJobs is running at http://localhost:5173
timeout /t 5 >nul

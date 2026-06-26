@echo off
REM chessmon local server for the WebRTC setup -- plain HTTP, NO certificate.
REM Run this on the club PC, then run peer.bat in a SECOND window.
REM Console (no cert, no warning):  http://localhost:8000/app/admin.html
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "webrtc\stop.ps1" uvicorn
echo chessmon server (plain HTTP) on port 8000 -- console: http://localhost:8000/app/admin.html
echo (from another LAN machine use this PC's IP, e.g. http://192.168.2.51:8000/app/admin.html)
.venv\Scripts\python -m uvicorn server.app:app --host 0.0.0.0 --port 8000

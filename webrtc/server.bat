@echo off
REM chessmon local server for the WebRTC setup -- plain HTTP, NO certificate.
REM Run this on the club PC, then run peer.bat in a SECOND window.
REM Console (no cert, no warning):  http://localhost:8000/app/admin.html
cd /d "%~dp0.."
echo chessmon server (plain HTTP) on http://localhost:8000   console: /app/admin.html
.venv\Scripts\python -m uvicorn server.app:app --port 8000

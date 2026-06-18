@echo off
rem Serve the chessmon clock + camera over HTTPS (needed for the camera PWA's getUserMedia).
rem Generates a self-signed cert on first run (no openssl) and prints the https://<lan-ip>:8000 URL.
C:\Claude\Projects\chessmon\.venv\Scripts\python.exe C:\Claude\Projects\chessmon\server\serve_https.py 8000
pause

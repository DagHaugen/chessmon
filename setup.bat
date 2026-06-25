@echo off
rem chessmon setup — Python, HTTPS cert, Stockfish, and (optionally) the cloud broadcast link.
rem Double-click to run, or pass flags, e.g.  setup.bat -NewCert
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause

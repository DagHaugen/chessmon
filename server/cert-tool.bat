@echo off
rem chessmon cert-tool — double-click for info; or pass args, e.g.  cert-tool.bat -Trust
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0cert-tool.ps1" %*
echo.
pause

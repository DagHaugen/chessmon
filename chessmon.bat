@echo off
rem chessmon launcher (Windows). Uses the project venv once setup has created it, else system Python.
rem   chessmon setup   |   chessmon   |   chessmon stop   |   chessmon restart   |   chessmon status
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0run.py" %*

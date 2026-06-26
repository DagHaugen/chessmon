@echo off
REM chessmon WebRTC bridge launcher. Edit RTC_ROOM to your club's code, then just run this on the
REM club PC. Keep your chessmon server running too (serve_https.py 8000, or uvicorn ... --port 8000).
cd /d "%~dp0.."
set RTC_BROKER=https://comlos.com/relay/signal.php
set RTC_ROOM=club1
set RTC_TARGET=wss://localhost:8000/ws
echo Bridging room "%RTC_ROOM%"  to  %RTC_TARGET%
.venv\Scripts\python webrtc\rtc_peer.py

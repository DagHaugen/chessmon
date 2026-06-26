@echo off
REM chessmon WebRTC bridge launcher. Edit RTC_ROOM to your club's code, then just run this on the
REM club PC. Keep your chessmon server running too (serve_https.py 8000, or uvicorn ... --port 8000).
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "webrtc\stop.ps1" rtc_peer
set RTC_BROKER=https://comlos.com/relay/signal.php
set RTC_ROOM=club1
REM ws:// for a plain-HTTP server (uvicorn server.app:app); wss:// for serve_https.py.
set RTC_TARGET=ws://localhost:8000/ws
REM The bridge auto-excludes VPN/virtual adapters now (any IP range), so leave this OFF. Manual override only:
REM set RTC_EXCLUDE=172.27
echo Bridging room "%RTC_ROOM%"  to  %RTC_TARGET%
.venv\Scripts\python webrtc\rtc_peer.py

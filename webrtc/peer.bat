@echo off
REM chessmon WebRTC bridge launcher. Just run this on the club PC, alongside the chessmon server
REM (webrtc\server.bat). The signaling room is automatic -- shared via rtc_room.txt with the server.
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "webrtc\stop.ps1" rtc_peer
set RTC_BROKER=https://comlos.com/relay/signal.php
REM RTC_ROOM is AUTO now: the bridge shares rtc_room.txt (repo root) with the server -> a unique, stable room
REM per club, no manual matching. Uncomment only to force a custom room:
REM set RTC_ROOM=club1
REM ws:// for a plain-HTTP server (uvicorn server.app:app); wss:// for serve_https.py.
set RTC_TARGET=ws://localhost:8000/ws
REM The bridge auto-excludes VPN/virtual adapters now (any IP range), so leave this OFF. Manual override only:
REM set RTC_EXCLUDE=172.27
echo Bridging to %RTC_TARGET%  (room auto from rtc_room.txt unless RTC_ROOM is set)
.venv\Scripts\python webrtc\rtc_peer.py

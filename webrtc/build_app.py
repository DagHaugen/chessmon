"""Assemble the device-facing chessmon PWA for hosting on comlos.com (WebRTC phase 4).

Copies the device clients + their assets from server/web/ into webrtc/dist/app/. Upload dist/app/ to
comlos.com/relay/app/ (next to signal.php). A device then opens

    https://comlos.com/relay/app/?rtc=1&room=<club>

loads on comlos.com's real cert (so getUserMedia works and nothing is installed), and connects over a
WebRTC data channel to the club's local server. The local side runs rtc_peer.py:

    set RTC_BROKER=https://comlos.com/relay/signal.php
    set RTC_ROOM=<club>             # must match the ?room= the console QR encodes
    set RTC_TARGET=ws://localhost:8000/ws
    chessmon-webrtc\\.venv\\Scripts\\python webrtc\\rtc_peer.py

sw.js is intentionally LEFT OUT for now (a service worker scoped to /relay/app/ needs its shell paths
reworked; the venue is online so the pages just load fresh). Add it in a phase-4b once paths are sorted.
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, "..", "server", "web"))
DIST = os.path.join(HERE, "dist", "app")

FILES = ["index.html", "clock.html", "camera.html", "rtc_transport.js",
         "manifest.webmanifest", "icon.svg", "jsqr.min.js", "qrcode.min.js"]
DIRS = ["pieces"]

if os.path.isdir(DIST):
    shutil.rmtree(DIST)
os.makedirs(DIST)

copied, missing = [], []
for f in FILES:
    s = os.path.join(SRC, f)
    if os.path.isfile(s):
        shutil.copy2(s, os.path.join(DIST, f)); copied.append(f)
    else:
        missing.append(f)
for d in DIRS:
    s = os.path.join(SRC, d)
    if os.path.isdir(s):
        shutil.copytree(s, os.path.join(DIST, d)); copied.append(d + "/")
    else:
        missing.append(d + "/")

print("assembled", DIST)
for c in copied:
    print("  +", c)
for m in missing:
    print("  ? missing:", m)
print("\nNext: upload dist/app/ to comlos.com/relay/app/, run rtc_peer.py on the club PC")
print("(RTC_ROOM=<club>, RTC_TARGET=ws://localhost:8000/ws), and have the console QR encode")
print("https://comlos.com/relay/app/?rtc=1&room=<club>")

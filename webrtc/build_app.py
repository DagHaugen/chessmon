"""Assemble the device-facing chessmon PWA for hosting on comlos.com (WebRTC phase 4).

Copies the device clients + their assets from server/web/ into webrtc/dist/app/. Upload dist/app/ to
comlos.com/relay/app/ (next to signal.php). A device then opens

    https://comlos.com/relay/app/?rtc=1&room=<club>

loads on comlos.com's real cert (so getUserMedia works and nothing is installed), and connects over a
WebRTC data channel to the club's local server. The club PC just runs the two launchers:

    webrtc\\server.bat   (the local chessmon server, plain HTTP)
    webrtc\\peer.bat     (the WebRTC bridge; the signaling room is AUTO -- shared rtc_room.txt with the server)

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
# These device pages are ALWAYS served over WebRTC from comlos.com, so bake the transport config in
# (window.CM_RTC) -> the QR/URL no longer needs ?rtc=1, just ?room=<club>. cmConnect + the old-browser
# guard both read window.CM_RTC.
RTC_PAGES = {"index.html", "clock.html", "camera.html"}
CM_RTC_TAG = '<script>window.CM_RTC={signal:"/relay/signal.php"};</script>\n'

if os.path.isdir(DIST):
    shutil.rmtree(DIST)
os.makedirs(DIST)

copied, missing = [], []
for f in FILES:
    s = os.path.join(SRC, f)
    if os.path.isfile(s):
        if f in RTC_PAGES:                                   # bake window.CM_RTC into the comlos.com copies
            html = open(s, encoding="utf-8").read().replace("</head>", CM_RTC_TAG + "</head>", 1)
            open(os.path.join(DIST, f), "w", encoding="utf-8").write(html)
        else:
            shutil.copy2(s, os.path.join(DIST, f))
        copied.append(f)
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
print("\nNext: upload dist/app/ to comlos.com/relay/app/. The club PC runs webrtc\\server.bat +")
print("webrtc\\peer.bat -- the signaling room is automatic (rtc_room.txt) and the console QR encodes it.")

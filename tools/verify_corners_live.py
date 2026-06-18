"""Corner-calibration check against the LIVE server (real WebSockets, like the real devices):
camera relays the empty frame -> clock gets the image -> clock returns corners -> board registers."""
import asyncio
import json
import sys
import urllib.request

import cv2
import websockets

BASE = "http://127.0.0.1:8788"
WS = "ws://127.0.0.1:8788/ws"


async def main():
    req = urllib.request.Request(BASE + "/tables", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req))
    tok, pair = r["tableToken"], r["pairToken"]
    frame = cv2.imread(r"C:\Claude\Projects\chessmon\out\cam_empty.png")
    jpg = cv2.imencode(".jpg", frame)[1].tobytes()

    async with websockets.connect(WS) as clock, websockets.connect(WS) as cam:
        await clock.send(json.dumps({"type": "table.join", "tableToken": tok}))
        await clock.recv(); await clock.recv()                       # session.ready, state
        await cam.send(json.dumps({"type": "pair.join", "pairToken": pair}))
        await cam.recv()                                             # session.ready

        await cam.send(json.dumps({"type": "calib", "step": "corners"}))
        await cam.send(jpg)                                          # binary empty frame
        img = json.loads(await clock.recv())
        relayed = json.loads(await cam.recv())
        assert img["type"] == "calib.image" and img["image"].startswith("data:image/jpeg"), img
        assert relayed["type"] == "calib.relayed", relayed
        print(f"relay OK -> clock calib.image ({len(img['image'])//1024} KB), camera calib.relayed", flush=True)

        corners = [[.078, .031], [.922, .043], [.932, .684], [.063, .672]]
        await clock.send(json.dumps({"type": "calib.corners", "corners": corners}))
        ok1 = json.loads(await clock.recv())
        ok2 = json.loads(await cam.recv())
        assert ok1["type"] == "calib.ok" and ok2["type"] == "calib.ok", (ok1, ok2)
        print("corners -> calib.ok to both clock & camera  LIVE CORNER-CALIB PASSED", flush=True)


asyncio.run(main())

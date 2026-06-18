"""One-step calibration over the LIVE server (real WebSockets): camera relays the SET-UP board ->
clock taps corners -> server can't auto-orient a symmetric start -> orient.ask -> clock picks White's
side -> session.baselined. Uses the real out/cam_corners.png frame."""
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
    frame = cv2.imread(r"C:\Claude\Projects\chessmon\out\cam_corners.png")   # a set-up start, 90deg-rotated
    jpg = cv2.imencode(".jpg", frame)[1].tobytes()

    async with websockets.connect(WS) as clock, websockets.connect(WS) as cam:
        await clock.send(json.dumps({"type": "table.join", "tableToken": tok}))
        await clock.recv(); await clock.recv()
        await cam.send(json.dumps({"type": "pair.join", "pairToken": pair}))
        await cam.recv()

        await cam.send(json.dumps({"type": "calib", "step": "corners"}))      # one button on the camera
        await cam.send(jpg)
        img = json.loads(await clock.recv())
        relayed = json.loads(await cam.recv())
        assert img["type"] == "calib.image" and relayed["type"] == "calib.relayed", (img, relayed)
        print("relay -> clock calib.image, camera calib.relayed  OK", flush=True)

        corners = [[.125, .090], [.875, .109], [.906, .668], [.104, .652]]    # eyeballed playing-area corners
        await clock.send(json.dumps({"type": "calib.corners", "corners": corners}))
        ask1 = json.loads(await clock.recv())
        ask2 = json.loads(await cam.recv())
        assert ask1["type"] == "orient.ask" and ask2["type"] == "orient.ask", (ask1, ask2)
        print("corners -> orient.ask to clock & camera (symmetric start)  OK", flush=True)

        await clock.send(json.dumps({"type": "orient.pick", "side": "right"}))  # White is on the right
        b1 = json.loads(await clock.recv())
        b2 = json.loads(await cam.recv())
        assert b1["type"] == "session.baselined" and b2["type"] == "session.baselined", (b1, b2)
        print(f"orient.pick right -> session.baselined (t={b1.get('t')})  ONE-STEP PASSED", flush=True)

        # clock RESTART while the camera stays connected -> continuation: no QR, resume calibrated
        async with websockets.connect(WS) as clock2:
            await clock2.send(json.dumps({"type": "table.join", "tableToken": tok}))
            ready = json.loads(await clock2.recv())
            state = json.loads(await clock2.recv())
            assert ready.get("calibrated") is True and ready.get("cameraLinked") is True, ready
            assert state.get("calibrated") is True, state
            print("clock restart -> calibrated=True cameraLinked=True + state resumes  CONTINUATION PASSED", flush=True)


asyncio.run(main())

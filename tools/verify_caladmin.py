"""Console-driven calibration over the live server: console triggers a frame, the camera answers,
the console gets the image, returns corners, and picks White's side -> session.baselined."""
import asyncio
import json
import urllib.request

import cv2
import websockets

BASE = "http://127.0.0.1:8788"
WS = "ws://127.0.0.1:8788/ws"


async def main():
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        BASE + "/tables", data=b"{}", headers={"Content-Type": "application/json"})))
    tok, pairtok = r["tableToken"], r["pairToken"]
    frame = cv2.imread(r"C:\Claude\Projects\chessmon\out\cam_corners.png")
    jpg = cv2.imencode(".jpg", frame)[1].tobytes()

    async with websockets.connect(WS) as admin, websockets.connect(WS) as cam:
        await admin.send(json.dumps({"type": "admin.join"})); await admin.recv()
        await cam.send(json.dumps({"type": "pair.join", "pairToken": pairtok})); await cam.recv()

        await admin.send(json.dumps({"type": "admin.calib", "table": tok}))   # console hits Calibrate
        assert json.loads(await cam.recv())["type"] == "capture.req"
        await cam.send(jpg)                                                    # camera answers with a frame
        assert json.loads(await cam.recv())["type"] == "calib.relayed"
        img = json.loads(await admin.recv())
        assert img["type"] == "calib.image" and img["table"] == tok, img
        print("admin.calib -> capture.req -> frame -> console calib.image  OK", flush=True)

        corners = [[.125, .090], [.875, .109], [.906, .668], [.104, .652]]
        await admin.send(json.dumps({"type": "admin.corners", "table": tok, "corners": corners}))
        ask = json.loads(await admin.recv())
        assert ask["type"] == "orient.ask" and ask["table"] == tok, ask
        print("admin.corners -> orient.ask  OK", flush=True)

        await admin.send(json.dumps({"type": "admin.orient", "table": tok, "side": "right"}))
        b = json.loads(await admin.recv())
        assert b["type"] == "session.baselined" and b["table"] == tok, b
        print(f"admin.orient right -> session.baselined (t={b.get('t')})  CONSOLE CALIB PASSED", flush=True)


asyncio.run(main())

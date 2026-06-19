"""Restart-resume check: calibrate a table on the live server, then load the persisted file into a
fresh SessionManager (= a restarted server) and confirm the session comes back calibrated."""
import asyncio
import json
import sys
import urllib.request

import cv2
import websockets

sys.path.insert(0, r"C:\Claude\Projects\chessmon")
from server.manager import SessionManager  # noqa: E402

BASE = "http://127.0.0.1:8788"
WS = "ws://127.0.0.1:8788/ws"
SESS = r"C:\Claude\Projects\chessmon\out\test_sess.pkl"


async def main():
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        BASE + "/tables", data=b"{}", headers={"Content-Type": "application/json"})))
    tok, pairtok = r["tableToken"], r["pairToken"]
    frame = cv2.imread(r"C:\Claude\Projects\chessmon\out\cam_corners.png")
    jpg = cv2.imencode(".jpg", frame)[1].tobytes()
    async with websockets.connect(WS) as admin, websockets.connect(WS) as cam:
        await admin.send(json.dumps({"type": "admin.join"})); await admin.recv()
        await cam.send(json.dumps({"type": "pair.join", "pairToken": pairtok})); await cam.recv()
        await admin.send(json.dumps({"type": "admin.calib", "table": tok}))
        assert json.loads(await cam.recv())["type"] == "capture.req"
        await cam.send(jpg); assert json.loads(await cam.recv())["type"] == "calib.relayed"
        assert json.loads(await admin.recv())["type"] == "calib.image"
        await admin.send(json.dumps({"type": "admin.corners", "table": tok,
                                     "corners": [[.125, .090], [.875, .109], [.906, .668], [.104, .652]]}))
        assert json.loads(await admin.recv())["type"] == "orient.ask"
        await admin.send(json.dumps({"type": "admin.orient", "table": tok, "side": "right"}))
        assert json.loads(await admin.recv())["type"] == "session.baselined"
    print(f"calibrated table {tok[:6]} on the live server (it persisted to disk)", flush=True)

    mgr = SessionManager()
    mgr.load(SESS)                                    # what a restarted server does on startup
    s = mgr.by_table(tok)
    assert s is not None, "table missing from the persisted file"
    assert s.board_reader is not None, "restored session is not calibrated"
    assert mgr.by_pair(pairtok) is s, "pair index not rebuilt"
    print("restart-load: table restored + calibrated + pair index rebuilt  PERSIST LIVE PASSED", flush=True)


asyncio.run(main())

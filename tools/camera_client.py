"""Camera unit — connect to the chessmon server, calibrate (empty board, then start
position), and answer every capture.req with a real JPEG from the BRIO. The SERVER runs
the vision; this client only captures and uploads (thin client, see the ecosystem plan).

    python tools/camera_client.py --url ws://HOST:8000/ws --pair PAIRTOKEN

Hardware-free testing: --frame out/empty.png feeds a saved image for every capture.
"""
import argparse
import asyncio
import json
import os
import sys

import cv2
import websockets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live                                    # reuse BRIO capture (MJPG/MSMF + exposure lock)

_FRAME = None                                  # set by --frame for hardware-free testing


def grab_jpeg():
    frame = cv2.imread(_FRAME) if _FRAME else live.capture()
    if frame is None:
        return None
    ok, buf = cv2.imencode(".jpg", frame)
    return buf.tobytes() if ok else None


async def _calib(ws, step, prompt):
    await asyncio.to_thread(input, prompt)
    jpg = grab_jpeg()
    if jpg is None:
        print("  capture failed (is the BRIO free / the path valid?)")
        return
    await ws.send(json.dumps({"type": "calib", "step": step}))
    await ws.send(jpg)
    print("  server:", json.loads(await ws.recv()))


async def run(url, pair):
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "pair.join", "pairToken": pair}))
        print("joined:", json.loads(await ws.recv()))
        await _calib(ws, "empty", "Clear the board, then Enter to capture the empty reference... ")
        await _calib(ws, "start", "Set up the START position, then Enter... ")
        print("calibrated — answering capture requests (Ctrl+C to stop)")
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            if msg.get("type") == "capture.req":
                jpg = grab_jpeg()
                if jpg:
                    await ws.send(jpg)
                    print("  captured + sent frame")
            else:
                print("  server:", msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8000/ws")
    ap.add_argument("--pair", required=True)
    ap.add_argument("--frame", help="use a saved image for every capture (testing, no camera)")
    args = ap.parse_args()
    global _FRAME
    _FRAME = args.frame
    asyncio.run(run(args.url, args.pair))


if __name__ == "__main__":
    main()

"""FastAPI + WebSocket layer — the thin socket I/O around the pure `Session` logic.

Speaks the wire contract:
  HTTP  POST /tables                  -> {tableToken, pairToken, qr}     (organiser/web)
        GET  /tables/{token}/state    -> snapshot                        (spectator/web)
  WS    /ws   first message:
          {type: table.join, tableToken}   (clock)
          {type: pair.join,  pairToken}    (camera)
          {type: spectate,   tableToken}   (web)
        then the move loop:
          clock  -> move.confirm {side, clockWhite, clockBlack}
          server -> camera: capture.req
          camera -> <binary frame>   (or, for dev/testing, {type: grid, grid})
          server -> clock: move.result | move.ambiguous | move.unseen | move.unclear
          clock  -> move.resolve {uci}
          server -> cloud/web: state   (broadcast after every accepted move)

Run:  uvicorn server.app:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import os

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .manager import SessionManager

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")

app = FastAPI(title="chessmon server")
mgr = SessionManager()
conns: dict[str, dict] = {}        # table_token -> {clock, camera, spectators}


def hub(token):
    return conns.setdefault(token, {"clock": None, "camera": None, "spectators": set()})


async def send(ws, obj):
    if ws is not None:
        await ws.send_text(json.dumps(obj))


async def broadcast_state(s):
    snap = {"type": "state", **s.snapshot()}
    h = hub(s.table_token)
    for ws in [h["clock"], *list(h["spectators"])]:
        await send(ws, snap)


@app.post("/tables")
async def create_table(body: dict):
    s = mgr.create_table(body.get("white", "White"), body.get("black", "Black"),
                         body.get("variant", "standard"))
    return {"tableToken": s.table_token, "pairToken": s.pair_token,
            "qr": f"/join/{s.table_token}"}


@app.get("/tables/{token}/state")
async def table_state(token: str):
    s = mgr.by_table(token)
    if not s:
        return JSONResponse({"error": "unknown table"}, status_code=404)
    return s.snapshot()


@app.get("/")
async def root():
    return RedirectResponse("/app/clock.html")


@app.get("/favicon.ico")
async def favicon():
    return RedirectResponse("/app/icon.svg")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    s = None
    role = None
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:                       # a camera frame (calib or move)
                if s is not None and role == "camera":
                    frame = cv2.imdecode(np.frombuffer(msg["bytes"], np.uint8),
                                         cv2.IMREAD_COLOR)
                    step = s._calib_step or "move"
                    if frame is not None:                          # save for debugging
                        try:
                            os.makedirs(OUT, exist_ok=True)
                            cv2.imwrite(os.path.join(OUT, f"cam_{step}.png"), frame)
                        except Exception:
                            pass
                    verdict = (s.on_frame(frame) if frame is not None
                               else {"type": "calib.failed", "reason": "undecodable frame"})
                    shape = "x".join(map(str, frame.shape)) if frame is not None else "decode-fail"
                    print(f"[frame] {step} {shape} -> {verdict.get('type')}: {verdict.get('reason', '')}")
                    await send(hub(s.table_token)["clock"], verdict)
                    await send(ws, verdict)                         # echo to the camera operator
                    await broadcast_state(s)
                continue
            data = json.loads(msg["text"])
            t = data.get("type")
            if t == "table.join":
                s, role = mgr.by_table(data["tableToken"]), "clock"
                if s is None:
                    await send(ws, {"type": "error", "reason": "unknown table"})
                    continue
                hub(s.table_token)["clock"] = ws
                await send(ws, {"type": "session.ready", "pairToken": s.pair_token,
                                **s.session_info()})
            elif t == "pair.join":
                s, role = mgr.by_pair(data["pairToken"]), "camera"
                if s is None:
                    await send(ws, {"type": "error", "reason": "unknown pairing"})
                    continue
                hub(s.table_token)["camera"] = ws
                await send(ws, {"type": "session.ready", "role": "camera"})
            elif t == "spectate":
                s, role = mgr.by_table(data["tableToken"]), "spectator"
                if s is None:
                    await send(ws, {"type": "error", "reason": "unknown table"})
                    continue
                hub(s.table_token)["spectators"].add(ws)
                await send(ws, {"type": "state", **s.snapshot()})
            elif s is None:
                await send(ws, {"type": "error", "reason": "join a table first"})
            elif t == "calib":                                    # next camera frame is this step
                s.set_calib_step(data.get("step"))
            elif t == "move.confirm":
                s.confirm(data["side"], data.get("clockWhite"), data.get("clockBlack"))
                await send(hub(s.table_token)["camera"], {"type": "capture.req"})
            elif t == "move.resolve":
                await send(hub(s.table_token)["clock"], s.resolve(data["uci"]))
                await broadcast_state(s)
            elif t == "grid":                                     # dev/testing without a camera
                await send(hub(s.table_token)["clock"], s.ingest_grid(data["grid"]))
                await broadcast_state(s)
    finally:
        if s is not None:
            h = hub(s.table_token)
            if role == "spectator":
                h["spectators"].discard(ws)
            elif h.get(role) is ws:
                h[role] = None


app.mount("/app", StaticFiles(directory=WEB, html=True), name="app")    # the clock PWA

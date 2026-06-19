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

import base64
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
DEVICES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "devices.json")

app = FastAPI(title="chessmon server")
mgr = SessionManager()
conns: dict[str, dict] = {}        # table_token -> {clock, camera, spectators}
devices: dict[str, dict] = {}      # devId -> {id, name, userName, role, table, online, ws}
admins: set = set()                # server-console websockets


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


def dev_public(d):
    return {k: d.get(k) for k in ("id", "name", "userName", "role", "table", "online")}


async def broadcast_devices():
    save_devices()
    lst = [dev_public(d) for d in devices.values()]
    for ws in list(admins):
        await send(ws, {"type": "devices", "devices": lst})


def save_devices():
    """Persist device identities (id, auto name, user-defined name, role) so a server restart
    doesn't lose the names the operator typed in the console."""
    try:
        recs = [{"id": d["id"], "name": d.get("name", ""), "userName": d.get("userName", ""),
                 "role": d.get("role", "")} for d in devices.values()]
        with open(DEVICES_FILE, "w") as f:
            json.dump(recs, f)
    except Exception:
        pass


def load_devices():
    try:
        with open(DEVICES_FILE) as f:
            for d in json.load(f):
                devices[d["id"]] = {**d, "online": False, "ws": None, "table": None}
    except Exception:
        pass


load_devices()


async def to_clock_admins(sess, obj):
    """A calibration message to the table's clock + every console (either can drive the corner-tap)."""
    obj = {**obj, "table": sess.table_token}
    await send(hub(sess.table_token)["clock"], obj)
    for a in list(admins):
        await send(a, obj)


async def to_units_admins(sess, obj):
    """A calibration verdict to the table's clock + camera + every console."""
    obj = {**obj, "table": sess.table_token}
    h = hub(sess.table_token)
    await send(h["clock"], obj)
    await send(h["camera"], obj)
    for a in list(admins):
        await send(a, obj)


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
    dev_id = None
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
                    if step == "corners" and frame is not None:    # relay to the clock for the corner-tap UI
                        s._calib_step, s._calib_frame = None, frame
                        _ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                        durl = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
                        await to_clock_admins(s, {"type": "calib.image", "image": durl})
                        await send(ws, {"type": "calib.relayed"})
                        print(f"[frame] corners {frame.shape} -> relayed to clock")
                        continue
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
                if dev_id in devices:
                    devices[dev_id]["table"] = s.table_token
                    await broadcast_devices()
                await send(ws, {"type": "session.ready", "pairToken": s.pair_token,
                                "calibrated": s.board_reader is not None,
                                "cameraLinked": hub(s.table_token)["camera"] is not None,
                                **s.session_info()})
                await send(ws, {"type": "state", **s.snapshot()})  # restore view on reconnect
            elif t == "pair.join":
                s, role = mgr.by_pair(data["pairToken"]), "camera"
                if s is None:
                    await send(ws, {"type": "error", "reason": "unknown pairing"})
                    continue
                hub(s.table_token)["camera"] = ws
                if dev_id in devices:
                    devices[dev_id]["table"] = s.table_token
                    await broadcast_devices()
                await send(ws, {"type": "session.ready", "role": "camera",
                                "calibrated": s.board_reader is not None})
            elif t == "spectate":
                s, role = mgr.by_table(data["tableToken"]), "spectator"
                if s is None:
                    await send(ws, {"type": "error", "reason": "unknown table"})
                    continue
                hub(s.table_token)["spectators"].add(ws)
                await send(ws, {"type": "state", **s.snapshot()})
            elif t == "ping":                                     # heartbeat — keep the socket alive
                pass
            elif t == "hello":                                    # device registers itself (clock/camera)
                dev_id = data.get("devId")
                if dev_id:
                    d = devices.setdefault(dev_id, {"id": dev_id, "userName": "", "table": None})
                    d.update({"name": data.get("name", "device"), "role": data.get("role", "?"),
                              "online": True, "ws": ws})
                    await broadcast_devices()
            elif t == "admin.join":                               # the server-console page
                role = "admin"
                admins.add(ws)
                await send(ws, {"type": "devices",
                                "devices": [dev_public(d) for d in devices.values()]})
            elif t == "device.rename":                            # console set a user-defined name
                d = devices.get(data.get("devId"))
                if d is not None:
                    d["userName"] = data.get("userName", "")
                    await broadcast_devices()
            elif t == "pair.devices":                             # console pairs two devices into a new table
                clock_dev = devices.get(data.get("clock"))
                cam_dev = devices.get(data.get("camera"))
                sess = mgr.create_table("White", "Black", "standard")
                if clock_dev and clock_dev.get("ws"):             # push the role + token; the unit auto-joins
                    await send(clock_dev["ws"], {"type": "assign", "role": "clock",
                                                 "table": sess.table_token})
                if cam_dev and cam_dev.get("ws"):
                    await send(cam_dev["ws"], {"type": "assign", "role": "camera",
                                               "pair": sess.pair_token})
                await send(ws, {"type": "paired", "table": sess.table_token, "pair": sess.pair_token,
                                "clockOnline": bool(clock_dev and clock_dev.get("ws")),
                                "cameraOnline": bool(cam_dev and cam_dev.get("ws"))})
            elif t == "admin.calib":                              # console triggers a calibration frame
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    sess.set_calib_step("corners")
                    await send(hub(sess.table_token)["camera"], {"type": "capture.req"})
            elif t == "admin.corners":                            # console returned the 4 tapped corners
                sess = mgr.by_table(data.get("table"))
                fr = sess._calib_frame if sess is not None else None
                if fr is None:
                    await send(ws, {"type": "calib.failed", "reason": "no frame — hit Calibrate first"})
                else:
                    h, w = fr.shape[:2]
                    px = [[c[0] * w, c[1] * h] for c in data["corners"]]   # fractions -> pixels
                    try:
                        verdict = sess.calibrate_oneshot(fr, px)
                    except Exception as e:
                        verdict = {"type": "calib.failed", "reason": str(e)}
                    await to_units_admins(sess, verdict)
                    if verdict.get("type") == "session.baselined":
                        await broadcast_state(sess)
            elif t == "admin.orient":                             # console picked which side White is on
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    verdict = sess.resolve_orientation(data.get("side"))
                    await to_units_admins(sess, verdict)
                    if verdict.get("type") == "session.baselined":
                        await broadcast_state(sess)
            elif s is None:
                await send(ws, {"type": "error", "reason": "join a table first"})
            elif t == "calib":                                    # next camera frame is this step
                s.set_calib_step(data.get("step"))
            elif t == "calib.corners":                            # clock returned the 4 tapped corners
                fr = s._calib_frame
                if fr is None:
                    await send(ws, {"type": "calib.failed",
                                    "reason": "no frame yet — tap Calibrate on the camera"})
                else:
                    h, w = fr.shape[:2]
                    px = [[c[0] * w, c[1] * h] for c in data["corners"]]   # fractions -> pixels
                    try:
                        verdict = s.calibrate_oneshot(fr, px)          # one-step on the set-up board
                    except Exception as e:
                        verdict = {"type": "calib.failed", "reason": str(e)}
                    await send(hub(s.table_token)["clock"], verdict)
                    await send(hub(s.table_token)["camera"], verdict)
                    if verdict.get("type") == "session.baselined":
                        await broadcast_state(s)
            elif t == "orient.pick":                              # clock picked which side White is on
                verdict = s.resolve_orientation(data.get("side"))
                await send(hub(s.table_token)["clock"], verdict)
                await send(hub(s.table_token)["camera"], verdict)
                if verdict.get("type") == "session.baselined":
                    await broadcast_state(s)
            elif t == "move.confirm":
                s.confirm(data["side"], data.get("clockWhite"), data.get("clockBlack"))
                await send(hub(s.table_token)["camera"], {"type": "capture.req"})
            elif t == "move.resolve":
                await send(hub(s.table_token)["clock"], s.resolve(data["uci"]))
                await broadcast_state(s)
            elif t == "flag":                                     # a clock hit 0 -> loss on time
                result = "1-0" if data.get("side") == "black" else "0-1"
                await send(hub(s.table_token)["clock"], s.end(result))
                await broadcast_state(s)
            elif t == "refresh":                                  # clock wants a fresh read (board moved?)
                s.set_calib_step("refresh")
                await send(hub(s.table_token)["camera"], {"type": "capture.req"})
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
        admins.discard(ws)
        if dev_id in devices and devices[dev_id].get("ws") is ws:
            devices[dev_id]["online"] = False
            devices[dev_id]["ws"] = None
            await broadcast_devices()


app.mount("/app", StaticFiles(directory=WEB, html=True), name="app")    # the clock PWA

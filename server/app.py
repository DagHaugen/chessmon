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

import asyncio
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
DEVICES_FILE = os.environ.get("CHESSMON_DEVICES",
                              os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "devices.json"))
SESSIONS_FILE = os.environ.get("CHESSMON_SESSIONS",
                               os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sessions.pkl"))

app = FastAPI(title="chessmon server")
mgr = SessionManager()
mgr.load(SESSIONS_FILE)            # resume calibrated sessions + games across a restart
conns: dict[str, dict] = {}        # table_token -> {clock, camera, spectators}
devices: dict[str, dict] = {}      # devId -> {id, name, userName, role, table, online, ws}
admins: set = set()                # server-console websockets


def _quiet_conn_reset(loop, context):
    # Windows' Proactor loop raises ConnectionResetError/AbortedError (WinError 10054/10053) from its
    # transport-cleanup callback when a client drops the socket abruptly (phone sleeps, a WS reconnects,
    # a tab closes). It's harmless and already handled in the ws loop -- swallow it, log everything else.
    if isinstance(context.get("exception"), (ConnectionResetError, ConnectionAbortedError)):
        return
    loop.default_exception_handler(context)


@app.on_event("startup")
async def _install_loop_handler():
    asyncio.get_running_loop().set_exception_handler(_quiet_conn_reset)


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
    await broadcast_tables()        # keep the console's Running-games list live (moves / result)
    mgr.save(SESSIONS_FILE)         # a move or calibration changed the session -> persist


def dev_public(d):
    return {k: d.get(k) for k in ("id", "name", "userName", "role", "table", "online", "screen", "cam", "plat", "battery")}


def tables_public():
    """The configured tables (each persists its unit assignments + name + calibration)."""
    return [{"token": s.table_token, "name": s.name, "clock": s.clock_dev, "camera": s.camera_dev,
             "calibrated": s.board_reader is not None, "moved": getattr(s, "alignment_alert", False), "moves": len(s.moves),
             "started_at": s.started_at, "result": s.result, "status": getattr(s, "status", "")}
            for s in mgr._by_table.values()]


def admin_state():
    return {"type": "devices", "devices": [dev_public(d) for d in devices.values()], "tables": tables_public()}


async def broadcast_devices():
    save_devices()
    msg = admin_state()
    for ws in list(admins):
        await send(ws, msg)


async def broadcast_tables():
    """Just the table list (live moves / started / result) -> the console, without re-saving devices."""
    msg = {"type": "tables", "tables": tables_public()}
    for ws in list(admins):
        await send(ws, msg)


def save_devices():
    """Persist device identities (id, auto name, user-defined name, role) so a server restart
    doesn't lose the names the operator typed in the console."""
    try:
        recs = [{"id": d["id"], "name": d.get("name", ""), "userName": d.get("userName", ""),
                 "role": d.get("role", ""), "screen": d.get("screen"), "cam": d.get("cam"), "plat": d.get("plat")}
                for d in devices.values()]
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
for _s in mgr._by_table.values():          # rebuild the live device<->table link from persisted table configs
    for _role, _devid in (("clock", _s.clock_dev), ("camera", _s.camera_dev)):
        if _devid and _devid in devices:
            devices[_devid]["table"] = _s.table_token
            devices[_devid]["role"] = _role


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
                         body.get("variant", "standard"), name=body.get("name", ""))
    mgr.save(SESSIONS_FILE)
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
    return RedirectResponse("/app/")            # the device landing page (index.html); it routes to clock/camera once assigned


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
                    if step == "snap" and frame is not None:       # console "View screen" -> relay a live frame to admins
                        s._calib_step = None
                        _ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                        durl = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
                        for a in list(admins):
                            await send(a, {"type": "snap.image", "table": s.table_token, "image": durl, "corners": s.corners})
                        continue
                    if step == "corners" and frame is not None:    # relay to the clock for the corner-tap UI
                        s._calib_step, s._calib_frame = None, frame
                        _ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                        durl = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
                        await to_clock_admins(s, {"type": "calib.image", "image": durl, "corners": s.corners})
                        await send(ws, {"type": "calib.relayed"})
                        print(f"[frame] corners {frame.shape} -> relayed to clock")
                        continue
                    verdict = (s.on_frame(frame) if frame is not None
                               else {"type": "calib.failed", "reason": "undecodable frame"})
                    shape = "x".join(map(str, frame.shape)) if frame is not None else "decode-fail"
                    print(f"[frame] {step} {shape} -> {verdict.get('type')}: {verdict.get('reason', '')}")
                    await send(hub(s.table_token)["clock"], verdict)
                    await send(ws, verdict)                         # echo to the camera operator
                    # A warning / prompt verdict (illegal, no-change, ambiguous, unseen) did NOT change the
                    # board -- re-pushing 'state' to the clock would wipe the warning it just rendered (the
                    # "flash then back to green" bug). Refresh the console only (keeps the camera-moved badge
                    # live); anything that actually advanced the game broadcasts state as before.
                    if verdict.get("type") in ("move.unclear", "move.nochange", "move.ambiguous", "move.unseen"):
                        await broadcast_tables()
                    else:
                        await broadcast_state(s)
                continue
            data = json.loads(msg["text"])
            t = data.get("type")
            if t == "table.join":
                s, role = mgr.by_table(data["tableToken"]), "clock"
                if s is None:
                    if dev_id in devices and devices[dev_id].get("table") == data["tableToken"]:
                        devices[dev_id]["table"] = None              # table was pruned -> free the device
                        await broadcast_devices()
                    await send(ws, {"type": "error", "reason": "unknown table"})
                    continue
                hub(s.table_token)["clock"] = ws
                if dev_id in devices:
                    devices[dev_id]["table"] = s.table_token
                    await broadcast_devices()
                await send(ws, {"type": "session.ready", "pairToken": s.pair_token,
                                "calibrated": s.board_reader is not None,
                                "cameraLinked": hub(s.table_token)["camera"] is not None,
                                "cameraAssigned": s.camera_dev is not None,
                                **s.session_info()})
                await send(ws, {"type": "state", **s.snapshot()})  # restore view on reconnect
            elif t == "pair.join":
                s, role = mgr.by_pair(data["pairToken"]), "camera"
                if s is None:
                    await send(ws, {"type": "error", "reason": "unknown pairing"})
                    continue
                hub(s.table_token)["camera"] = ws
                if dev_id in devices:
                    s.camera_dev = dev_id                            # record the pairing so the console shows the camera on this table
                    s.activate_calibration()                        # restore THIS camera's remembered calibration (same as console-add, so QR/console agree)
                    devices[dev_id]["table"] = s.table_token
                    await broadcast_devices()
                await send(hub(s.table_token)["clock"], {"type": "camera.linked", "calibrated": s.board_reader is not None})  # clock drops the QR (reflects the restored calibration)
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
            elif t == "hello":                                    # device registers itself (landing / clock / camera)
                dev_id = data.get("devId")
                if dev_id:
                    landing = bool(data.get("landing"))           # the /app landing page is role-agnostic
                    known = dev_id in devices                     # already registered before this hello? (server recognises it)
                    d = devices.setdefault(dev_id, {"id": dev_id, "userName": "", "table": None})
                    d.update({"name": data.get("name", d.get("name", "device")), "online": True, "ws": ws})
                    if not landing:                               # a clock/camera page declares its role; the landing must not clobber it
                        d["role"] = data.get("role", "?")
                    for k in ("screen", "plat"):
                        if data.get(k):
                            d[k] = data[k]
                    await broadcast_devices()
                    await send(ws, {"type": "welcome", "known": known, "userName": d.get("userName", "")})  # the name the device shows (landing + clock both)
                    if landing:                                   # the landing also gets bounced straight to its role page once configured
                        # already configured? bounce it straight to its role page
                        sess = next((s for s in mgr._by_table.values()
                                     if s.clock_dev == dev_id or s.camera_dev == dev_id), None)
                        if sess is not None and sess.clock_dev == dev_id:
                            await send(ws, {"type": "assign", "role": "clock", "table": sess.table_token})
                        elif sess is not None:
                            await send(ws, {"type": "assign", "role": "camera", "pair": sess.pair_token})
            elif t == "device.meta":                              # extra device info (camera res, live battery)
                if dev_id in devices:
                    for k in ("screen", "cam", "battery"):
                        if data.get(k):
                            devices[dev_id][k] = data[k]
                    await broadcast_devices()
            elif t == "admin.join":                               # the server-console page
                role = "admin"
                admins.add(ws)
                await send(ws, admin_state())
            elif t == "device.rename":                            # console (or the device itself) set a user-defined name
                d = devices.get(data.get("devId"))
                if d is not None:
                    d["userName"] = data.get("userName", "")
                    await broadcast_devices()
                    await send(d.get("ws"), {"type": "name.updated", "userName": d["userName"]})  # reflect it on the device's landing page
            elif t == "device.remove":                            # console forgot a device (stale / phantom)
                if devices.pop(data.get("devId"), None) is not None:
                    await broadcast_devices()
            elif t == "table.create":                             # console makes a new, empty table
                mgr.create_table("White", "Black", "standard", name=(data.get("name") or "").strip())
                mgr.save(SESSIONS_FILE)
                await broadcast_devices()
            elif t == "table.rename":
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    sess.name = (data.get("name") or "").strip()
                    mgr.save(SESSIONS_FILE)
                    await broadcast_devices()
            elif t == "table.cleargame":                          # console clears a stale/finished game off a table (keeps its setup)
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    sess.reset_game()
                    await broadcast_state(sess)                   # clock/spectators back to a fresh start (also persists)
                    await broadcast_devices()                     # drop it out of the console's Running list
            elif t == "table.remove":                             # console deletes a table -> its units go back to Unused
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    for dv in devices.values():
                        if dv.get("table") == sess.table_token:
                            dv["table"] = None
                    mgr._by_table.pop(sess.table_token, None)
                    mgr._by_pair.pop(sess.pair_token, None)
                    mgr.save(SESSIONS_FILE)
                    await broadcast_devices()
            elif t == "table.assign":                             # add / replace a unit on a table, picked from Unused
                sess = mgr.by_table(data.get("table"))
                dev = devices.get(data.get("devId"))
                role2 = data.get("role")
                if sess is not None and dev is not None and role2 in ("clock", "camera"):
                    for other in mgr._by_table.values():          # a device lives on one table -> free its old slot
                        if other.clock_dev == dev["id"]:
                            other.clock_dev = None
                        if other.camera_dev == dev["id"]:
                            other.camera_dev = None
                    if role2 == "clock":
                        sess.clock_dev = dev["id"]
                        if dev.get("ws"):
                            await send(dev["ws"], {"type": "assign", "role": "clock", "table": sess.table_token})
                    else:
                        sess.camera_dev = dev["id"]
                        sess.activate_calibration()                  # restore this camera's remembered calibration (or none)
                        if dev.get("ws"):
                            await send(dev["ws"], {"type": "assign", "role": "camera", "pair": sess.pair_token})
                    dev["table"] = sess.table_token
                    dev["role"] = role2
                    mgr.save(SESSIONS_FILE)
                    await broadcast_devices()
            elif t == "table.unassign":                           # pull a unit off a table -> back to Unused
                sess = mgr.by_table(data.get("table"))
                role2 = data.get("role")
                if sess is not None and role2 in ("clock", "camera"):
                    devid = sess.clock_dev if role2 == "clock" else sess.camera_dev
                    if role2 == "clock":
                        sess.clock_dev = None
                    else:
                        sess.camera_dev = None
                        sess.activate_calibration()                  # camera removed -> no active calibration
                    dev = devices.get(devid)
                    if dev is not None:
                        dev["table"] = None
                        if dev.get("ws"):
                            await send(dev["ws"], {"type": "unassigned"})
                    mgr.save(SESSIONS_FILE)
                    await broadcast_devices()
            elif t == "camera.control":                           # console -> the table's camera: screen on/off, flashlight
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    await send(hub(sess.table_token)["camera"],
                               {"type": "camera.control", "what": data.get("what"), "on": bool(data.get("on")), "value": data.get("value")})
            elif t == "admin.watch":                              # console opens the live board for a running game
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    hub(sess.table_token)["spectators"].add(ws)
                    await send(ws, {"type": "state", **sess.snapshot()})
            elif t == "admin.unwatch":
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    hub(sess.table_token)["spectators"].discard(ws)
            elif t == "admin.snap":                               # console "View screen" -> ask the camera for a live frame
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    sess.set_calib_step("snap")
                    await send(hub(sess.table_token)["camera"], {"type": "capture.req"})
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
                    sess.corners = data["corners"]                         # remember for view/edit from the console
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
                    s.corners = data["corners"]
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
            elif t == "move.cancel":                              # clock gave up on the guesses -> retry
                if s is not None:
                    s.revert_to_valid()                           # re-anchor to the last valid move (player sets the piece back)
                    await broadcast_state(s)
            elif t == "flag":                                     # a clock hit 0 -> loss on time
                result = "1-0" if data.get("side") == "black" else "0-1"
                await send(hub(s.table_token)["clock"], s.end(result))
                await broadcast_state(s)
            elif t == "refresh":                                  # clock wants a fresh read (board moved?)
                s.set_calib_step("refresh")
                await send(hub(s.table_token)["camera"], {"type": "capture.req"})
            elif t == "game.start":                               # clock pressed START -> mark the game running
                s.mark_started()
                await broadcast_state(s)
            elif t == "game.status":                              # clock reports running / paused / waiting
                s.status = data.get("status", "")
                await broadcast_tables()
            elif t == "game.reset":                               # operator reset the pieces to the start
                s.reset_game()
                mgr.save(SESSIONS_FILE)
                await broadcast_state(s)
                cam = hub(s.table_token)["camera"]
                if cam is not None and s.board_reader is not None:
                    s.set_calib_step("refresh")                   # re-anchor the baseline to the start position
                    await send(cam, {"type": "capture.req"})
            elif t == "game.undo":                                # take back the last (wrong) move
                if s.undo_move():
                    mgr.save(SESSIONS_FILE)
                    await broadcast_state(s)
                    cam = hub(s.table_token)["camera"]
                    if cam is not None and s.board_reader is not None:
                        s.set_calib_step("refresh")               # re-anchor to the reverted position
                        await send(cam, {"type": "capture.req"})
            elif t == "camera.controlled":                        # camera -> console: did the screen/torch control apply?
                for a in list(admins):
                    await send(a, {"type": "camera.controlled", "table": s.table_token,
                                   "what": data.get("what"), "on": bool(data.get("on")),
                                   "ok": bool(data.get("ok")), "reason": data.get("reason", "")})
            elif t == "camera.status":                            # camera -> console: its actual flash/screen state on connect/link
                if s is not None:
                    for a in list(admins):
                        await send(a, {"type": "camera.status", "table": s.table_token,
                                       "flash": bool(data.get("flash")),
                                       "flashAvail": bool(data.get("flashAvail", True)),
                                       "screen": bool(data.get("screen", True)),
                                       "zoom": data.get("zoom"),
                                       "zoomMin": data.get("zoomMin"),
                                       "zoomMax": data.get("zoomMax")})
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
                if role == "camera":                          # tell the clock its camera dropped -> WAIT (or "removed" if it was unassigned)
                    await send(h["clock"], {"type": "camera.offline", "cameraAssigned": s.camera_dev is not None})
        admins.discard(ws)
        for hb in conns.values():
            hb["spectators"].discard(ws)                # an admin that was watching a board
        if dev_id in devices and devices[dev_id].get("ws") is ws:
            devices[dev_id]["online"] = False
            devices[dev_id]["ws"] = None
            await broadcast_devices()


app.mount("/app", StaticFiles(directory=WEB, html=True), name="app")    # the clock PWA

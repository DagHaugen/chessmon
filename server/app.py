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
import io
import json
import os
import platform
import socket
import subprocess
import time
import uuid

import chess
import chess.pgn
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
FORMATS_FILE = os.environ.get("CHESSMON_FORMATS",
                              os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "formats.json"))
PLAYERS_FILE = os.environ.get("CHESSMON_PLAYERS",
                              os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "players.json"))
TOURNAMENTS_FILE = os.environ.get("CHESSMON_TOURNAMENTS",
                                  os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tournaments.json"))
GAMES_FILE = os.environ.get("CHESSMON_GAMES",
                            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "games.json"))

app = FastAPI(title="chessmon server")
mgr = SessionManager()
mgr.load(SESSIONS_FILE)            # resume calibrated sessions + games across a restart
conns: dict[str, dict] = {}        # table_token -> {clock, camera, spectators}
devices: dict[str, dict] = {}      # devId -> {id, name, userName, role, table, online, ws}
admins: set = set()                # server-console websockets
formats: dict = {}                 # format id -> {id, name, base_ms, increment_ms, inc_type, category, variant}
players: dict = {}                 # player id -> {id, name}
tournaments: dict = {}             # tournament id -> {id, name, format, players[], rounds[]}
games: list = []                   # append-only archive of finished games (moves + pgn + metadata)
network: dict = {"dhcp": None, "ip": None, "mac": None, "host": "", "adapters": []}   # how this host holds its LAN IP -> console "DHCP" chip


def detect_network():
    """Best-effort: does this host hold its LAN IP by dynamic DHCP (can change) or pinned (static/reserved)?
    Windows picks the *physical*, Up, gateway-bearing NIC and reads DHCP from the address origin — so a VPN /
    Hyper-V / WSL adapter is skipped by adapter type (not by IP range, which is unreliable: VPNs use 10.x and
    172.x alike). Other OSes return dhcp=None so the console just hides the chip."""
    info = {"dhcp": None, "ip": None, "mac": None, "host": socket.gethostname(), "adapters": []}
    if platform.system() == "Windows":
        try:
            # one record per physical, connected NIC: its IPv4, whether that address is DHCP, MAC, and its default route
            ps = ("Get-NetAdapter -Physical | ? { $_.Status -eq 'Up' } | % { $i=$_.ifIndex; "
                  "$ip = Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 -EA SilentlyContinue | "
                  "? { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1; "
                  "$gw = Get-NetRoute -InterfaceIndex $i -DestinationPrefix '0.0.0.0/0' -EA SilentlyContinue | Select-Object -First 1; "
                  "if ($ip) { [pscustomobject]@{ IP=$ip.IPAddress; DHCP=($ip.PrefixOrigin -eq 'Dhcp'); "
                  "MAC=$_.MacAddress; HasGW=[bool]$gw; Metric=$(if($gw){$gw.RouteMetric}else{9999}) } } } | ConvertTo-Json -Compress")
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                                 capture_output=True, text=True, timeout=8).stdout
            data = json.loads(out or "[]")
            if isinstance(data, dict):
                data = [data]
            elif not isinstance(data, list):
                data = []
            cands = [ad for ad in data if ad.get("IP")]
            # every physical NIC, so the console can match the exact address it was opened on (like the QR does)
            info["adapters"] = [{"ip": ad["IP"], "mac": ((ad.get("MAC") or "").replace("-", ":")) or None,
                                 "dhcp": bool(ad.get("DHCP"))} for ad in cands]
            if cands:
                # the OS's own pick: a default-gateway NIC first, then the lowest route metric (the active link)
                cands.sort(key=lambda ad: (0 if ad.get("HasGW") else 1,
                                           ad["Metric"] if ad.get("Metric") is not None else 9999))
                ad = cands[0]
                info["ip"] = ad["IP"]
                info["dhcp"] = bool(ad.get("DHCP"))
                info["mac"] = ((ad.get("MAC") or "").replace("-", ":")) or None
        except Exception:
            pass
    if not info["ip"]:                                        # non-Windows / probe failed -> default-route IP (may be a VPN)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80)); info["ip"] = s.getsockname()[0]; s.close()
        except Exception:
            pass
    return info


def _quiet_conn_reset(loop, context):
    # Windows' Proactor loop raises ConnectionResetError/AbortedError (WinError 10054/10053) from its
    # transport-cleanup callback when a client drops the socket abruptly (phone sleeps, a WS reconnects,
    # a tab closes). It's harmless and already handled in the ws loop -- swallow it, log everything else.
    if isinstance(context.get("exception"), (ConnectionResetError, ConnectionAbortedError)):
        return
    loop.default_exception_handler(context)


@app.on_event("startup")
async def _install_loop_handler():
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_quiet_conn_reset)

    async def _net_loop():                  # probe DHCP/IP off the event loop, then refresh so a lease change shows within a minute
        global network
        while True:
            try:
                network = await loop.run_in_executor(None, detect_network)
            except Exception:
                pass
            await asyncio.sleep(60)
    asyncio.create_task(_net_loop())


def hub(token):
    return conns.setdefault(token, {"clock": None, "camera": None, "spectators": set()})


async def send(ws, obj):
    if ws is None:
        return
    data = json.dumps(obj)                 # serialize first so a real encoding bug still surfaces
    try:
        await ws.send_text(data)
    except Exception:
        pass                               # client went away mid-broadcast (1001/1006); its disconnect handler drops it from the lists


async def broadcast_state(s):
    snap = {"type": "state", **s.snapshot()}
    h = hub(s.table_token)
    for ws in [h["clock"], *list(h["spectators"])]:
        await send(ws, snap)
    await broadcast_tables()        # keep the console's Running-games list live (moves / result)
    mgr.save(SESSIONS_FILE)         # a move or calibration changed the session -> persist
    if archive_if_finished(s):      # a finished game -> save it to the archive before any reuse clears the moves
        for a in list(admins):
            await send(a, {"type": "games_changed"})
    if flow_result_to_tournament(s):   # a finished tournament game -> write the result back onto its pairing
        await broadcast_admin()


def dev_public(d):
    return {k: d.get(k) for k in ("id", "name", "userName", "role", "table", "online", "screen", "cam", "plat", "battery")}


def tables_public():
    """The configured tables (each persists its unit assignments + name + calibration)."""
    return [{"token": s.table_token, "name": s.name, "clock": s.clock_dev, "camera": s.camera_dev,
             "calibrated": s.board_reader is not None, "moved": getattr(s, "alignment_alert", False), "moves": len(s.moves),
             "san": [m.get("san", "") for m in s.moves], "fen": s.game.board.fen(), "match": getattr(s, "match", None),   # live board + match for the console
             "started_at": s.started_at, "result": s.result, "status": getattr(s, "status", "")}
            for s in mgr._by_table.values()]


def flow_result_to_tournament(s):
    """A finished game whose Match came from a tournament pairing -> record the result on that pairing.
    Idempotent: returns True only on the transition (when the pairing's result actually changes)."""
    res = getattr(s, "result", None)
    if res not in ("1-0", "0-1", "1/2-1/2"):
        return False
    ref = (getattr(s, "match", None) or {}).get("ref") or {}
    tr = tournaments.get(ref.get("tournament"))
    pid = ref.get("pairing")
    if not tr or not pid:
        return False
    for rd in tr.get("rounds", []):
        for pg in rd.get("pairings", []):
            if pg.get("id") == pid:
                if pg.get("r") != res:
                    pg["r"] = res
                    save_tournaments()
                    return True
                return False
    return False


def archive_if_finished(s):
    """A just-finished game -> append it to the games archive once, before any reuse clears the moves."""
    if not getattr(s, "result", None) or getattr(s, "_archived", False):
        return False
    s._archived = True
    if not s.moves:                                   # a result with no moves -> nothing worth keeping
        return False
    m = getattr(s, "match", None) or {}
    ref = m.get("ref") or {}
    fmt = m.get("format") or {}
    games.append({
        "id": uuid.uuid4().hex[:12],
        "table": s.table_token, "table_name": s.name,
        "white": (m.get("white") or {}).get("name") or s.white,
        "black": (m.get("black") or {}).get("name") or s.black,
        "result": s.result,
        "ply": len(s.moves),
        "moves": [{"san": mv.get("san", ""), "fen": mv.get("fen", "")} for mv in s.moves],
        "pgn": s.pgn(),
        "format": fmt.get("name"),
        "variant": getattr(s, "variant", "standard"),
        "chess960": m.get("chess960"),
        "start_fen": getattr(s, "start_fen", None),
        "tournament": ref.get("tournament"),
        "tournament_name": ref.get("name"),
        "round": ref.get("round"),
        "finished_at": time.time(),
    })
    save_games()
    return True


def game_pgn(g):
    """Build a full PGN (with headers) from an archived game record."""
    h = ['[Event "%s"]' % (g.get("tournament_name") or "chessmon game"), '[Site "chessmon"]']
    if g.get("finished_at"):
        h.append('[Date "%s"]' % time.strftime("%Y.%m.%d", time.localtime(g["finished_at"])))
    if g.get("round") is not None:
        h.append('[Round "%d"]' % (int(g["round"]) + 1))
    h += ['[White "%s"]' % (g.get("white") or "White"),
          '[Black "%s"]' % (g.get("black") or "Black"),
          '[Result "%s"]' % (g.get("result") or "*")]
    if g.get("variant") == "chess960":
        h.append('[Variant "Chess960"]')
        if g.get("start_fen"):
            h += ['[FEN "%s"]' % g["start_fen"], '[SetUp "1"]']
    body = (g.get("pgn", "") + " " + (g.get("result") or "*")).strip()
    return "\n".join(h) + "\n\n" + body + "\n"


def admin_state():
    return {"type": "devices", "devices": [dev_public(d) for d in devices.values()], "tables": tables_public(),
            "formats": list(formats.values()), "players": list(players.values()),
            "tournaments": list(tournaments.values()), "network": network}


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


async def broadcast_admin():
    """Full admin state (devices + tables + formats + players) -> every console / management page."""
    msg = admin_state()
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


SEED_FORMATS = [   # common controls (from the time-controls reference); seeded on first run, then editable
    {"id": "f_90_30", "name": "90+30",   "base_ms": 5400000, "increment_ms": 30000, "inc_type": "fischer", "category": "Classical", "variant": "standard"},
    {"id": "f_15_10", "name": "15+10",   "base_ms":  900000, "increment_ms": 10000, "inc_type": "fischer", "category": "Rapid",     "variant": "standard"},
    {"id": "f_25_10", "name": "25+10",   "base_ms": 1500000, "increment_ms": 10000, "inc_type": "fischer", "category": "Rapid",     "variant": "standard"},
    {"id": "f_10_0",  "name": "10+0",    "base_ms":  600000, "increment_ms":     0, "inc_type": "none",    "category": "Rapid",     "variant": "standard"},
    {"id": "f_30_0",  "name": "30+0",    "base_ms": 1800000, "increment_ms":     0, "inc_type": "none",    "category": "Rapid",     "variant": "standard"},
    {"id": "f_3_2",   "name": "3+2",     "base_ms":  180000, "increment_ms":  2000, "inc_type": "fischer", "category": "Blitz",     "variant": "standard"},
    {"id": "f_3_0",   "name": "3+0",     "base_ms":  180000, "increment_ms":     0, "inc_type": "none",    "category": "Blitz",     "variant": "standard"},
    {"id": "f_5_0",   "name": "5+0",     "base_ms":  300000, "increment_ms":     0, "inc_type": "none",    "category": "Blitz",     "variant": "standard"},
    {"id": "f_5_5",   "name": "5+5",     "base_ms":  300000, "increment_ms":  5000, "inc_type": "fischer", "category": "Blitz",     "variant": "standard"},
    {"id": "f_1_0",   "name": "1+0",     "base_ms":   60000, "increment_ms":     0, "inc_type": "none",    "category": "Bullet",    "variant": "standard"},
    {"id": "f_1_1",   "name": "1+1",     "base_ms":   60000, "increment_ms":  1000, "inc_type": "fischer", "category": "Bullet",    "variant": "standard"},
    {"id": "f_2_1",   "name": "2+1",     "base_ms":  120000, "increment_ms":  1000, "inc_type": "fischer", "category": "Bullet",    "variant": "standard"},
    {"id": "f_g60d5", "name": "G/60 d5", "base_ms": 3600000, "increment_ms":  5000, "inc_type": "delay",   "category": "Rapid",     "variant": "standard"},
]


def load_formats():
    try:
        with open(FORMATS_FILE) as f:
            for r in json.load(f):
                formats[r["id"]] = r
    except Exception:
        pass
    if not formats:                       # first run -> seed the common time controls
        for r in SEED_FORMATS:
            formats[r["id"]] = dict(r)
        save_formats()


def save_formats():
    try:
        with open(FORMATS_FILE, "w") as f:
            json.dump(list(formats.values()), f, indent=1)
    except Exception:
        pass


def load_players():
    try:
        with open(PLAYERS_FILE) as f:
            for r in json.load(f):
                players[r["id"]] = r
    except Exception:
        pass


def save_players():
    try:
        with open(PLAYERS_FILE, "w") as f:
            json.dump(list(players.values()), f, indent=1)
    except Exception:
        pass


def load_tournaments():
    try:
        with open(TOURNAMENTS_FILE) as f:
            for r in json.load(f):
                tournaments[r["id"]] = r
    except Exception:
        pass


def save_tournaments():
    try:
        with open(TOURNAMENTS_FILE, "w") as f:
            json.dump(list(tournaments.values()), f, indent=1)
    except Exception:
        pass


def load_games():
    try:
        with open(GAMES_FILE) as f:
            games.clear(); games.extend(json.load(f))
    except Exception:
        pass


def save_games():
    try:
        with open(GAMES_FILE, "w") as f:
            json.dump(games, f, indent=1)
    except Exception:
        pass


load_devices()
load_formats()
load_players()
load_tournaments()
load_games()
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
                    if frame is not None:
                        s._last_frame = frame                      # cache the latest board for preview.req (overlays, no fresh grab)
                        try:                                       # also save for debugging
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
                if s.clock_dev != dev_id:                            # not this table's assigned clock (stale cm_table) -> don't let it hijack the table
                    if dev_id in devices:
                        devices[dev_id]["table"] = None
                        await broadcast_devices()
                    await send(ws, {"type": "unassigned"})           # clears its cm_table and bounces it to the landing
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
            elif t == "preview.req":                              # overlay wants the latest board WITHOUT a fresh grab (avoids the move-detect frame race)
                sess = mgr.by_table(data.get("table"))
                f = getattr(sess, "_last_frame", None) if sess is not None else None
                if f is not None:
                    _ok, buf = cv2.imencode(".jpg", f, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                    durl = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
                    await send(ws, {"type": "preview.image", "table": sess.table_token, "image": durl, "corners": sess.corners})
            elif t == "formats.save":                             # management page added/edited a time control
                fmt = data.get("format") or {}
                if fmt.get("id"):
                    formats[fmt["id"]] = fmt
                    save_formats()
                    await broadcast_admin()
            elif t == "formats.delete":
                if formats.pop(data.get("id"), None) is not None:
                    save_formats()
                    await broadcast_admin()
            elif t == "players.save":                             # management page added/edited a player
                p = data.get("player") or {}
                if p.get("id"):
                    players[p["id"]] = p
                    save_players()
                    await broadcast_admin()
            elif t == "players.delete":
                if players.pop(data.get("id"), None) is not None:
                    save_players()
                    await broadcast_admin()
            elif t == "tournament.save":                          # management page created/edited a tournament
                tr = data.get("tournament") or {}
                if tr.get("id"):
                    tournaments[tr["id"]] = tr
                    save_tournaments()
                    await broadcast_admin()
            elif t == "tournament.delete":
                if tournaments.pop(data.get("id"), None) is not None:
                    save_tournaments()
                    await broadcast_admin()
            elif t == "tournament.start_round":                   # activate a planned round: push its matches to the planned tables, skipping any busy with another game
                tr = tournaments.get(data.get("tournament"))
                ri = data.get("round")
                rounds = (tr or {}).get("rounds", [])
                if tr is None or not isinstance(ri, int) or ri < 0 or ri >= len(rounds):
                    await send(ws, {"type": "error", "reason": "unknown tournament or round"})
                else:
                    rd = rounds[ri]
                    ch960 = tr.get("variant") == "chess960"
                    if ch960 and rd.get("chess960") is None:
                        await send(ws, {"type": "error", "reason": "set this round's Chess960 number first"})
                    else:
                        def _pl(x):
                            r = players.get(x) or {}
                            return {"id": x, "name": r.get("name", "")} if x else None
                        n = int(rd["chess960"]) if ch960 else None
                        start_fen = chess.Board.from_chess960_pos(n).fen() if ch960 else None
                        started, conflicts = [], []
                        for pg in rd.get("pairings", []):
                            tok = pg.get("table")
                            if pg.get("r") or not tok or not pg.get("w") or not pg.get("b"):
                                continue                          # finished / unplanned / incomplete -> skip
                            sess = mgr.by_table(tok)
                            if sess is None:
                                continue
                            in_prog = bool(sess.moves) and not sess.result
                            ref_pid = (sess.match or {}).get("ref", {}).get("pairing") if sess.match else None
                            if in_prog and ref_pid == pg["id"]:
                                continue                          # this very pairing is already running here
                            if in_prog:
                                conflicts.append(sess.name or tok[:6])   # a different game is live on this table
                                continue
                            sess.match = {"white": _pl(pg.get("w")), "black": _pl(pg.get("b")),
                                          "format": tr.get("format"), "start_mode": "manual",
                                          "variant": "chess960" if ch960 else "standard",
                                          "chess960": n, "start_fen": start_fen,
                                          "ref": {"tournament": tr["id"], "pairing": pg["id"], "name": tr.get("name", ""), "round": ri}}
                            sess.apply_match_position(start_fen, ch960)   # free or finished table -> reset to the start
                            await broadcast_state(sess)               # board + match -> clock; tables -> console/setup; persists
                            await send(hub(sess.table_token)["clock"], {"type": "match", "match": sess.match})
                            started.append(sess.name or tok[:6])
                        await send(ws, {"type": "tournament.started", "round": ri,
                                        "started": started, "conflicts": conflicts})
            elif t == "games.list":                               # the archive, metadata only (light)
                await send(ws, {"type": "games", "games": [
                    {k: g.get(k) for k in ("id", "white", "black", "result", "ply", "format",
                                           "variant", "chess960", "table_name", "tournament",
                                           "tournament_name", "round", "finished_at")}
                    for g in games]})
            elif t == "games.get":                                # one game in full (moves + start_fen) for replay
                g = next((x for x in games if x.get("id") == data.get("id")), None)
                await send(ws, {"type": "game", "game": g})
            elif t == "games.pgn":                                # built PGN text for download: one game (id), one tournament, or all
                gid, tid = data.get("id"), data.get("tournament")
                if gid:
                    sel = [g for g in games if g.get("id") == gid]
                else:
                    sel = [g for g in games if tid is None or g.get("tournament") == tid]
                await send(ws, {"type": "games_pgn", "pgn": "\n\n".join(game_pgn(g) for g in sel),
                                "count": len(sel), "id": gid, "tournament": tid})
            elif t == "games.delete":
                before = len(games)
                games[:] = [g for g in games if g.get("id") != data.get("id")]
                if len(games) != before:
                    save_games()
                    await send(ws, {"type": "games_changed"})
            elif t == "preview.pgn":                              # intern.html: parse a pasted PGN -> positions for the clock preview
                try:
                    game = chess.pgn.read_game(io.StringIO(data.get("pgn", "") or ""))
                except Exception:
                    game = None
                if game is None:
                    await send(ws, {"type": "preview_pgn", "ok": False, "error": "couldn't read a game from that PGN"})
                else:
                    board = game.board()
                    plies = [{"san": "start", "fen": board.fen()}]
                    try:
                        for mv in game.mainline_moves():
                            san = board.san(mv)
                            uci = mv.uci()
                            board.push(mv)
                            plies.append({"san": san, "uci": uci, "fen": board.fen()})
                    except Exception:
                        pass
                    h = game.headers
                    await send(ws, {"type": "preview_pgn", "ok": True,
                                    "white": h.get("White", ""), "black": h.get("Black", ""),
                                    "result": h.get("Result", "*"), "fen": board.fen(), "plies": plies})
            elif t == "match.set":                                # Basic Setup / Tournament assigned players + format to a table
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    sess.match = data.get("match")
                    m = sess.match or {}
                    if not sess.moves and not sess.result:        # no game in progress -> adopt the assigned start position
                        sess.apply_match_position(m.get("start_fen"), m.get("variant") == "chess960")
                    await broadcast_state(sess)                   # new board + match -> clock; tables -> console; persists
                    await send(hub(sess.table_token)["clock"], {"type": "match", "match": sess.match})
            elif t == "admin.calib.lock":                         # console opened/closed its calibration modal -> block/unblock the clock's Calibrate
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    await send(hub(sess.table_token)["clock"], {"type": "calib.lock", "on": bool(data.get("on"))})
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
                cam = hub(s.table_token)["camera"]
                if getattr(s, "needs_anchor", False) and cam is not None and s.board_reader is not None:
                    s.needs_anchor = False                        # board was (re)assigned -> re-anchor the detector to the start before the first move
                    s.set_calib_step("refresh")
                    await send(cam, {"type": "capture.req"})
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
            elif t == "camera.moved":                             # camera's gyro felt movement -> nudge the console to re-grab the calibration frame
                if s is not None:
                    for a in list(admins):
                        await send(a, {"type": "camera.moved", "table": s.table_token})
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

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
import socket
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
from . import engine

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
# Obscured-board re-shoot: when the detector reports `move.unsettled` (a hand or object over the
# board), wait briefly and re-request a frame, up to RESHOOT_MAX times, before giving up.
RESHOOT_MAX, RESHOOT_DELAY = 4, 0.6
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
SETTINGS_FILE = os.environ.get("CHESSMON_SETTINGS",
                               os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "settings.json"))
ENGINES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines")
STOCKFISH_EXE = os.path.join(ENGINES_DIR, "stockfish.exe" if os.name == "nt" else "stockfish")   # bundled engine for suggested moves (installed from the console Setup page); .exe on Windows, no extension on macOS/Linux
FIDE_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fide.db")   # local FIDE rating-list index (SQLite), built by the console "Download FIDE list" action

VERSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "VERSION")   # repo-root VERSION (semver): shown in the console + checked against GitHub
try:
    APP_VERSION = open(VERSION_FILE, encoding="utf-8").read().strip()[:20] or "0.0.0"
except Exception:
    APP_VERSION = "0.0.0"
GITHUB_VERSION_URL = "https://raw.githubusercontent.com/DagHaugen/chessmon/main/VERSION"   # the latest published VERSION (default branch)
CLOUD_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cloud.json")   # chessmon-cloud relay config (gates web broadcast)

app = FastAPI(title="chessmon server")
mgr = SessionManager()
mgr.load(SESSIONS_FILE)            # resume calibrated sessions + games across a restart
conns: dict[str, dict] = {}        # table_token -> {clock, camera, spectators}
cam_status: dict = {}              # table_token -> last camera.status payload (flash/screen/zoom) so a console (re)join restores it
clock_state: dict = {}             # table_token -> last clock.tick (live white/black/active/running) so viewers mirror the device clock
suggest_state: dict = {}           # table_token -> last Stockfish suggestion (move + WDL) for the viewer overlay / late joiners
devices: dict[str, dict] = {}      # devId -> {id, name, userName, role, table, online, ws}
admins: set = set()                # server-console websockets
viewers: set = set()               # local-network viewers.html pages; fed the live tables while broadcast_local is on
watched: set = set()               # union of table tokens on a watch page / monitor tile -> the only games the engine analyses
watch_by_ws: dict = {}             # viewer ws -> set of tokens it is showing (watch page = 1, monitor = up to 4)
formats: dict = {}                 # format id -> {id, name, base_ms, increment_ms, inc_type, category, variant}
players: dict = {}                 # player id -> {id, name}
tournaments: dict = {}             # tournament id -> {id, name, format, players[], rounds[]}
games: list = []                   # append-only archive of finished games (moves + pgn + metadata)
settings: dict = {}                # global console prefs: time/date format, suggested-moves, broadcast (+ detected stockfish)
RTC_ROOM_FILE = os.environ.get("CHESSMON_RTC_ROOM_FILE",
                               os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rtc_room.txt"))


def load_or_create_rtc_room():
    """This club's globally-unique WebRTC signaling room, stable across restarts. RTC_ROOM env wins (manual
    override); else read/create rtc_room.txt at the repo root. The console QR and rtc_peer.py BOTH use this
    exact value, so two clubs never share a comlos.com signaling room (which would cross-connect them)."""
    env = (os.environ.get("RTC_ROOM") or "").strip()
    if env:
        return env
    try:
        with open(RTC_ROOM_FILE) as f:
            r = f.read().strip()
        if r:
            return r
    except OSError:
        pass
    r = "cm-" + os.urandom(6).hex()
    try:
        with open(RTC_ROOM_FILE, "w") as f:
            f.write(r)
    except OSError:
        pass
    return r


rtc_room: str = load_or_create_rtc_room()


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
    asyncio.create_task(suggestion_scheduler())   # owns the engine: analyses watched games, deepening + preempting


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


def _state_msg(s):
    """A 'state' snapshot carrying the Magnus squares (gated; null when off), so a reconnecting / refreshed
    clock gets them too -- snapshot() lives in game_session and can't see the app's settings."""
    return {"type": "state", **s.snapshot(),
            "magnus": _magnus_squares(s.game.board) if settings.get("magnus_mode") else None}


async def broadcast_state(s):
    snap = _state_msg(s)
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
    wake_suggest()                  # a move landed -> the suggestion scheduler re-reads this board (if it is watched)


# --- suggested-moves scheduler -------------------------------------------------------------
# One loop owns the engine. It analyses only WATCHED games (a watch page / monitor tile), one at
# a time, running a CONTINUOUS deepening search and pushing each refinement. A freshly-moved (or
# newly-watched) board jumps the queue for a quick first read; otherwise it keeps deepening the
# board it is on until something, anywhere, moves -- so one game deepens "forever", several share.
SUGGEST_CAP_DEPTH = 28             # deep enough; stop hogging the engine so other watched boards get a turn
sched_wake = asyncio.Event()
_analyzed: dict = {}               # token -> {"fen", "depth", "key"} of what we last looked at / pushed


def wake_suggest():
    sched_wake.set()


def recompute_watched():
    new = set()
    for toks in watch_by_ws.values():
        new |= toks
    if new != watched:
        watched.clear()
        watched.update(new)
        wake_suggest()


def _watched_sessions():
    out = []
    for t in list(watched):
        s = mgr.by_table(t)
        if s is not None and not s.result and (s.started_at or s.moves) and not s.game.board.is_game_over():
            out.append((t, s))
    return out


def _is_fresh(t, s):
    return _analyzed.get(t, {}).get("fen") != s.game.board.fen()


async def suggestion_scheduler():
    """Pick a watched board (fresh first, else the shallowest) and deepen it until it moves on, is
    unwatched, another board needs a first read, or it is deep enough -- then move to the next."""
    while True:
        try:
            on = settings.get("show_suggested_moves") and settings.get("broadcast_local") and is_stockfish_installed()
            sessions = _watched_sessions() if on else []
            target = None
            if sessions:
                fresh = [ts for ts in sessions if _is_fresh(*ts)]
                if fresh:
                    target = fresh[0]
                else:
                    deep = [ts for ts in sessions if _analyzed.get(ts[0], {}).get("depth", 0) < SUGGEST_CAP_DEPTH]
                    deep.sort(key=lambda ts: _analyzed.get(ts[0], {}).get("depth", 0))
                    target = deep[0] if deep else None
            if target is None:
                sched_wake.clear()
                await sched_wake.wait()
                continue
            await _deepen(*target)
        except Exception as e:
            print("[engine] scheduler:", e)
            await asyncio.sleep(0.5)


async def _deepen(token, sess):
    board = sess.game.board.copy()
    fen = board.fen()
    try:
        analysis = await asyncio.to_thread(engine.start, STOCKFISH_EXE, board)
    except Exception as e:
        print("[engine] start failed:", e)
        _analyzed[token] = {"fen": fen, "depth": SUGGEST_CAP_DEPTH, "key": None}   # mark settled so we don't spin on it
        return
    try:
        while True:
            await asyncio.sleep(0.4)
            if token not in watched or sess.result or sess.game.board.fen() != fen:
                break                                                  # unwatched / ended / a move landed
            if any(_is_fresh(t, s) for (t, s) in _watched_sessions() if t != token):
                break                                                  # a freshly-moved board elsewhere wants a first read
            sug = engine.read(analysis, board)
            if not sug or not sug.get("moves"):
                continue
            depth = sug.get("depth") or 0
            key = (tuple(m["san"] for m in sug["moves"]), tuple(sug.get("wdl_white") or ()))
            prev = _analyzed.get(token)
            _analyzed[token] = {"fen": fen, "depth": depth, "key": key}
            if not prev or prev.get("fen") != fen or prev.get("key") != key:
                msg = {"type": "suggest", "table": token, **sug}
                suggest_state[token] = msg
                for v in list(viewers):
                    await send(v, msg)
            if depth >= SUGGEST_CAP_DEPTH:
                break                                                  # deep enough -> release the engine for another board
    finally:
        await asyncio.to_thread(engine.stop, analysis)


def dev_public(d):
    return {k: d.get(k) for k in ("id", "name", "userName", "role", "table", "online", "screen", "cam", "plat", "battery")}


def _magnus_squares(board):
    """Where the two queenside ('b-file') knights are now -- replay the move stack from b1/b8, so 'Magnus
    mode' can keep just those knights mirrored as they roam. Returns square names; a captured one drops out.
    Off for Chess960 -- the b1/b8 start no longer holds, so the tracking would be meaningless."""
    if getattr(board, "chess960", False):
        return []
    wm, bm = chess.B1, chess.B8
    for mv in board.move_stack:
        if mv.to_square == wm:
            wm = None
        elif mv.to_square == bm:
            bm = None
        if mv.from_square == wm:
            wm = mv.to_square
        elif mv.from_square == bm:
            bm = mv.to_square
    return [chess.square_name(sq) for sq in (wm, bm) if sq is not None]


def tables_public():
    """The configured tables (each persists its unit assignments + name + calibration)."""
    mag = settings.get("magnus_mode")
    return [{"token": s.table_token, "name": s.name, "clock": s.clock_dev, "camera": s.camera_dev,
             "calibrated": s.board_reader is not None, "moved": getattr(s, "alignment_alert", False), "moves": len(s.moves),
             "san": [m.get("san", "") for m in s.moves], "fen": s.game.board.fen(), "match": getattr(s, "match", None),   # live board + match for the console
             "clock_white": (s.moves[-1].get("clock_white") if s.moves else None),   # last reported clocks -> viewers tick locally
             "clock_black": (s.moves[-1].get("clock_black") if s.moves else None),
             "started_at": s.started_at, "result": s.result, "termination": getattr(s, "termination", None),
             "status": getattr(s, "status", ""),
             "magnus": _magnus_squares(s.game.board) if mag else None}
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
            "tournaments": list(tournaments.values()), "rtc_room": rtc_room, "settings": settings}


async def broadcast_devices():
    save_devices()
    msg = admin_state()
    for ws in list(admins):
        await send(ws, msg)


def _lan_ip():
    """The server's LAN IPv4 for the viewers 'watch on a phone' QR -- Wi-Fi/Ethernet (192.168.*/10.*) preferred,
    matching the LAN address the console shows. Returns None on failure (the client then falls back to the page host)."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))   # sends no packets; just reveals the default-route IP
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass

    def _priv(ip):
        if ip.startswith("192.168.") or ip.startswith("10."):
            return True
        p = ip.split(".")
        return len(p) == 4 and p[0] == "172" and p[1].isdigit() and 16 <= int(p[1]) <= 31

    cand = sorted((ip for ip in ips if not ip.startswith("127.") and _priv(ip)),
                  key=lambda ip: (0 if ip.startswith(("192.168.", "10.")) else 1, ip))
    return cand[0] if cand else None


async def broadcast_tables():
    """Just the table list (live moves / started / result) -> the console, and to local viewers when broadcast_local is on."""
    msg = {"type": "tables", "tables": tables_public()}
    for ws in list(admins):
        await send(ws, msg)
    if settings.get("broadcast_local"):
        for ws in list(viewers):
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


DEFAULT_SETTINGS = {"time_format": "24h",   # "12h" -> "04:54 PM"          | "24h" -> "16:54"
                    "date_format": "iso",   # "iso" -> "2026-06-27 13:11"  | "long" -> "Jun 27 01:11 PM"
                    "room_name": "",         # friendly club/room name -> shown in admin instead of the cm- room id
                    "show_suggested_moves": False,
                    "broadcast_local": False, "broadcast_web": False,
                    "magnus_mode": False}                # easter egg -- mirror the queenside knights


def is_stockfish_installed():
    return os.path.exists(STOCKFISH_EXE)


def _sf_asset(rel):
    """Pick the best Stockfish release asset for THIS OS/CPU (best SIMD level first)."""
    import platform
    sysname = platform.system()
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sysname == "Windows":
        prefs = (["windows-armv8-dotprod", "windows-armv8"] if arm
                 else ["windows-x86-64-avx2", "windows-x86-64-sse41-popcnt", "windows-x86-64"])
    elif sysname == "Darwin":
        prefs = ["macos-m1-apple-silicon"] if arm else ["macos-x86-64-avx2", "macos-x86-64-sse41-popcnt", "macos-x86-64"]
    else:
        prefs = ["ubuntu-x86-64-avx2", "ubuntu-x86-64-sse41-popcnt", "ubuntu-x86-64"]
    for p in prefs:
        a = next((a for a in rel.get("assets", []) if p in a.get("name", "") and a.get("name", "").endswith((".zip", ".tar"))), None)
        if a:
            return a
    return None


def download_stockfish():
    """Fetch the latest Stockfish build for THIS platform into STOCKFISH_EXE (console "Get Stockfish":
    Windows .zip/.exe, macOS/Linux .tar). Blocking -> asyncio.to_thread. Returns (ok: bool, message: str)."""
    import urllib.request, zipfile, tarfile, tempfile, shutil, stat
    try:
        os.makedirs(ENGINES_DIR, exist_ok=True)
        req = urllib.request.Request("https://api.github.com/repos/official-stockfish/Stockfish/releases/latest",
                                     headers={"User-Agent": "chessmon", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            rel = json.load(r)
        asset = _sf_asset(rel)
        if not asset:
            return False, "no Stockfish build for this OS/CPU in the latest release"
        tmp = tempfile.mkdtemp(prefix="cm_sf_")
        try:
            apath = os.path.join(tmp, asset["name"])
            urllib.request.urlretrieve(asset["browser_download_url"], apath)
            if apath.endswith(".zip"):                                       # Windows: a .exe inside
                with zipfile.ZipFile(apath) as z:
                    name = next((n for n in z.namelist() if n.lower().endswith(".exe")), None)
                    if not name:
                        return False, "no .exe inside the download"
                    with z.open(name) as src, open(STOCKFISH_EXE, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            else:                                                            # macOS/Linux: the largest 'stockfish*' file in the tar
                with tarfile.open(apath) as t:
                    cands = [m for m in t.getmembers() if m.isfile() and os.path.basename(m.name).lower().startswith("stockfish")]
                    member = max(cands, key=lambda m: m.size) if cands else None
                    if member is None:
                        return False, "no stockfish binary inside the download"
                    with t.extractfile(member) as src, open(STOCKFISH_EXE, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            if os.name != "nt":                                              # Unix: mark it executable
                os.chmod(STOCKFISH_EXE, os.stat(STOCKFISH_EXE).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            return True, ("Stockfish " + str(rel.get("tag_name") or "").replace("sf_", "").lstrip("v") + " installed").strip()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        return False, str(e)


def is_fide_installed():
    return os.path.exists(FIDE_DB) and os.path.getsize(FIDE_DB) > 0


def fide_count():
    import sqlite3
    try:
        db = sqlite3.connect(FIDE_DB)
        row = db.execute("SELECT v FROM meta WHERE k='count'").fetchone()
        db.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def fide_date():
    import sqlite3
    try:
        db = sqlite3.connect(FIDE_DB)
        row = db.execute("SELECT v FROM meta WHERE k='date'").fetchone()
        db.close()
        return row[0] if row else ""
    except Exception:
        return ""


def _build_fide_db(xpath, dbpath):
    """Stream-parse the FIDE rating-list XML (~hundreds of MB) into a fresh SQLite index. Returns the count."""
    import sqlite3
    import xml.etree.ElementTree as ET
    if os.path.exists(dbpath):
        os.remove(dbpath)
    db = sqlite3.connect(dbpath)
    db.execute("CREATE TABLE p(fideid INTEGER PRIMARY KEY, name TEXT, lname TEXT, country TEXT, title TEXT,"
               " rating INTEGER, rapid INTEGER, blitz INTEGER, born INTEGER)")
    db.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")

    def num(s):
        s = (s or "").strip()
        return int(s) if s.isdigit() else None

    n, batch = 0, []
    for _ev, el in ET.iterparse(xpath, events=("end",)):
        if el.tag != "player":
            continue
        fid = num(el.findtext("fideid"))
        if fid is not None:
            name = (el.findtext("name") or "").strip()
            batch.append((fid, name, name.lower(), (el.findtext("country") or "").strip(),
                          (el.findtext("title") or "").strip(), num(el.findtext("rating")),
                          num(el.findtext("rapid_rating")), num(el.findtext("blitz_rating")), num(el.findtext("birthday"))))
            n += 1
            if len(batch) >= 5000:
                db.executemany("INSERT OR REPLACE INTO p VALUES(?,?,?,?,?,?,?,?,?)", batch)
                batch = []
        el.clear()
    if batch:
        db.executemany("INSERT OR REPLACE INTO p VALUES(?,?,?,?,?,?,?,?,?)", batch)
    db.execute("INSERT OR REPLACE INTO meta VALUES('count', ?)", (str(n),))
    db.execute("INSERT OR REPLACE INTO meta VALUES('date', ?)", (time.strftime("%Y-%m-%d"),))
    db.commit()
    db.close()
    return n


def download_fide():
    """Download the FIDE combined rating list (~47 MB) and rebuild the local SQLite index. Blocking ->
    asyncio.to_thread. Returns (ok, message); a 0-count parse is reported as an error (XML format changed)."""
    import urllib.request
    import zipfile
    import tempfile
    import shutil
    tmp = tempfile.mkdtemp(prefix="cm_fide_")
    try:
        zpath = os.path.join(tmp, "players.zip")
        urllib.request.urlretrieve("https://ratings.fide.com/download/players_list_xml.zip", zpath)
        with zipfile.ZipFile(zpath) as z:
            xname = next((nm for nm in z.namelist() if nm.lower().endswith(".xml")), None)
            if not xname:
                return False, "no .xml inside the FIDE download"
            z.extract(xname, tmp)
            xpath = os.path.join(tmp, xname)
        newdb = FIDE_DB + ".new"
        n = _build_fide_db(xpath, newdb)
        if n == 0:
            os.remove(newdb)
            return False, "parsed 0 players -- the FIDE XML format may have changed"
        os.replace(newdb, FIDE_DB)            # atomic swap over any previous index
        return True, str(n) + " players indexed"
    except Exception as e:
        return False, str(e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fide_lookup(q, limit=20):
    """Search the local FIDE index by FIDE ID (numeric) or name substring, ranked by rating. List of dicts."""
    import sqlite3
    q = (q or "").strip()
    if not q or not is_fide_installed():
        return []
    try:
        db = sqlite3.connect(FIDE_DB)
        db.row_factory = sqlite3.Row
        if q.isdigit():
            rows = db.execute("SELECT * FROM p WHERE fideid=?", (int(q),)).fetchall()
        else:
            rows = db.execute("SELECT * FROM p WHERE lname LIKE ? LIMIT 60", ("%" + q.lower() + "%",)).fetchall()
        db.close()
    except Exception:
        return []
    out = sorted((dict(r) for r in rows), key=lambda r: -(r.get("rating") or 0))
    return out[:limit]


def is_cloud_configured():
    try:
        with open(CLOUD_FILE) as f:
            c = json.load(f)
        return bool(c.get("url") and c.get("key"))
    except Exception:
        return False


def _vtuple(v):
    out = []
    for part in str(v or "").replace("-", ".").split("."):
        digits = "".join(c for c in part if c.isdigit())
        if digits:
            out.append(int(digits))
    return tuple(out)


def latest_version():
    """The VERSION published on the repo's main branch, for an 'update available?' check. None on any error."""
    import urllib.request
    try:
        req = urllib.request.Request(GITHUB_VERSION_URL, headers={"User-Agent": "chessmon"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.read().decode("utf-8").strip()[:20] or None
    except Exception:
        return None


def load_settings():
    settings.update(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE) as f:
            settings.update({k: v for k, v in json.load(f).items() if k in DEFAULT_SETTINGS})
    except Exception:
        pass
    settings["stockfish_installed"] = is_stockfish_installed()   # auto-detected each start, not persisted
    settings["cloud_configured"] = is_cloud_configured()         # web broadcast is offered only when chessmon-cloud is set up
    settings["fide_installed"] = is_fide_installed()             # local FIDE rating-list index present?
    settings["fide_count"] = fide_count()
    settings["fide_date"] = fide_date()                          # YYYY-MM-DD it was downloaded (FIDE refreshes the list monthly)
    settings["version"] = APP_VERSION                            # repo VERSION -> shown in the console Setup footer


def save_settings():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({k: settings.get(k) for k in DEFAULT_SETTINGS}, f, indent=1)
    except Exception:
        pass


load_devices()
load_formats()
load_players()
load_tournaments()
load_games()
load_settings()
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
                            await send(a, {"type": "snap.image", "table": s.table_token, "image": durl, "corners": s.corners, "t": (s.board_reader.t if s.board_reader is not None else 0)})
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
                    if verdict.get("type") == "move.unsettled":     # hand/object over the board -> re-shoot, don't guess a move
                        s._reshoots = getattr(s, "_reshoots", 0) + 1
                        if s._reshoots <= RESHOOT_MAX:
                            await send(hub(s.table_token)["clock"], {"type": "settling"})   # tell the clock it is re-grabbing -> show the hand
                            await asyncio.sleep(RESHOOT_DELAY)       # give the hand a moment to clear
                            await send(ws, {"type": "capture.req"})  # grab a fresh frame and try again
                            continue
                        s._reshoots = 0                              # gave up: board persistently blocked
                        verdict = {"type": "move.unclear", "reason": "board obscured - clear it and re-confirm", "squares": []}
                    else:
                        s._reshoots = 0                              # a clean read -> reset the re-shoot counter
                    await send(hub(s.table_token)["clock"], verdict)
                    await send(ws, verdict)                         # echo to the camera operator
                    if verdict.get("verify"):                      # low-confidence move shown -> double-check it with a fresh frame
                        s.set_calib_step("verifymove")             # next frame routes to verify_move
                        await broadcast_state(s)                    # show the move on the console while we re-check
                        await asyncio.sleep(RESHOOT_DELAY)          # let a reaching hand clear / the board settle
                        await send(ws, {"type": "capture.req"})
                        continue
                    # A warning / prompt verdict (illegal, no-change, ambiguous, unseen) did NOT change the
                    # board -- re-pushing 'state' to the clock would wipe the warning it just rendered (the
                    # "flash then back to green" bug). Refresh the console only (keeps the camera-moved badge
                    # live); anything that actually advanced the game broadcasts state as before.
                    if verdict.get("type") in ("move.unclear", "move.nochange", "move.ambiguous", "move.unseen", "started", "move.reverted"):   # 'started' / 'move.reverted' carry a warning/prompt -> a state re-push would wipe it
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
                await send(ws, _state_msg(s))  # restore view on reconnect (incl. Magnus squares)
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
                await send(ws, _state_msg(s))
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
                for st in cam_status.values():                    # restore each camera's flash/screen after a console refresh / (re)join
                    await send(ws, st)
            elif t == "viewer.join":                              # a local-network viewers.html page
                viewers.add(ws)
                await send(ws, {"type": "net", "lan_ip": _lan_ip()})   # so the 'watch on a phone' QR links to the LAN address, not localhost
                if settings.get("broadcast_local"):
                    await send(ws, {"type": "tables", "tables": tables_public()})
                    for cs in clock_state.values():               # mirror the live device clocks right away
                        await send(ws, cs)
                    if settings.get("show_suggested_moves"):
                        for sg in suggest_state.values():         # restore the current engine suggestions
                            await send(ws, sg)
                else:
                    await send(ws, {"type": "broadcast_off"})
            elif t == "watch":                                    # a viewers.html watch page -> the one game it is showing (null on the lobby)
                tok = data.get("table")
                watch_by_ws[ws] = {tok} if tok else set()
                recompute_watched()
                if tok and settings.get("show_suggested_moves") and tok in suggest_state:
                    await send(ws, suggest_state[tok])            # hand over the current suggestion right away
            elif t == "watch_set":                                # a monitor page -> the set of games on its tiles
                watch_by_ws[ws] = set(x for x in (data.get("tables") or []) if x)
                recompute_watched()
                if settings.get("show_suggested_moves"):
                    for x in watch_by_ws[ws]:
                        if x in suggest_state:
                            await send(ws, suggest_state[x])
            elif t == "device.rename":                            # console (or the device itself) set a user-defined name
                d = devices.get(data.get("devId"))
                if d is not None:
                    d["userName"] = data.get("userName", "")
                    await broadcast_devices()
                    await send(d.get("ws"), {"type": "name.updated", "userName": d["userName"]})  # reflect it on the device's landing page
            elif t == "device.remove":                            # console forgot a device (stale / phantom)
                if devices.pop(data.get("devId"), None) is not None:
                    await broadcast_devices()
            elif t == "settings.update":                          # console changed a global pref (time/date format, suggested-moves, broadcast)
                for k in ("time_format", "date_format", "room_name", "show_suggested_moves", "broadcast_local", "broadcast_web", "magnus_mode"):
                    if k in data:
                        settings[k] = data[k]
                settings["room_name"] = str(settings.get("room_name") or "").strip()[:60]   # keep the club name tidy
                save_settings()
                await broadcast_devices()                          # push the new settings to every console
                if "broadcast_local" in data:                     # toggled -> show or hide the live games on the viewer pages
                    push = {"type": "tables", "tables": tables_public()} if settings.get("broadcast_local") else {"type": "broadcast_off"}
                    for v in list(viewers):
                        await send(v, push)
                if "show_suggested_moves" in data:                # toggled -> push fresh suggestions, or tell viewers to drop the overlay
                    if settings.get("show_suggested_moves"):
                        wake_suggest()                            # the scheduler picks up the watched games
                    else:
                        suggest_state.clear()
                        _analyzed.clear()
                        for v in list(viewers):
                            await send(v, {"type": "suggest_off"})
                if "magnus_mode" in data and settings.get("broadcast_local"):   # toggled -> re-render the live boards with/without the knight flip
                    push = {"type": "tables", "tables": tables_public()}
                    for v in list(viewers):
                        await send(v, push)
                if "magnus_mode" in data:                              # the clocks mirror the knights on their own boards too -> refresh them
                    for sess2 in mgr._by_table.values():
                        await broadcast_state(sess2)
            elif t == "stockfish.install":                        # console "Get Stockfish" -> the server fetches the engine
                await send(ws, {"type": "stockfish.status", "state": "downloading"})
                ok, msg = await asyncio.to_thread(download_stockfish)
                settings["stockfish_installed"] = is_stockfish_installed()
                await send(ws, {"type": "stockfish.status", "state": ("done" if ok else "error"), "message": msg})
                await broadcast_devices()                          # push the refreshed stockfish_installed flag to every console
            elif t == "fide.install":                             # console "Download FIDE list" -> fetch + index the rating list
                await send(ws, {"type": "fide.status", "state": "downloading"})
                ok, msg = await asyncio.to_thread(download_fide)
                settings["fide_installed"] = is_fide_installed()
                settings["fide_count"] = fide_count()
                settings["fide_date"] = fide_date()
                await send(ws, {"type": "fide.status", "state": ("done" if ok else "error"), "message": msg})
                await broadcast_devices()
            elif t == "fide.lookup":                              # players page: search the FIDE index by id or name
                res = await asyncio.to_thread(fide_lookup, data.get("q", ""))
                await send(ws, {"type": "fide.results", "q": data.get("q", ""), "results": res})
            elif t == "version.check":                            # console: is a newer chessmon published on GitHub?
                latest = await asyncio.to_thread(latest_version)
                await send(ws, {"type": "version.result", "current": APP_VERSION, "latest": latest,
                                "update": bool(latest and _vtuple(latest) > _vtuple(APP_VERSION))})
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
                    if data.get("what") in ("flash", "screen"):   # cache the console's INTENT now so a refresh restores it even if the camera never reports status
                        st = cam_status.setdefault(sess.table_token, {"type": "camera.status", "table": sess.table_token,
                                                                      "flash": False, "screen": True, "flashAvail": True})
                        st[data.get("what")] = bool(data.get("on"))
                    await send(hub(sess.table_token)["camera"],
                               {"type": "camera.control", "what": data.get("what"), "on": bool(data.get("on")), "value": data.get("value")})
            elif t == "admin.watch":                              # console opens the live board for a running game
                sess = mgr.by_table(data.get("table"))
                if sess is not None:
                    hub(sess.table_token)["spectators"].add(ws)
                    await send(ws, _state_msg(sess))
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
                    await send(ws, {"type": "preview.image", "table": sess.table_token, "image": durl, "corners": sess.corners, "t": (sess.board_reader.t if sess.board_reader is not None else 0)})
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
            elif t == "games.pgn":                                # built PGN text for download: selected ids, one game (id), one tournament, or all
                gid, tid, ids = data.get("id"), data.get("tournament"), data.get("ids")
                if ids:
                    idset = set(ids); sel = [g for g in games if g.get("id") in idset]
                elif gid:
                    sel = [g for g in games if g.get("id") == gid]
                else:
                    sel = [g for g in games if tid is None or g.get("tournament") == tid]
                await send(ws, {"type": "games_pgn", "pgn": "\n\n".join(game_pgn(g) for g in sel),
                                "count": len(sel), "id": gid, "tournament": tid})
            elif t == "games.delete":                             # one id, or a list of ids (batch delete from the console)
                ids = set(data.get("ids") or ([data["id"]] if data.get("id") else []))
                before = len(games)
                games[:] = [g for g in games if g.get("id") not in ids]
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
            elif t == "clock.tick":                               # clock -> live clock state; cache it + relay to local viewers
                if s is not None:
                    msg = {"type": "clock.tick", "table": s.table_token,
                           "white_ms": data.get("white_ms"), "black_ms": data.get("black_ms"),
                           "active": data.get("active"), "running": bool(data.get("running"))}
                    clock_state[s.table_token] = msg
                    if settings.get("broadcast_local"):
                        for v in list(viewers):
                            await send(v, msg)
            elif t == "move.confirm":
                s.confirm(data["side"], data.get("clockWhite"), data.get("clockBlack"))
                await send(hub(s.table_token)["camera"], {"type": "capture.req"})
            elif t == "move.resolve":
                await send(hub(s.table_token)["clock"], s.resolve(data["uci"]))
                await broadcast_state(s)
            elif t == "move.cancel":                              # clock gave up on the guesses -> retry
                if s is not None:
                    s.revert_to_valid()                           # re-anchor to the last valid move (player sets the piece back)
                    await broadcast_tables()                      # console-only refresh; a full state re-push would restore the clock from the last move and roll the player's time back
            elif t == "flag":                                     # a clock hit 0 -> loss on time
                result = "1-0" if data.get("side") == "black" else "0-1"
                await send(hub(s.table_token)["clock"], s.end(result, "timeout"))
                await broadcast_state(s)
            elif t == "refresh":                                  # clock wants a fresh read (board moved?)
                s.set_calib_step("refresh")
                await send(hub(s.table_token)["camera"], {"type": "capture.req"})
            elif t == "setup.check":                              # clock pressed READY -> snapshot + re-baseline + verify the start position (no clock started yet)
                cam = hub(s.table_token)["camera"]
                if cam is not None and s.board_reader is not None:
                    s.needs_anchor = False
                    s.set_calib_step("startverify")
                    await send(cam, {"type": "capture.req"})
            elif t == "game.start":                               # clock pressed START (after a clean READY check) -> mark the game running
                s.mark_started()
                await broadcast_state(s)
            elif t == "game.status":                              # clock reports running / paused / waiting
                s.status = data.get("status", "")
                await broadcast_tables()
            elif t == "game.reset":                               # operator reset the pieces to the start
                s.reset_game()
                mgr.save(SESSIONS_FILE)
                await broadcast_state(s)                          # the clock re-enters 'ready' and auto-sends setup.check (re-baseline + verify) -> START shows directly if the board's already correct
            elif t == "game.undo":                                # take back the last (wrong) move
                if s.undo_move():
                    mgr.save(SESSIONS_FILE)
                    await broadcast_state(s)
                    cam = hub(s.table_token)["camera"]
                    if cam is not None and s.board_reader is not None:
                        s.set_calib_step("refresh")               # re-anchor to the reverted position
                        await send(cam, {"type": "capture.req"})
            elif t == "camera.controlled":                        # camera -> console: did the screen/torch control apply?
                if s is not None and data.get("what") in ("flash", "screen"):
                    st = cam_status.get(s.table_token)            # reconcile the cache with the applied control (mirrors the console's rule)
                    if st is not None:
                        if data.get("what") == "flash":
                            st["flash"] = bool(data.get("on")) if data.get("ok") else False
                        else:
                            st["screen"] = bool(data.get("on"))
                for a in list(admins):
                    await send(a, {"type": "camera.controlled", "table": s.table_token,
                                   "what": data.get("what"), "on": bool(data.get("on")),
                                   "ok": bool(data.get("ok")), "reason": data.get("reason", "")})
            elif t == "camera.status":                            # camera -> console: its actual flash/screen state on connect/link
                if s is not None:
                    msg = {"type": "camera.status", "table": s.table_token,
                           "flash": bool(data.get("flash")),
                           "flashAvail": bool(data.get("flashAvail", True)),
                           "screen": bool(data.get("screen", True)),
                           "zoom": data.get("zoom"),
                           "zoomMin": data.get("zoomMin"),
                           "zoomMax": data.get("zoomMax")}
                    cam_status[s.table_token] = msg               # cache so a console refresh / (re)join restores it
                    for a in list(admins):
                        await send(a, msg)
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
        viewers.discard(ws)
        if watch_by_ws.pop(ws, None):
            recompute_watched()
        for hb in conns.values():
            hb["spectators"].discard(ws)                # an admin that was watching a board
        if dev_id in devices and devices[dev_id].get("ws") is ws:
            devices[dev_id]["online"] = False
            devices[dev_id]["ws"] = None
            await broadcast_devices()


app.mount("/app", StaticFiles(directory=WEB, html=True), name="app")    # the clock PWA

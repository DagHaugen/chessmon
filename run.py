#!/usr/bin/env python3
"""chessmon launcher -- one command to run (and stop) the club's stack.

The stack is two local processes:
  * the chessmon SERVER  -- FastAPI/uvicorn, plain HTTP on :8000 (the operator's console)
  * the WebRTC BRIDGE    -- webrtc/rtc_peer.py, which connects phones through comlos.com
                           (no certificate, no inbound ports). Kept alive + respawned here.

Usage (via chessmon.bat on Windows, ./chessmon on macOS/Linux):
    chessmon setup          create .venv + install dependencies (run once after download)
    chessmon                run server + bridge in this window; Ctrl+C stops both
    chessmon start -d       run in the background (writes a pidfile + chessmon.log)
    chessmon stop           stop a backgrounded instance (server + bridge)
    chessmon restart
    chessmon status         server up? port, bridge, Stockfish, cloud
    chessmon --no-bridge    server only (pure-local / simulator, no phones)

run.py uses only the standard library and starts the venv Python for the real work, so it
runs fine under any Python -- the wrapper picks the venv once setup has created it.
"""
import argparse
import glob
import json
import os
import signal
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
IS_WIN = os.name == "nt"
VENV_PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe") if IS_WIN \
    else os.path.join(ROOT, ".venv", "bin", "python")
RUN_DIR = os.path.join(ROOT, ".run")
STATE = os.path.join(RUN_DIR, "chessmon.json")          # {supervisor, server, bridge, port}
LOG = os.path.join(ROOT, "chessmon.log")
DEFAULT_PORT = int(os.environ.get("CHESSMON_PORT", "8000"))
SIGNAL_BROKER = "https://comlos.com/relay/signal.php"   # comlos WebRTC signaling (keyless, room-based)


def say(m=""):
    print(m, flush=True)


# ---- environment ------------------------------------------------------------
def cmd_setup(_args=None):
    """Create .venv (with whatever Python is running this) and install every dependency."""
    if not os.path.exists(VENV_PY):
        say("creating .venv ...")
        subprocess.check_call([sys.executable, "-m", "venv", os.path.join(ROOT, ".venv")])
    pip = [VENV_PY, "-m", "pip"]
    subprocess.check_call(pip + ["install", "--quiet", "--upgrade", "pip"])
    say("installing dependencies (a minute or two the first time) ...")
    for req in ("requirements.txt", os.path.join("server", "requirements.txt")):
        subprocess.check_call(pip + ["install", "--quiet", "-r", os.path.join(ROOT, req)])
    subprocess.check_call(pip + ["install", "--quiet", "websockets", "aiortc"])  # WebRTC bridge deps
    say("\n[ok] ready. Start it with:  chessmon")


def _ensure_venv():
    if not os.path.exists(VENV_PY):
        say("first run -> setting up the environment ...")
        cmd_setup()


# ---- small cross-platform process helpers -----------------------------------
def _port_open(port):
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _alive(pid):
    if not pid:
        return False
    if IS_WIN:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill(pid):
    if not _alive(pid):
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _read_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(d):
    os.makedirs(RUN_DIR, exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(d, f)


def _find_stockfish():
    return next((p for p in glob.glob(os.path.join(ROOT, "server", "engines", "stockfish*"))
                 if os.path.isfile(p)), None)


# ---- spawning ---------------------------------------------------------------
def _spawn_kwargs():
    # Children get their own group/session so a console Ctrl+C only reaches the supervisor;
    # we then stop them ourselves, cleanly and in order.
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WIN \
        else {"start_new_session": True}


def _spawn_server(port, out):
    return subprocess.Popen(
        [VENV_PY, "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", str(port)],
        cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, **_spawn_kwargs())


def _spawn_bridge(port, out):
    env = dict(os.environ)
    env.setdefault("RTC_BROKER", SIGNAL_BROKER)
    env["RTC_TARGET"] = f"ws://localhost:{port}/ws"           # bridge each device channel to the local server
    return subprocess.Popen(
        [VENV_PY, os.path.join("webrtc", "rtc_peer.py")],
        cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, env=env, **_spawn_kwargs())


# ---- commands ---------------------------------------------------------------
def cmd_start(args):
    _ensure_venv()
    port = args.port
    if _port_open(port):
        say(f"chessmon already running on :{port}   (use  chessmon stop  first)")
        return

    if args.detach:                                          # relaunch ourselves in the background
        os.makedirs(RUN_DIR, exist_ok=True)
        logf = open(LOG, "ab")
        flags = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} \
            if IS_WIN else {"start_new_session": True}
        sub = [VENV_PY, os.path.abspath(__file__), "start", "--port", str(port)]
        if args.no_bridge:
            sub.append("--no-bridge")
        subprocess.Popen(sub, cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, **flags)
        time.sleep(2.0)
        say(f"chessmon started in the background{'' if _port_open(port) else ' (still coming up)'}.")
        say(f"  console : http://localhost:{port}/app/admin.html")
        say(f"  logs    : {LOG}")
        say(f"  stop    : chessmon stop")
        return

    # foreground: inherit stdout so server + bridge logs share this window
    server = _spawn_server(port, None)
    bridge = None if args.no_bridge else _spawn_bridge(port, None)
    _write_state({"supervisor": os.getpid(), "server": server.pid,
                  "bridge": bridge.pid if bridge else None, "port": port})
    say(f"chessmon server  ->  http://localhost:{port}/app/admin.html")
    say("  (other LAN machines: use this PC's IP, e.g. http://192.168.x.x:%d/app/admin.html)" % port)
    if bridge:
        say("WebRTC bridge    ->  comlos.com  (phones connect by scanning the console QR)")
    say("Ctrl+C to stop.\n")
    _supervise(server, bridge, port)


def _supervise(server, bridge, port):
    stop = {"v": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(v=True))
    if not IS_WIN:
        signal.signal(signal.SIGTERM, lambda *_: stop.update(v=True))
    try:
        while not stop["v"]:
            if server.poll() is not None:
                say("server exited -- shutting down.")
                break
            if bridge is not None and bridge.poll() is not None and not stop["v"]:
                say("bridge dropped -- restarting in 3s ...")
                for _ in range(6):
                    if stop["v"]:
                        break
                    time.sleep(0.5)
                if stop["v"]:
                    break
                bridge = _spawn_bridge(port, None)
                st = _read_state()
                st["bridge"] = bridge.pid
                _write_state(st)
            time.sleep(0.5)
    finally:
        _shutdown(server, bridge)


def _shutdown(server, bridge):
    for p in (bridge, server):                              # bridge first, then the server
        if p and p.poll() is None:
            _kill(p.pid)
    for p in (bridge, server):
        if p:
            try:
                p.wait(timeout=6)
            except Exception:
                pass
    try:
        os.remove(STATE)
    except OSError:
        pass
    say("stopped.")


def cmd_stop(_args=None):
    st = _read_state()
    pids = [st.get("supervisor"), st.get("bridge"), st.get("server")]
    had = any(_alive(p) for p in pids if p)
    for p in pids:                                          # supervisor first so it can't respawn the bridge
        _kill(p)
    try:
        os.remove(STATE)
    except OSError:
        pass
    say("stopped." if had else "nothing was running.")


def cmd_restart(args):
    cmd_stop()
    time.sleep(1.0)
    cmd_start(args)


def cmd_status(args):
    st = _read_state()
    port = st.get("port", args.port)
    say(f"server    : {'UP on :' + str(port) if _port_open(port) else 'down'}")
    say(f"supervisor: {'running (pid ' + str(st.get('supervisor')) + ')' if _alive(st.get('supervisor')) else '-'}")
    say(f"bridge    : {'running' if _alive(st.get('bridge')) else '-'}")
    say(f"stockfish : {'present' if _find_stockfish() else 'not installed (get it from the console Setup page)'}")
    say(f"cloud     : {'configured' if os.path.exists(os.path.join(ROOT, 'cloud.json')) else 'not configured'}")


def main():
    p = argparse.ArgumentParser(prog="chessmon", description="Run the chessmon server + WebRTC bridge.")
    p.add_argument("command", nargs="?", default="start",
                   choices=["start", "stop", "restart", "status", "setup"])
    p.add_argument("-d", "--detach", action="store_true", help="run in the background")
    p.add_argument("--no-bridge", action="store_true", help="server only (no phones)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = p.parse_args()
    {"setup": lambda a: cmd_setup(),
     "start": cmd_start,
     "stop": lambda a: cmd_stop(),
     "restart": cmd_restart,
     "status": cmd_status}[args.command](args)


if __name__ == "__main__":
    main()

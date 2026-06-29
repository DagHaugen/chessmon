"""chessmon WebRTC peer — the club's LOCAL-SERVER side. Polls the comlos.com signaling endpoint for
device offers and answers each one, opening a WebRTC data channel straight to the device (peer-to-peer).
Run this on the club PC; it only needs OUTBOUND access to comlos.com (no inbound, no local certificate).

    set RTC_BROKER=https://comlos.com/relay/signal.php
    set RTC_ROOM=<your club/room code>
    set RTC_TARGET=ws://localhost:8000/ws        # the local chessmon server (plain HTTP)
    .venv\\Scripts\\python webrtc\\rtc_peer.py        # (normally started by  chessmon , which sets the vars above)

With RTC_TARGET set, every data channel is bridged to a fresh WebSocket on the chessmon server, so the
device speaks the normal chessmon protocol (hello / table.join / capture.req / move.result ...) over
WebRTC and the server's handlers are untouched. Each message (incl. a camera JPEG) is one data-channel
message both ways. Without RTC_TARGET the peer just echoes (phase 1b).
"""
import asyncio
import json
import os
import ssl
import urllib.request

from aiortc import RTCPeerConnection, RTCSessionDescription

# Skip the browser's mDNS (.local) ICE candidates instead of resolving them: aiortc's mDNS resolve is
# slow when a VPN is up and fails ("unreachable host") when it's down. The device still connects to OUR
# host candidate, and ICE peer-reflexive discovery learns its real address from that incoming check.
import aioice.ice as _aii
import aioice.mdns as _amd
_orig_add_remote = _aii.Connection.add_remote_candidate
async def _add_remote_skip_mdns(self, cand):
    if cand is not None and _amd.is_mdns_hostname(cand.host):
        return
    return await _orig_add_remote(self, cand)
_aii.Connection.add_remote_candidate = _add_remote_skip_mdns

def _load_or_create_room():
    """The club's signaling room. RTC_ROOM env wins (manual override); else share rtc_room.txt at the repo
    root with the chessmon server (the server writes it on startup; if the bridge runs first it creates the
    same file). A globally-unique, stable room per club — so two clubs never collide on comlos.com signaling
    (a shared room would let one club's device answer into the other club's server)."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rtc_room.txt")
    try:
        with open(path) as f:
            r = f.read().strip()
        if r:
            return r
    except OSError:
        pass
    r = "cm-" + os.urandom(6).hex()
    try:
        with open(path, "w") as f:
            f.write(r)
    except OSError:
        pass
    return r


BROKER = os.environ.get("RTC_BROKER", "https://comlos.com/relay/signal.php")
ROOM = (os.environ.get("RTC_ROOM") or "").strip() or _load_or_create_room()
TARGET = os.environ.get("RTC_TARGET", "")          # if set, bridge each channel to this WS (the chessmon server)
EXCLUDE = [p.strip() for p in os.environ.get("RTC_EXCLUDE", "").split(",") if p.strip()]  # optional manual override of IP prefixes to drop


def _physical_ipv4():
    """IPv4 addresses on PHYSICAL adapters (auto-excludes VPN/virtual adapters of ANY IP range), via Windows
    Get-NetAdapter -Physical. Returns [] on non-Windows / any failure -> then no auto-filtering is applied."""
    ps = ("$i=(Get-NetAdapter -Physical -EA SilentlyContinue | Where-Object Status -eq 'Up').ifIndex;"
          "Get-NetIPAddress -AddressFamily IPv4 -EA SilentlyContinue | "
          "Where-Object {$_.InterfaceIndex -in $i -and $_.IPAddress -notmatch '^(127\\.|169\\.254\\.)'} | "
          "Select-Object -Expand IPAddress")
    try:
        import subprocess
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=10)
        return [s.strip() for s in r.stdout.splitlines() if s.strip()]
    except Exception:
        return []


# Gather ICE host candidates only on physical-adapter IPv4s, so a VPN/virtual adapter (whatever its range)
# is auto-excluded and the phone never tries an address it can't reach. RTC_EXCLUDE still works as a manual
# override; IPv6 is left untouched.
_PHYS = _physical_ipv4()
print("bridge: gather IPs ->", (_PHYS or "all (physical detect failed)"), flush=True)
_orig_get_host = _aii.get_host_addresses
def _get_host_filtered(use_ipv4, use_ipv6):
    keep = []
    for a in _orig_get_host(use_ipv4, use_ipv6):
        if ":" not in a:                                  # filter IPv4 only
            if EXCLUDE and any(a.startswith(b) for b in EXCLUDE):
                continue
            if _PHYS and a not in _PHYS:
                continue
        keep.append(a)
    return keep
_aii.get_host_addresses = _get_host_filtered

pcs = set()


def _bridge(channel):
    """Pipe a device's data channel <-> a fresh WebSocket to the chessmon server. Early channel messages
    are buffered until the WS is up; each message (text or one-shot binary frame) passes straight through."""
    import websockets
    state = {"ws": None}
    buf = []

    @channel.on("message")
    def _to_ws(msg):
        if state["ws"] is not None:
            asyncio.ensure_future(state["ws"].send(msg))
        else:
            buf.append(msg)

    @channel.on("close")
    def _closed():
        if state["ws"] is not None:
            asyncio.ensure_future(state["ws"].close())

    async def _run():
        try:
            kw = {}
            if TARGET.startswith("wss"):                  # a wss target would be a self-signed localhost cert -> skip verification
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                kw["ssl"] = ctx
            ws = await websockets.connect(TARGET, max_size=None, **kw)
        except Exception as e:
            print("bridge: can't reach", TARGET, ":", e)
            channel.close()
            return
        state["ws"] = ws
        for m in buf:                              # flush anything the device sent before the WS was up
            asyncio.ensure_future(ws.send(m))
        buf.clear()
        try:
            async for m in ws:                     # server -> device
                channel.send(m)
        except Exception:
            pass
        finally:
            await ws.close()

    asyncio.ensure_future(_run())


async def answer(session, sdp, loop):
    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def _state():
        print("  session", session, "->", pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)

    @pc.on("datachannel")
    def _dc(channel):
        if TARGET:
            _bridge(channel)                       # phase 2: relay to the chessmon server
        else:
            @channel.on("message")                 # phase 1b: echo
            def _msg(message):
                channel.send("echo: " + (message if isinstance(message, str) else message.decode("utf-8", "replace")))

    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
    await pc.setLocalDescription(await pc.createAnswer())
    ans = pc.localDescription.sdp
    if EXCLUDE:                                       # strip candidates on excluded IPs (a VPN) so the device doesn't wait on dead paths
        ans = "\r\n".join(l for l in ans.splitlines()
                          if not (l.startswith("a=candidate:") and any(b in l for b in EXCLUDE))) + "\r\n"
    payload = json.dumps({"room": ROOM, "session": session, "kind": "answer", "sdp": ans}).encode()

    def _post():
        req = urllib.request.Request(BROKER, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()

    await loop.run_in_executor(None, _post)
    print("answered", session, "(bridge)" if TARGET else "(echo)")


async def main():
    loop = asyncio.get_event_loop()
    print("chessmon peer polling", BROKER, "room", ROOM, "| target:", TARGET or "echo")
    while True:
        try:
            def _get():
                return json.loads(urllib.request.urlopen(
                    BROKER + "?room=" + ROOM + "&kind=offer", timeout=10).read())
            for off in (await loop.run_in_executor(None, _get)).get("offers", []):
                try:
                    await answer(off["session"], off["sdp"], loop)
                except Exception as e:
                    print("answer error", off.get("session"), ":", e)
        except Exception as e:
            print("poll error:", e)
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())

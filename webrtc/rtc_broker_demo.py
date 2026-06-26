"""WebRTC phase 1b — handshake brokered through a POLLED signaling endpoint (the comlos.com role).

In 1a the browser POSTed its offer and got the answer in the same response. Here the offer/answer are
relayed through a broker that neither side can be pushed from — exactly what comlos.com (PHP + MariaDB,
polling) is. This one process plays BOTH roles for a local test: it hosts the broker (the /signal store,
in-memory here; MariaDB in signal.php) AND runs the local-server peer that POLLS the broker for offers
and answers them. The browser (rtc.html) signals ONLY through /signal — never directly to the peer; the
data channel itself is peer-to-peer.

    python webrtc\\rtc_broker_demo.py      ->  http://localhost:8091/
"""
import asyncio
import json
import os
import urllib.request

import uvicorn
from aiortc import RTCPeerConnection, RTCSessionDescription
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOM = os.environ.get("RTC_ROOM", "demo")                       # the club/room code a device joins
BROKER = os.environ.get("RTC_BROKER", "http://localhost:8091")  # in production: https://comlos.com/relay (signal.php)
app = FastAPI()
OFFERS = {}    # room -> [ {session, sdp} ]   — offers the peer hasn't answered yet
ANSWERS = {}   # "room:session" -> sdp        — answers the browser hasn't picked up yet
pcs = set()


# ---- BROKER routes (what signal.php becomes on comlos.com) -----------------
@app.get("/")
async def index():
    return FileResponse(os.path.join(HERE, "rtc.html"))


@app.post("/signal")
async def post_signal(req: Request):
    m = await req.json()
    if m.get("kind") == "offer":
        OFFERS.setdefault(m["room"], []).append({"session": m["session"], "sdp": m["sdp"]})
    elif m.get("kind") == "answer":
        ANSWERS[m["room"] + ":" + m["session"]] = m["sdp"]
    return JSONResponse({"ok": True})


@app.get("/signal")
async def get_signal(room: str, kind: str, session: str = ""):
    if kind == "offer":
        return JSONResponse({"offers": OFFERS.pop(room, [])})            # consume-on-read
    return JSONResponse({"sdp": ANSWERS.pop(room + ":" + session, None)})


# ---- LOCAL-SERVER peer: poll the broker, answer offers ---------------------
async def answer(session, sdp, loop):
    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("datachannel")
    def _dc(channel):
        @channel.on("message")
        def _m(msg):
            channel.send("echo: " + (msg if isinstance(msg, str) else msg.decode("utf-8", "replace")))

    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
    await pc.setLocalDescription(await pc.createAnswer())
    payload = json.dumps({"room": ROOM, "session": session, "kind": "answer",
                          "sdp": pc.localDescription.sdp}).encode()

    def _post():
        req = urllib.request.Request(BROKER + "/signal", data=payload,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()

    await loop.run_in_executor(None, _post)
    print("answered session", session)


async def poller():
    loop = asyncio.get_event_loop()
    print("peer polling", BROKER, "for offers in room", ROOM)
    while True:
        try:
            def _get():
                return json.loads(urllib.request.urlopen(
                    BROKER + "/signal?room=" + ROOM + "&kind=offer", timeout=5).read())
            for off in (await loop.run_in_executor(None, _get)).get("offers", []):
                try:
                    await answer(off["session"], off["sdp"], loop)
                except Exception as e:
                    print("answer error for", off.get("session"), ":", e)   # one bad offer must not drop the rest
        except Exception as e:
            print("poll error:", e)
        await asyncio.sleep(1)


@app.on_event("startup")
async def _startup():
    if not os.environ.get("RTC_NO_PEER"):       # RTC_NO_PEER=1 -> broker only (for testing a standalone peer/bridge)
        asyncio.create_task(poller())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8091)

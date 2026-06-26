"""chessmon WebRTC peer — the club's LOCAL-SERVER side. Polls the comlos.com signaling endpoint for
device offers and answers each one, opening a WebRTC data channel straight to the device (peer-to-peer).
Run this on the club PC; it only needs OUTBOUND access to comlos.com (no inbound, no local certificate).

    set RTC_BROKER=https://comlos.com/relay/signal.php
    set RTC_ROOM=<your club/room code>
    chessmon-webrtc\\.venv\\Scripts\\python webrtc\\rtc_peer.py

Phase 1b still just echoes over the channel; phase 2 routes the messages into the real chessmon handlers.
"""
import asyncio
import json
import os
import urllib.request

from aiortc import RTCPeerConnection, RTCSessionDescription

BROKER = os.environ.get("RTC_BROKER", "https://comlos.com/relay/signal.php")
ROOM = os.environ.get("RTC_ROOM", "demo")
pcs = set()


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
        @channel.on("message")
        def _msg(message):
            channel.send("echo: " + (message if isinstance(message, str) else message.decode("utf-8", "replace")))

    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
    await pc.setLocalDescription(await pc.createAnswer())
    payload = json.dumps({"room": ROOM, "session": session, "kind": "answer",
                          "sdp": pc.localDescription.sdp}).encode()

    def _post():
        req = urllib.request.Request(BROKER, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()

    await loop.run_in_executor(None, _post)
    print("answered", session)


async def main():
    loop = asyncio.get_event_loop()
    print("chessmon peer polling", BROKER, "for offers in room", ROOM)
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
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())

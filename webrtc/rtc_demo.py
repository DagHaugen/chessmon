"""WebRTC phase 1a — prove an aiortc data channel to a browser.

Throwaway demo: FastAPI serves the test page + a /offer signaling endpoint; aiortc answers, opens the
data channel and echoes whatever the browser sends. Non-trickle ICE — the answer SDP already carries the
host candidates, so no separate candidate exchange. No comlos.com and no certificate yet; that's phase 1b
(move the offer/answer swap onto a comlos.com PHP endpoint so the page can load from the real domain).

    chessmon-webrtc\\.venv\\Scripts\\python webrtc\\rtc_demo.py      ->  http://localhost:8090/
"""
import os

import uvicorn
from aiortc import RTCPeerConnection, RTCSessionDescription
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI()
pcs = set()


@app.get("/")
async def index():
    return FileResponse(os.path.join(HERE, "index.html"))


@app.post("/offer")
async def offer(req: Request):
    params = await req.json()
    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def _state():
        print("peer:", pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            pcs.discard(pc)

    @pc.on("datachannel")
    def _dc(channel):
        print("data channel:", channel.label)

        @channel.on("message")
        def _msg(message):
            text = message if isinstance(message, str) else message.decode("utf-8", "replace")
            print("recv:", text)
            channel.send("echo: " + text)          # phase 1a just echoes; phase 2 routes to the real handlers

    await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    await pc.setLocalDescription(await pc.createAnswer())   # aiortc gathers ICE here -> localDescription carries the candidates
    return JSONResponse({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)

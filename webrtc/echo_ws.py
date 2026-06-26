"""Tiny echo WebSocket server — a stand-in for the chessmon server while testing the rtc_peer.py bridge.
Echoes each message back with a 'ws-echo:' prefix so a round-trip through the bridge is visible.

    chessmon-webrtc\\.venv\\Scripts\\python webrtc\\echo_ws.py      # ws://localhost:8000
"""
import asyncio

import websockets


async def echo(ws):
    async for m in ws:
        await ws.send("ws-echo: " + (m if isinstance(m, str) else m.decode("utf-8", "replace")))


async def main():
    async with websockets.serve(echo, "localhost", 8000):
        print("echo ws on ws://localhost:8000")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())

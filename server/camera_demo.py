"""Camera-path demo: push a REAL board image through the server over a WebSocket (in-process
TestClient, send_bytes) and watch chessmon register it — proving the camera client's frame
path end to end without the BRIO. Run:  python server/camera_demo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from server.app import app

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")


def main():
    empty = os.path.join(OUT, "empty.png")
    if not os.path.exists(empty):
        print("no out/empty.png available to demo with — run on a machine with a captured board")
        return
    client = TestClient(app)
    pair = client.post("/tables", json={"white": "Ada", "black": "Bob"}).json()["pairToken"]
    with client.websocket_connect("/ws") as cam:
        cam.send_json({"type": "pair.join", "pairToken": pair})
        print(f"pair.join              -> {cam.receive_json()['type']}")
        cam.send_json({"type": "calib", "step": "empty"})
        cam.send_bytes(open(empty, "rb").read())                  # a real empty-board frame
        print(f"empty-board frame      -> {cam.receive_json()}")
    print("(a real JPEG decoded + registered by chessmon, over the WebSocket)")


if __name__ == "__main__":
    main()

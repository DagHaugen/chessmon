"""Hardware-free check of the two new wire behaviours: camera-join `calibrated`, and flag->game.end."""
import sys

sys.path.insert(0, r"C:\Claude\Projects\chessmon")
from fastapi.testclient import TestClient  # noqa: E402

from server.app import app  # noqa: E402

c = TestClient(app)
r = c.post("/tables", json={"white": "W", "black": "B"}).json()
tok, pair = r["tableToken"], r["pairToken"]

with c.websocket_connect("/ws") as clock:
    clock.send_json({"type": "table.join", "tableToken": tok})
    clock.receive_json()  # session.ready
    clock.receive_json()  # state
    with c.websocket_connect("/ws") as cam:
        cam.send_json({"type": "pair.join", "pairToken": pair})
        cready = cam.receive_json()
    assert cready.get("calibrated") is False, cready
    print("camera join -> calibrated:", cready.get("calibrated"), "OK")

    clock.send_json({"type": "flag", "side": "white"})       # white runs out -> black wins
    end = clock.receive_json()
    assert end == {"type": "game.end", "result": "0-1", "pgn": "0-1"}, end
    print("flag white  ->", end["type"], end["result"], "OK")

with c.websocket_connect("/ws") as clock:                     # black flags -> white wins
    clock.send_json({"type": "table.join", "tableToken": tok})
    clock.receive_json(); clock.receive_json()
    clock.send_json({"type": "flag", "side": "black"})
    end = clock.receive_json()
    assert end["result"] == "1-0", end
    print("flag black  ->", end["type"], end["result"], "OK")
print("WS VERIFY PASSED")

"""End-to-end demo: drive a whole game through the server over real WebSockets, with a
clock client and a camera client, no hardware (FastAPI in-process TestClient). The camera
reports occupancy grids in place of JPEG frames via the {type:grid} dev message.

    python server/demo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chess
from fastapi.testclient import TestClient

from server.app import app
from chessmon.board_state import board_to_grid, square_to_rc, Cell


def recv_verdict(ws):
    """Next non-state message the clock receives (skip the state broadcasts)."""
    while True:
        m = ws.receive_json()
        if m.get("type") != "state":
            return m


def main():
    client = TestClient(app)
    r = client.post("/tables", json={"white": "Ada", "black": "Bob"})
    tok = r.json()["tableToken"]
    print(f"POST /tables            -> table {tok}")
    with client.websocket_connect("/ws") as clock, client.websocket_connect("/ws") as camera:
        clock.send_json({"type": "table.join", "tableToken": tok})
        ready = clock.receive_json()
        print(f"clock  table.join       -> {ready['type']} (pairToken {ready['pairToken'][:6]}...)")
        camera.send_json({"type": "pair.join", "pairToken": ready["pairToken"]})
        print(f"camera pair.join        -> {camera.receive_json()['type']}  [paired]")

        board = chess.Board()
        camera.send_json({"type": "grid", "grid": board_to_grid(board).tolist()})
        print(f"camera baseline grid    -> {recv_verdict(clock)['type']}\n")

        for uci in ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]:
            side = "white" if board.turn else "black"
            board.push_uci(uci)
            clock.send_json({"type": "move.confirm", "side": side,
                             "clockWhite": 300, "clockBlack": 300})
            assert camera.receive_json()["type"] == "capture.req"
            camera.send_json({"type": "grid", "grid": board_to_grid(board).tolist()})
            v = recv_verdict(clock)
            print(f"confirm+frame ({uci}) -> {v['type']}: {v['san']}  ({v['turn']} to move)")

        obs = board_to_grid(board)
        obs[square_to_rc(chess.E1)] = Cell.EMPTY
        obs[square_to_rc(chess.E8)] = Cell.EMPTY
        obs[square_to_rc(chess.D5)] = Cell.DARK
        camera.send_json({"type": "grid", "grid": obs.tolist()})
        end = recv_verdict(clock)
        print(f"\nkings to centre         -> {end['type']}: {end['result']}")

    print("\nfinal scoresheet (GET /tables/{token}/state):")
    print(client.get(f"/tables/{tok}/state").json()["pgn"])


if __name__ == "__main__":
    main()

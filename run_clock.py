"""Path-independent launcher for the clock device server.

Used by .claude/launch.json (and handy on its own). Starts at a near-promotion
position so the promotion picker can be demonstrated immediately:

    python run_clock.py [port]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chessmon.clock_server import run_server

PROMO_FEN = "r3k2r/pP3ppp/8/8/8/8/5PPP/R3K2R w KQkq - 0 1"

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    run_server(port=port, base_seconds=300.0, increment=3.0, start_fen=PROMO_FEN)

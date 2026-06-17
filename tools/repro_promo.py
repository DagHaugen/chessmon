import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chessmon.clock_server import ClockGame

FEN = "r3k2r/pP3ppp/8/8/8/8/5PPP/R3K2R w KQkq - 0 1"
for key in ["q", "r", "b", "n"]:
    g = ClockGame(start_fen=FEN)
    g.make_move("b7b8q")
    g.confirm()
    g.promote(key)
    print(f"promote({key!r}) -> {g.history}")

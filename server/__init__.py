"""chessmon server — FastAPI + WebSocket core that pairs a clock and a camera unit and
drives a game through the chessmon vision engine. `game_session` holds the pure, testable
logic; `app` is the thin socket layer that speaks the wire protocol."""

# chessmon server

FastAPI + WebSocket core that pairs a **clock unit** and a **camera unit** and drives a game
through the `chessmon` vision engine. This is the option‑1 MVP / the spine of the
[ecosystem plan](../) — vision runs **server‑side**, the camera unit just uploads frames.

## Layout
- `game_session.py` — pure, hardware‑free session logic (move loop, resolve, gesture, PGN).
  Wraps `chessmon.CameraGame` + `RealBoard`. No sockets here → unit‑testable.
- `manager.py` — in‑memory session registry, indexed by table token (QR‑A) and pair token (QR‑B).
- `app.py` — the thin FastAPI/WebSocket layer that speaks the wire protocol.

## Run
From the repo root — one launcher runs the server **and** the WebRTC bridge that connects phones
through comlos.com (no certificate, no inbound ports):
```
chessmon setup      # one-time: .venv + dependencies
chessmon            # server (:8000) + bridge; Ctrl+C stops both
```
Console: `http://localhost:8000/app/admin.html` (plain HTTP on this PC). Also `chessmon stop` /
`restart` / `status`, and `chessmon --no-bridge` for the server alone. See [`run.py`](../run.py).

## Devices (the two PWAs)
Phones connect over WebRTC through **comlos.com/relay/app** (real cert, nothing to install) — the
console shows a pairing **QR** that encodes this club's room (`rtc_room.txt`). The same pages are
served locally under `/app/` for same-PC testing.
- **Clock** — open the console QR on phone #1 (or `…/app/clock.html` on localhost).
- **Camera** — scan the clock's QR with phone #2 (or `tools/camera_client.py --pair <token>` on a
  laptop+BRIO): it opens the camera page, you capture the empty board + start position, then it
  streams a frame on every `capture.req`.

The bridge needs only **outbound** access to comlos.com — no inbound firewall rule, and no local
certificate (the device pages come from comlos.com over real HTTPS, so `getUserMedia` is allowed).
The operator's own console is fine on plain `http://localhost:8000` (a secure origin too).

## Wire protocol
HTTP:
- `POST /tables {white, black, variant}` → `{tableToken, pairToken, qr}`
- `GET /tables/{token}/state` → snapshot (spectator view)

WebSocket `/ws` — first message joins a session:
- clock:   `{type: table.join, tableToken}` → `session.ready {pairToken, …}`
- camera:  `{type: pair.join, pairToken}`   → `session.ready {role: camera}`
- web:     `{type: spectate, tableToken}`   → `state {…}`

Then the move loop:
1. clock → `move.confirm {side, clockWhite, clockBlack}`
2. server → camera `capture.req`
3. camera → `<binary JPEG frame>`  (server runs `chessmon` on it)
4. server → clock one of: `move.result {san, fen, ply}` · `move.ambiguous {candidates}` ·
   `move.unseen {candidates}` · `move.unclear {reason}` · `game.end {result, pgn}`
5. clock → `move.resolve {uci}` to commit an ambiguous/unseen read (player tapped it)
6. server → web `state {…}` after every accepted move

## Testing without a camera
The session logic is driven directly in `tests/test_server.py`:
```
.venv\Scripts\python tests\test_server.py
```
Over the socket, send `{type: grid, grid: [[…]]}` to feed a three‑state occupancy grid in
place of a frame (skips classification) — handy for a browser dev client with no camera.

## Stubbed in the MVP (see the ecosystem plan)
- In‑memory store (swap for Postgres). No auth yet — table/pair tokens only.
- Cloud relay (hybrid mode) not wired — this is the **core server** = local‑only / option 1.
- Calibration: `Session.calibrate_from_frame()` reuses the no‑empty‑board flow; exposure‑lock
  and empty‑board calibration live on the camera unit (chessmon `tools/live.py`).

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
```
.venv\Scripts\pip install -r server/requirements.txt
.venv\Scripts\python -m uvicorn server.app:app --reload --host 0.0.0.0 --port 8000
```

## Devices (the two PWAs)
Served at `/app/` by the server.
- **Clock** — `http://<server-ip>:8000/` → "New table" → shows a pairing **QR** (encodes a deep
  link to the camera page) + the raw token. Confirm button, board, and the ambiguity prompt.
- **Camera** — `tools/camera_client.py --pair <token>` on a laptop+BRIO, **or** the camera PWA
  at `/app/camera.html?pair=<token>`: scan the clock's QR with phone #2's camera → it opens the
  page, asks for the camera, you capture the empty board + start position, then it streams a
  frame on every `capture.req`.

**HTTPS is required for the camera PWA** — browsers only allow `getUserMedia` on a secure origin
(https or localhost), so over plain `http://<LAN-ip>` the camera is blocked. One command serves
TLS with an auto-generated self-signed cert (no openssl needed; phones accept the warning once):
```
.venv\Scripts\python server\serve_https.py          # prints the https://<lan-ip>:8000 URL
```
It writes `cert.pem`/`key.pem` (covering localhost + this machine's LAN IPs) on first run — pass
`new` to regenerate. The clock auto-uses `wss` over https. (The CLI `camera_client.py` needs no HTTPS.)

**Phone times out connecting?** Two usual causes:
- **Wrong IP** — use the **Wi-Fi** address the helper prints first (usually `192.168.*` / `10.*`),
  not a virtual adapter (`172.*` Hyper-V/WSL) that the phone can't route to.
- **Firewall** — Windows blocks inbound by default. Allow the port once, from an **admin** PowerShell:
  ```
  New-NetFirewallRule -DisplayName "chessmon 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
  ```
  (Both phones must be on the **same Wi-Fi** as this machine.)

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

"""Console / table-config coverage:
  * persistence  — a configured table survives a restart with its units offline (and the empty-table prune)
  * live WS flow — hello -> Unused; table.create / assign / unassign / rename / remove

Run the persistence half standalone; the WS half needs the server up on :8788.
    python tools/test_console.py
"""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, r"C:\Claude\Projects\chessmon")
import websockets                                              # noqa: E402
from server.manager import SessionManager                     # noqa: E402

URL = "ws://127.0.0.1:8788/ws"
_FAIL = []


def check(cond, msg):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {msg}")
    if not cond:
        _FAIL.append(msg)


def test_persistence():
    print("table config persists a restart (units offline) + survives the empty-table prune")
    path = os.path.join(tempfile.gettempdir(), "cm_cfg_test.pkl")
    mgr = SessionManager()
    s = mgr.create_table(name="Persist Test")
    s.clock_dev, s.camera_dev = "dev-clock", "dev-cam"          # configured but never calibrated, no moves
    tok = s.table_token
    mgr.save(path)

    mgr2 = SessionManager()
    mgr2.load(path)
    s2 = mgr2.by_table(tok)
    check(s2 is not None, "a named + configured table is NOT pruned on load")
    if s2:
        check(s2.clock_dev == "dev-clock" and s2.camera_dev == "dev-cam",
              f"unit assignments persisted ({s2.clock_dev} / {s2.camera_dev})")
        check(s2.started_at is None, "started_at defaults to None (not yet running)")

    mgr3 = SessionManager()
    e = mgr3.create_table(name="")                             # truly empty -> still prunable
    etok = e.table_token
    mgr3.save(path)
    mgr4 = SessionManager()
    mgr4.load(path)
    check(mgr4.by_table(etok) is None, "an unnamed, unconfigured, empty table is still pruned")
    os.remove(path)


async def next_devices(ws, timeout=3):
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if m.get("type") == "devices":
            return m


async def test_ws():
    print("console WS flow: hello -> Unused; create / assign / unassign / rename / remove")
    admin = await websockets.connect(URL)
    await admin.send(json.dumps({"type": "admin.join"}))
    await next_devices(admin)                                  # initial state

    dev = await websockets.connect(URL)
    await dev.send(json.dumps({"type": "hello", "devId": "dev-ws-1", "name": "WS Cam",
                               "role": "camera", "plat": "iPadOS",
                               "screen": {"w": 820, "h": 1180, "dpr": 2}}))
    m = await next_devices(admin)
    d = next((x for x in m["devices"] if x["id"] == "dev-ws-1"), None)
    check(d is not None and d.get("plat") == "iPadOS", "device registered, plat carried through")

    await dev.send(json.dumps({"type": "device.meta", "cam": {"w": 1280, "h": 960}}))
    m = await next_devices(admin)
    d = next((x for x in m["devices"] if x["id"] == "dev-ws-1"), None)
    check(d is not None and d.get("cam") == {"w": 1280, "h": 960}, "device.meta -> camera resolution recorded")

    await admin.send(json.dumps({"type": "table.create", "name": "WS Table"}))
    m = await next_devices(admin)
    t = next((x for x in m["tables"] if x["name"] == "WS Table"), None)
    check(t is not None and t["clock"] is None and t["camera"] is None, "table.create -> empty table")
    tok = t["token"]

    await admin.send(json.dumps({"type": "table.assign", "table": tok, "role": "camera", "devId": "dev-ws-1"}))
    asg = json.loads(await asyncio.wait_for(dev.recv(), 3))
    check(asg.get("type") == "assign" and asg.get("role") == "camera" and asg.get("pair"),
          "the unit receives assign + its pair token")
    m = await next_devices(admin)
    t = next(x for x in m["tables"] if x["token"] == tok)
    check(t["camera"] == "dev-ws-1", "table.camera now points at the device")
    check(not any(x["id"] == "dev-ws-1" and x["table"] != tok for x in m["devices"]), "device bound to this table")

    await admin.send(json.dumps({"type": "table.cleargame", "table": tok}))
    m = await next_devices(admin)
    t = next((x for x in m["tables"] if x["token"] == tok), None)
    check(t is not None and t["camera"] == "dev-ws-1" and t["moves"] == 0,
          "table.cleargame keeps the table + its units (game state cleared)")

    await admin.send(json.dumps({"type": "table.unassign", "table": tok, "role": "camera"}))
    una = json.loads(await asyncio.wait_for(dev.recv(), 3))
    check(una.get("type") == "unassigned", "the unit is told it was unassigned")
    m = await next_devices(admin)
    t = next(x for x in m["tables"] if x["token"] == tok)
    check(t["camera"] is None, "camera slot empty -> device back in Unused")

    await admin.send(json.dumps({"type": "table.rename", "table": tok, "name": "Renamed"}))
    m = await next_devices(admin)
    check(any(x["token"] == tok and x["name"] == "Renamed" for x in m["tables"]), "table.rename")

    await admin.send(json.dumps({"type": "table.remove", "table": tok}))
    m = await next_devices(admin)
    check(not any(x["token"] == tok for x in m["tables"]), "table.remove")

    await admin.close()
    await dev.close()


async def test_landing():
    print("landing page: a new device waits in Unused; an already-configured one is bounced to its role")
    admin = await websockets.connect(URL)
    await admin.send(json.dumps({"type": "admin.join"}))
    await next_devices(admin)

    new = await websockets.connect(URL)
    await new.send(json.dumps({"type": "hello", "devId": "land-new", "landing": True,
                               "name": "iPad", "plat": "iPad", "screen": {"w": 820, "h": 1180, "dpr": 2}}))
    m = await next_devices(admin)
    d = next((x for x in m["devices"] if x["id"] == "land-new"), None)
    check(d is not None and not d.get("table"), "a new landing device shows up unconfigured (Unused)")
    try:
        await asyncio.wait_for(new.recv(), 0.6)
        check(False, "a new landing device must NOT be auto-assigned")
    except asyncio.TimeoutError:
        check(True, "a new landing device is left waiting (no bounce)")

    await admin.send(json.dumps({"type": "table.create", "name": "Land Table"}))
    m = await next_devices(admin)
    tok = next(x["token"] for x in m["tables"] if x["name"] == "Land Table")
    await admin.send(json.dumps({"type": "table.assign", "table": tok, "role": "camera", "devId": "land-new"}))
    await asyncio.wait_for(new.recv(), 3)                       # the assign that table.assign pushes to the unit
    await next_devices(admin)

    reopen = await websockets.connect(URL)
    await reopen.send(json.dumps({"type": "hello", "devId": "land-new", "landing": True, "name": "iPad"}))
    asg = json.loads(await asyncio.wait_for(reopen.recv(), 3))
    check(asg.get("type") == "assign" and asg.get("role") == "camera" and asg.get("pair"),
          "reopening the landing on a configured device bounces it to its camera role")

    await admin.send(json.dumps({"type": "table.remove", "table": tok}))
    await admin.close()
    await new.close()
    await reopen.close()


async def test_camera_offline():
    print("camera offline: when the camera ws drops, the table's clock is told camera.offline")
    admin = await websockets.connect(URL)
    await admin.send(json.dumps({"type": "admin.join"}))
    await next_devices(admin)

    await admin.send(json.dumps({"type": "table.create", "name": "Off Table"}))
    m = await next_devices(admin)
    tok = next(x["token"] for x in m["tables"] if x["name"] == "Off Table")

    cam = await websockets.connect(URL)
    await cam.send(json.dumps({"type": "hello", "devId": "cam-off", "name": "Cam", "role": "camera"}))
    await next_devices(admin)
    await admin.send(json.dumps({"type": "table.assign", "table": tok, "role": "camera", "devId": "cam-off"}))
    asg = json.loads(await asyncio.wait_for(cam.recv(), 3))
    await cam.send(json.dumps({"type": "pair.join", "pairToken": asg["pair"]}))   # camera now linked to the table
    await next_devices(admin)

    await admin.send(json.dumps({"type": "camera.control", "table": tok, "what": "flash", "on": True}))
    got_ctl = False
    for _ in range(6):
        try:
            mc = json.loads(await asyncio.wait_for(cam.recv(), 2))
        except asyncio.TimeoutError:
            break
        if mc.get("type") == "camera.control" and mc.get("what") == "flash" and mc.get("on") is True:
            got_ctl = True
            break
    check(got_ctl, "the camera receives the camera.control relay (flash on)")

    clk = await websockets.connect(URL)
    await clk.send(json.dumps({"type": "table.join", "tableToken": tok}))
    await asyncio.sleep(0.2)
    await cam.close()                                            # camera drops

    got = False
    for _ in range(8):
        try:
            msg = json.loads(await asyncio.wait_for(clk.recv(), 2))
        except asyncio.TimeoutError:
            break
        if msg.get("type") == "camera.offline":
            got = True
            break
    check(got, "the clock receives camera.offline after the camera ws drops")

    await admin.send(json.dumps({"type": "table.remove", "table": tok}))
    await admin.close()
    await clk.close()


test_persistence()
try:
    asyncio.run(test_ws())
    asyncio.run(test_landing())
    asyncio.run(test_camera_offline())
except Exception as e:                                          # noqa: BLE001
    check(False, f"WS flow raised: {e!r}")
print("\n" + ("CONSOLE TESTS FAILED: " + "; ".join(_FAIL) if _FAIL else "ALL CONSOLE TESTS OK"))
sys.exit(1 if _FAIL else 0)

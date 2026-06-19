"""Server-console checks over the live server: devices register (hello), rename sticks, and the
console pairs two connected devices -> each gets an `assign` push, the console gets `paired`."""
import asyncio
import json

import websockets

WS = "ws://127.0.0.1:8788/ws"


async def main():
    async with websockets.connect(WS) as admin:
        await admin.send(json.dumps({"type": "admin.join"}))
        assert json.loads(await admin.recv())["type"] == "devices"

        async with websockets.connect(WS) as devA, websockets.connect(WS) as devB:
            await devA.send(json.dumps({"type": "hello", "devId": "devA", "name": "iPhone", "role": "clock"}))
            await admin.recv()
            await devB.send(json.dumps({"type": "hello", "devId": "devB", "name": "iPad", "role": "camera"}))
            seen = json.loads(await admin.recv())["devices"]
            assert {d["id"] for d in seen} >= {"devA", "devB"}, seen
            print("hello x2 -> console sees both devices  OK", flush=True)

            await admin.send(json.dumps({"type": "device.rename", "devId": "devA", "userName": "T1 clock"}))
            ren = json.loads(await admin.recv())["devices"]
            assert [d for d in ren if d["id"] == "devA"][0]["userName"] == "T1 clock"
            print("rename -> userName set  OK", flush=True)

            await admin.send(json.dumps({"type": "pair.devices", "clock": "devA", "camera": "devB"}))
            asn_a = json.loads(await devA.recv())
            asn_b = json.loads(await devB.recv())
            paired = json.loads(await admin.recv())
            assert asn_a["type"] == "assign" and asn_a["role"] == "clock" and asn_a.get("table"), asn_a
            assert asn_b["type"] == "assign" and asn_b["role"] == "camera" and asn_b.get("pair"), asn_b
            assert paired["type"] == "paired" and paired["clockOnline"] and paired["cameraOnline"], paired
            assert asn_a["table"] == paired["table"] and asn_b["pair"] == paired["pair"]
            print("pair.devices -> assign(clock)+assign(camera) pushed, console got paired  PAIRING PASSED",
                  flush=True)


asyncio.run(main())

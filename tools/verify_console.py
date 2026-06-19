"""Device-registry / server-console check over the live server: a device says hello, the console
(admin.join) sees it, a rename sticks, and a disconnect flips it offline."""
import asyncio
import json

import websockets

WS = "ws://127.0.0.1:8788/ws"


async def main():
    async with websockets.connect(WS) as admin:
        await admin.send(json.dumps({"type": "admin.join"}))
        d0 = json.loads(await admin.recv())
        assert d0["type"] == "devices", d0

        async with websockets.connect(WS) as dev:
            await dev.send(json.dumps({"type": "hello", "devId": "dev-test-1",
                                       "name": "iPad", "role": "camera"}))
            upd = json.loads(await admin.recv())
            found = [x for x in upd.get("devices", []) if x["id"] == "dev-test-1"]
            assert found and found[0]["name"] == "iPad" and found[0]["role"] == "camera" \
                and found[0]["online"] is True, upd
            print("hello -> console sees device (iPad / camera / online)  OK", flush=True)

            await admin.send(json.dumps({"type": "device.rename", "devId": "dev-test-1",
                                         "userName": "Board 1 cam"}))
            ren = json.loads(await admin.recv())
            f2 = [x for x in ren["devices"] if x["id"] == "dev-test-1"][0]
            assert f2["userName"] == "Board 1 cam", ren
            print("rename -> userName set  OK", flush=True)

        off = json.loads(await admin.recv())                       # dev disconnected above
        f3 = [x for x in off["devices"] if x["id"] == "dev-test-1"][0]
        assert f3["online"] is False, off
        print("disconnect -> online=False  CONSOLE PASSED", flush=True)


asyncio.run(main())

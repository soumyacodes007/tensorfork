"""Time state_queryStorageAt batches of different sizes on the archive (Chopsticks uses 1000)."""

import asyncio
import json
import sys
import time

import websockets

URL = "wss://archive.chain.opentensor.ai:443"
BLOCK = sys.argv[1]
PREFIX = "0xca407206ec1ab726b2636c4b145ac287419a60ae8b01e6dcaebd7317e43c69bf4000"


async def call(ws, rid, method, params, timeout=180):
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    async def wait():
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == rid:
                return msg
    return await asyncio.wait_for(wait(), timeout)


async def main():
    async with websockets.connect(URL, max_size=2**30, ping_interval=None) as ws:
        keys = (await call(ws, 1, "state_getKeysPaged", [PREFIX, 1000, PREFIX, BLOCK]))["result"]
        print(f"{len(keys)} keys under prefix")
        for size in (50, 200, len(keys)):
            t0 = time.perf_counter()
            try:
                msg = await call(ws, 100 + size, "state_queryStorageAt", [keys[:size], BLOCK])
            except asyncio.TimeoutError:
                print(f"batch {size}: TIMEOUT >180s")
                continue
            took = time.perf_counter() - t0
            if "error" in msg:
                print(f"batch {size}: ERROR {msg['error']} in {took:.1f}s")
                continue
            changes = msg["result"][0]["changes"]
            nbytes = sum(len(v or "") // 2 for _, v in changes)
            print(f"batch {size}: {took:.1f}s, {nbytes / 1e6:.2f} MB")


asyncio.run(main())

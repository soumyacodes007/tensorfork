"""Time SubnetInfoRuntimeApi_get_metagraph(netuid) on the fork with no client timeout.

The SDK gives up after 60 s; this measures how long the engine really needs when
it lazily pulls every storage key the runtime touches, and how fast the warm
(cached) call is.
"""

import asyncio
import json
import sys
import time

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8000"
NETUID = int(sys.argv[2]) if len(sys.argv) > 2 else 64


async def state_call(ws, rid):
    param = "0x" + NETUID.to_bytes(2, "little").hex()  # u16 netuid, SCALE little-endian
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": "state_call",
                              "params": ["SubnetInfoRuntimeApi_get_metagraph", param]}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == rid:
            return msg


async def main():
    async with websockets.connect(URL, max_size=2**28, ping_interval=None) as ws:
        for rid, label in ((1, "cold"), (2, "warm")):
            t0 = time.perf_counter()
            msg = await state_call(ws, rid)
            took = time.perf_counter() - t0
            if "error" in msg:
                print(f"{label}: ERROR after {took:.1f}s: {msg['error']}")
                return
            print(f"{label}: {took:.1f}s, {len(msg['result']) // 2} bytes")


asyncio.run(main())

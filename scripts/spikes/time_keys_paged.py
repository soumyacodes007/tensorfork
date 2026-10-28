"""Time the upstream requests Chopsticks makes for a metagraph call, directly on the archive.

Replays state_getKeysPaged / state_getStorage for SubtensorModule netuid-prefixed
keys at the fork block, to find which request exceeds Chopsticks' 60 s timeout.
"""

import asyncio
import json
import sys
import time

import websockets

URL = "wss://archive.chain.opentensor.ai:443"
BLOCK_HASH = sys.argv[1] if len(sys.argv) > 1 else None
PREFIXES = [
    # from the engine log: SubtensorModule items, keyed by netuid 64 (0x4000)
    "0xca407206ec1ab726b2636c4b145ac287419a60ae8b01e6dcaebd7317e43c69bf4000",
    "0xca407206ec1ab726b2636c4b145ac2879055a2cffd4b296f38f2de8d9b333cab4000",
]


async def call(ws, rid, method, params):
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == rid:
            return msg


async def main():
    async with websockets.connect(URL, max_size=2**28, ping_interval=None) as ws:
        block = BLOCK_HASH or (await call(ws, 1, "chain_getFinalizedHead", []))["result"]
        print("block", block)
        rid = 10
        for prefix in PREFIXES:
            for count, start in ((100, prefix), (1000, prefix)):
                rid += 1
                t0 = time.perf_counter()
                try:
                    msg = await asyncio.wait_for(
                        call(ws, rid, "state_getKeysPaged", [prefix, count, start, block]), 120)
                except asyncio.TimeoutError:
                    print(f"getKeysPaged {prefix[-12:]} count={count}: TIMEOUT >120s")
                    continue
                took = time.perf_counter() - t0
                if "error" in msg:
                    print(f"getKeysPaged {prefix[-12:]} count={count}: ERROR {msg['error']} ({took:.1f}s)")
                else:
                    print(f"getKeysPaged {prefix[-12:]} count={count}: {len(msg['result'])} keys in {took:.2f}s")


asyncio.run(main())

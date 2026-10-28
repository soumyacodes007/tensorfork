"""Probe upstream RPC endpoints: latency, archive depth, and burst tolerance."""

import asyncio
import json
import sys
import time

import websockets

ENDPOINTS = sys.argv[1:] or [
    "wss://archive.chain.opentensor.ai:443",
    "wss://entrypoint-finney.opentensor.ai:443",
]


async def call(ws, method, params, rid):
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == rid:
            return msg


async def probe(url):
    t0 = time.perf_counter()
    try:
        async with websockets.connect(url, max_size=2**26, open_timeout=20) as ws:
            health = await asyncio.wait_for(call(ws, "system_health", [], 1), 20)
            head = await asyncio.wait_for(call(ws, "chain_getHeader", [], 2), 20)
            number = int(head["result"]["number"], 16)
            old = await asyncio.wait_for(call(ws, "chain_getBlockHash", [number - 5000], 3), 20)
            old_hash = old.get("result")
            # Storage read at an older block tells us if the endpoint serves historical state.
            hist = await asyncio.wait_for(call(ws, "state_getRuntimeVersion", [old_hash], 4), 20)
            # Burst: 50 parallel reads on one socket.
            t1 = time.perf_counter()
            reqs = [
                {"jsonrpc": "2.0", "id": 100 + i, "method": "state_getStorage",
                 "params": ["0x26aa394eea5630e07c48ae0c9558cef702a5c1b19ab7a04f536c519aca4983ac", old_hash]}
                for i in range(50)
            ]
            for r in reqs:
                await ws.send(json.dumps(r))
            got, errors = 0, 0
            while got + errors < 50:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 30))
                if "error" in msg:
                    errors += 1
                elif msg.get("id", 0) >= 100:
                    got += 1
            print(f"{url}\n  health={health.get('result')} head={number}"
                  f"\n  historical(-5000) ok={'result' in hist} err={hist.get('error')}"
                  f"\n  burst50: ok={got} errors={errors} in {time.perf_counter() - t1:.2f}s"
                  f"\n  total {time.perf_counter() - t0:.2f}s")
    except Exception as e:  # noqa: BLE001 - spike diagnostics
        print(f"{url}\n  FAILED after {time.perf_counter() - t0:.2f}s: {type(e).__name__}: {e}")


async def main():
    for url in ENDPOINTS:
        await probe(url)


asyncio.run(main())

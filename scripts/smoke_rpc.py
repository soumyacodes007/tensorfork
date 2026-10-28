"""SDK-free smoke test of a running fork (works on native Windows): raw JSON-RPC through the proxy."""

import json
import sys
import time

from websockets.sync.client import connect

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:9944"


def call(ws, method, params=None, rid=[0]):
    rid[0] += 1
    ws.send(json.dumps({"jsonrpc": "2.0", "id": rid[0], "method": method, "params": params or []}))
    msg = json.loads(ws.recv(timeout=600))
    if "error" in msg:
        raise SystemExit(f"{method}: {msg['error']}")
    return msg["result"]


with connect(URL, max_size=2**28) as ws:
    head = call(ws, "chain_getHeader")
    rv = call(ws, "state_getRuntimeVersion")
    print(f"head {int(head['number'], 16)}  runtime {rv['specName']} v{rv['specVersion']}  chain {call(ws, 'system_chain')}")
    t0 = time.perf_counter()
    # SN64 metagraph via the runtime API, params given without 0x (as the bittensor SDK sends them)
    result = call(ws, "state_call", ["SubnetInfoRuntimeApi_get_metagraph", (64).to_bytes(2, "little").hex()])
    print(f"SN64 metagraph runtime call: {len(result) // 2} bytes in {time.perf_counter() - t0:.1f}s")

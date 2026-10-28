"""Serve a snapshot as the engine's upstream, so a fork runs fully offline.

Chopsticks treats its `--endpoint` as a read-only archive node: it asks for the
head header/block, chain identity, and storage (point reads, batch reads, key
paging). This server answers exactly those calls from a snapshot file; nothing
leaves the machine and reads run at disk speed.

usage: python -m tensorfork.snapshot_server SNAPSHOT [--port 9955]
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
from pathlib import Path

import websockets

from tensorfork.snapshot import Snapshot

log = logging.getLogger("tensorfork.snapshot_server")


class SnapshotRpc:
    def __init__(self, snap: Snapshot):
        self.snap = snap
        self._subs = itertools.count(1)

    def _at(self, block_hash: str | None) -> None:
        if block_hash not in (None, self.snap.block_hash):
            raise RpcError(-32602, f"snapshot only holds state at {self.snap.block_hash}, not {block_hash}")

    async def dispatch(self, method: str, params: list, send_notification) -> object:
        m, s = self.snap.meta, self.snap
        match method:
            case "chain_getBlockHash":
                n = params[0] if params else None
                if n is None or n == s.block_number:
                    return s.block_hash
                if n == 0:
                    return m["genesis_hash"]
                return None
            case "chain_getHeader":
                self._at(params[0] if params else None)
                return m["header"]
            case "chain_getBlock":
                self._at(params[0] if params else None)
                return m["block"]
            case "chain_getFinalizedHead" | "chain_finalizedHead":
                return s.block_hash
            case "system_chain" | "system_name" | "system_properties":
                return m[method]
            case "state_getRuntimeVersion" | "chain_getRuntimeVersion":
                return m["runtime_version"]
            case "state_getStorage":
                self._at(params[1] if len(params) > 1 else None)
                return s.get(params[0])
            case "state_queryStorageAt":
                self._at(params[1] if len(params) > 1 else None)
                return [{"block": s.block_hash, "changes": [[k, s.get(k)] for k in params[0]]}]
            case "state_getKeysPaged":
                prefix, count = params[0], params[1]
                start = params[2] if len(params) > 2 else None
                self._at(params[3] if len(params) > 3 else None)
                return s.keys_paged(prefix, count, start)
            case "childstate_getStorage" | "childstate_getStorageEntries":
                return None if method == "childstate_getStorage" else [None] * len(params[1])
            case "childstate_getKeysPaged":
                return []
            case "chain_subscribeNewHeads" | "chain_subscribeFinalizedHeads" | "chain_subscribeNewHead":
                sub = f"tf{next(self._subs)}"
                notify = method.replace("chain_subscribe", "chain_").replace("Heads", "Head")
                send_notification(notify, sub, m["header"])
                return sub
            case "chain_unsubscribeNewHeads" | "chain_unsubscribeFinalizedHeads" | "chain_unsubscribeNewHead":
                return True
            case "rpc_methods":
                return {"methods": SUPPORTED}
        raise RpcError(-32601, f"method {method} is not served by a TensorFork snapshot")


SUPPORTED = [
    "chain_getBlockHash", "chain_getHeader", "chain_getBlock", "chain_getFinalizedHead",
    "chain_subscribeNewHeads", "chain_subscribeFinalizedHeads", "system_chain", "system_name",
    "system_properties", "state_getRuntimeVersion", "state_getStorage", "state_queryStorageAt",
    "state_getKeysPaged", "childstate_getStorage", "childstate_getKeysPaged", "rpc_methods",
]


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


async def serve(snapshot_path: Path, host: str = "127.0.0.1", port: int = 9955) -> None:
    rpc = SnapshotRpc(Snapshot(snapshot_path))

    async def handler(ws):
        def send_notification(method: str, sub: str, result) -> None:
            # Deliver after the subscribe reply so the client knows the id.
            msg = {"jsonrpc": "2.0", "method": method, "params": {"subscription": sub, "result": result}}
            asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(ws.send(json.dumps(msg))))

        async def one(req: dict) -> dict:
            try:
                result = await rpc.dispatch(req.get("method", ""), req.get("params") or [], send_notification)
                return {"jsonrpc": "2.0", "id": req.get("id"), "result": result}
            except RpcError as e:
                return {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": e.code, "message": e.message}}
            except Exception as e:  # noqa: BLE001 - surface as a JSON-RPC error, keep serving
                log.exception("request failed: %s", req)
                return {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": -32603, "message": str(e)}}

        async for raw in ws:
            req = json.loads(raw)
            reply = await asyncio.gather(*map(one, req)) if isinstance(req, list) else await one(req)
            await ws.send(json.dumps(reply))

    async with websockets.serve(handler, host, port, max_size=2**30):
        snap = rpc.snap
        print(f"tensorfork snapshot server on ws://{host}:{port}: block {snap.block_number} "
              f"{snap.block_hash} ({len(snap)} keys)", flush=True)
        await asyncio.Future()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("snapshot", type=Path)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9955)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(a.snapshot, a.host, a.port))


if __name__ == "__main__":
    main()

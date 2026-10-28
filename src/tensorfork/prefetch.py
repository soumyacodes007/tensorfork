"""Warm the engine's storage cache straight from an archive node.

Chopsticks pulls storage lazily, one key per round trip, as the runtime touches
it. A metagraph read on a big subnet touches thousands of keys, so a cold fork
spends tens of minutes on sequential ~0.5 s fetches over a flaky public RPC.

Prefetch does the same work in bulk instead: it lists every key under the given
storage prefixes at the fork block (fanning out across 256 sub-prefixes), pulls
values in parallel `state_queryStorageAt` chunks, and writes them into the
engine's SQLite cache (`key_value_pair`), so the runtime finds them locally.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import websockets
import xxhash

PAGE = 1000
VALUE_CHUNK = 200


def twox128(name: str) -> str:
    data = name.encode()
    return (
        xxhash.xxh64(data, seed=0).intdigest().to_bytes(8, "little")
        + xxhash.xxh64(data, seed=1).intdigest().to_bytes(8, "little")
    ).hex()


def pallet_prefix(pallet: str) -> str:
    return "0x" + twox128(pallet)


@dataclass
class Stats:
    keys: int = 0
    values: int = 0
    bytes: int = 0
    retries: int = 0
    started: float = field(default_factory=time.perf_counter)

    def line(self) -> str:
        took = time.perf_counter() - self.started
        return (f"{self.keys} keys listed, {self.values} values ({self.bytes / 1e6:.1f} MB) "
                f"in {took:.0f}s, {self.retries} retries")


class RpcPool:
    """A few websocket connections with per-request timeouts, retries and reconnects."""

    def __init__(self, url: str, size: int = 6, timeout: float = 30.0, attempts: int = 8):
        self.url, self.size, self.timeout, self.attempts = url, size, timeout, attempts
        self._conns: list[websockets.ClientConnection | None] = [None] * size
        self._locks = [asyncio.Lock() for _ in range(size)]
        self._next = 0
        self.stats: Stats | None = None

    async def _conn(self, i: int) -> websockets.ClientConnection:
        if self._conns[i] is None:
            self._conns[i] = await websockets.connect(
                self.url, max_size=2**30, open_timeout=20, ping_interval=None
            )
        return self._conns[i]

    async def call(self, method: str, params: list):
        last: Exception | None = None
        for attempt in range(self.attempts):
            i = self._next = (self._next + 1) % self.size
            async with self._locks[i]:  # one request in flight per socket keeps ordering trivial
                try:
                    ws = await self._conn(i)
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
                    msg = json.loads(await asyncio.wait_for(ws.recv(), self.timeout))
                    if "error" in msg:
                        raise RuntimeError(msg["error"])
                    return msg["result"]
                except Exception as e:  # noqa: BLE001 - network flake: drop the socket and retry
                    last = e
                    if self.stats:
                        self.stats.retries += 1
                    conn, self._conns[i] = self._conns[i], None
                    if conn is not None:
                        await conn.close()
            await asyncio.sleep(min(0.5 * 2**attempt, 10))
        raise RuntimeError(f"{method} failed after {self.attempts} attempts: {last}")

    async def close(self) -> None:
        for c in self._conns:
            if c is not None:
                await c.close()


async def _list_keys(pool: RpcPool, prefix: str, block: str, stats: Stats) -> list[str]:
    keys: list[str] = []
    start = prefix
    while True:
        page = await pool.call("state_getKeysPaged", [prefix, PAGE, start, block])
        keys.extend(page)
        stats.keys += len(page)
        if len(page) < PAGE:
            return keys
        start = page[-1]


async def list_keys(pool: RpcPool, prefix: str, block: str, stats: Stats) -> list[str]:
    """All keys under `prefix`, listed in parallel across the next byte."""
    subs = [f"{prefix}{b:02x}" for b in range(256)]
    parts = await asyncio.gather(*(_list_keys(pool, s, block, stats) for s in subs))
    return [k for part in parts for k in part]


async def fetch_values(pool: RpcPool, keys: list[str], block: str, stats: Stats, sink) -> None:
    async def one(chunk: list[str]) -> None:
        result = await pool.call("state_queryStorageAt", [chunk, block])
        changes = result[0]["changes"] if result else []
        sink(changes)
        stats.values += len(changes)
        stats.bytes += sum(len(v or "") // 2 for _, v in changes)

    await asyncio.gather(*(one(keys[i:i + VALUE_CHUNK]) for i in range(0, len(keys), VALUE_CHUNK)))


def _sqlite_sink(db_path: Path, block_hash: str):
    con = sqlite3.connect(db_path, timeout=60)
    con.execute(
        'CREATE TABLE IF NOT EXISTS "key_value_pair" ("blockHash" varchar NOT NULL, "key" varchar NOT NULL, '
        '"value" text, PRIMARY KEY ("blockHash", "key"))'
    )

    def sink(changes: list[list[str | None]]) -> None:
        con.executemany(
            "INSERT OR REPLACE INTO key_value_pair (blockHash, key, value) VALUES (?, ?, ?)",
            [(block_hash, k, v) for k, v in changes],
        )
        con.commit()

    return sink, con


async def prefetch(
    endpoint: str,
    db_path: Path,
    block_hash: str,
    prefixes: list[str],
    *,
    connections: int = 6,
    progress=print,
) -> Stats:
    stats = Stats()
    pool = RpcPool(endpoint, size=connections)
    pool.stats = stats
    sink, con = _sqlite_sink(db_path, block_hash)

    async def report() -> None:
        while True:
            await asyncio.sleep(10)
            progress(stats.line())

    reporter = asyncio.create_task(report())
    try:
        for prefix in prefixes:
            keys = await list_keys(pool, prefix, block_hash, stats)
            progress(f"{prefix[:34]}: {len(keys)} keys")
            await fetch_values(pool, keys, block_hash, stats, sink)
    finally:
        reporter.cancel()
        await pool.close()
        con.close()
    progress("done: " + stats.line())
    return stats


async def block_hash_for(endpoint: str, number: int) -> str:
    pool = RpcPool(endpoint, size=1)
    try:
        return await pool.call("chain_getBlockHash", [number])
    finally:
        await pool.close()

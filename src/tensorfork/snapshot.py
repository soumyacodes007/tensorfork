"""State snapshots: every storage key/value of the chain at one block, in one file.

A snapshot is what lets a user fork mainnet without running a node or hammering
a public RPC: we export it once from a node we run, users download it, and the
engine reads storage from it locally (see `snapshot_server`).

Format: a SQLite file.
- `kv(key BLOB PRIMARY KEY, value BLOB)`: raw storage at the snapshot block.
  BLOB keys sort bytewise, so prefix scans are plain range queries.
- `meta(name TEXT PRIMARY KEY, value TEXT)`: JSON for the RPC answers the
  engine needs besides storage (header, block, chain name, properties, ...).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Iterator

from tensorfork.prefetch import RpcPool, Stats

PAGE = 1000
VALUE_CHUNK = 500
ITEM_PREFIX_BYTES = 32  # twox128(pallet) ++ twox128(item)

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key BLOB PRIMARY KEY, value BLOB) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT);
"""


def _b(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str.removeprefix("0x"))


def _h(data: bytes) -> str:
    return "0x" + data.hex()


def prefix_upper_bound(prefix: bytes) -> bytes | None:
    """Smallest byte string greater than every key starting with `prefix`."""
    p = bytearray(prefix)
    while p and p[-1] == 0xFF:
        p.pop()
    if not p:
        return None
    p[-1] += 1
    return bytes(p)


class Snapshot:
    """Read access to a snapshot file."""

    def __init__(self, path: Path):
        self.path = Path(path).expanduser()
        if not self.path.is_file():
            raise FileNotFoundError(f"no snapshot at {self.path}")
        self.con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        self.meta = {name: json.loads(value) for name, value in self.con.execute("SELECT name, value FROM meta")}

    @property
    def block_hash(self) -> str:
        return self.meta["block_hash"]

    @property
    def block_number(self) -> int:
        return self.meta["block_number"]

    def get(self, key_hex: str) -> str | None:
        row = self.con.execute("SELECT value FROM kv WHERE key = ?", (_b(key_hex),)).fetchone()
        return _h(row[0]) if row else None

    def keys_paged(self, prefix_hex: str, count: int, start_hex: str | None) -> list[str]:
        prefix = _b(prefix_hex)
        start = _b(start_hex) if start_hex else b""
        lower, op = (start, ">") if start and start >= prefix else (prefix, ">=")
        upper = prefix_upper_bound(prefix)
        sql = f"SELECT key FROM kv WHERE key {op} ?"
        args: list = [lower]
        if upper is not None:
            sql += " AND key < ?"
            args.append(upper)
        sql += " ORDER BY key LIMIT ?"
        args.append(count)
        return [_h(k) for (k,) in self.con.execute(sql, args)]

    def __len__(self) -> int:
        return self.con.execute("SELECT count(*) FROM kv").fetchone()[0]


# ---------------------------------------------------------------- export


async def _next_key_after(pool: RpcPool, start: str, block: str) -> str | None:
    page = await pool.call("state_getKeysPaged", ["0x", 1, start, block])
    return page[0] if page else None


async def discover_prefixes(pool: RpcPool, block: str) -> list[str]:
    """Walk the key space one storage item at a time.

    Every map's keys share a 32-byte item prefix, so after seeing one key we
    skip straight past its whole item. Shorter keys (`:code`, plain values) are
    their own prefix.
    """
    prefixes: list[str] = []
    start = "0x"
    while True:
        key = await _next_key_after(pool, start, block)
        if key is None:
            return prefixes
        raw = _b(key)
        if len(raw) > ITEM_PREFIX_BYTES:
            prefix = raw[:ITEM_PREFIX_BYTES]
            # Exclusive start key that sorts after every key under `prefix`.
            start = _h(prefix + b"\xff" * 256)
        else:
            prefix = raw
            start = key
        prefixes.append(_h(prefix))


async def _list_range(pool: RpcPool, prefix: str, block: str, stats: Stats) -> list[str]:
    # start=None is inclusive of a key equal to `prefix`; passing the prefix
    # itself as startKey would skip it (startKey is exclusive).
    keys: list[str] = []
    start = None
    while True:
        page = await pool.call("state_getKeysPaged", [prefix, PAGE, start, block])
        keys.extend(page)
        stats.keys += len(page)
        if len(page) < PAGE:
            return keys
        start = page[-1]


async def list_prefix(pool: RpcPool, prefix: str, block: str, stats: Stats) -> list[str]:
    """All keys under one item prefix; big items fan out over the next byte."""
    first = await pool.call("state_getKeysPaged", [prefix, PAGE, None, block])
    if len(first) < PAGE:
        stats.keys += len(first)
        return first
    subs = [f"{prefix}{b:02x}" for b in range(256)]
    parts = await asyncio.gather(*(_list_range(pool, s, block, stats) for s in subs))
    keys = [k for part in parts for k in part]
    # A key equal to the prefix itself (a plain value stored there) is not under any sub-prefix.
    if first[0] == prefix:
        keys.insert(0, prefix)
        stats.keys += 1
    return keys


def _chunks(items: list[str], n: int) -> Iterator[list[str]]:
    for i in range(0, len(items), n):
        yield items[i:i + n]


async def export_snapshot(
    endpoint: str,
    out: Path,
    *,
    block: int | None = None,
    connections: int = 16,
    progress=print,
) -> Path:
    """Export the full state at `block` (default: latest finalized) to `out`."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".partial")
    tmp.unlink(missing_ok=True)

    pool = RpcPool(endpoint, size=connections, timeout=120)
    stats = Stats()
    pool.stats = stats
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")

    async def report() -> None:
        while True:
            await asyncio.sleep(15)
            progress(stats.line())

    reporter = asyncio.create_task(report())
    try:
        if block is None:
            block_hash = await pool.call("chain_getFinalizedHead", [])
        else:
            block_hash = await pool.call("chain_getBlockHash", [block])
        header = await pool.call("chain_getHeader", [block_hash])
        number = int(header["number"], 16)
        meta = {
            "format": 1,
            "block_hash": block_hash,
            "block_number": number,
            "header": header,
            "block": await pool.call("chain_getBlock", [block_hash]),
            "genesis_hash": await pool.call("chain_getBlockHash", [0]),
            "system_chain": await pool.call("system_chain", []),
            "system_name": await pool.call("system_name", []),
            "system_properties": await pool.call("system_properties", []),
            "runtime_version": await pool.call("state_getRuntimeVersion", [block_hash]),
            "exported_at": int(time.time()),
            "source": endpoint,
        }
        progress(f"exporting block {number} {block_hash}")

        prefixes = await discover_prefixes(pool, block_hash)
        progress(f"{len(prefixes)} storage prefixes")

        sem = asyncio.Semaphore(connections)

        async def fetch(chunk: list[str]) -> None:
            async with sem:
                result = await pool.call("state_queryStorageAt", [chunk, block_hash])
            changes = result[0]["changes"] if result else []
            rows = [(_b(k), _b(v)) for k, v in changes if v is not None]
            con.executemany("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", rows)
            stats.values += len(rows)
            stats.bytes += sum(len(v) for _, v in rows)

        prefix_sem = asyncio.Semaphore(8)

        async def export_prefix(prefix: str) -> None:
            async with prefix_sem:
                keys = await list_prefix(pool, prefix, block_hash, stats)
                await asyncio.gather(*(fetch(c) for c in _chunks(keys, VALUE_CHUNK)))
                con.commit()

        await asyncio.gather(*(export_prefix(p) for p in prefixes))

        if con.execute("SELECT 1 FROM kv WHERE key = ?", (b":code",)).fetchone() is None:
            raise RuntimeError("export is missing :code (runtime wasm); refusing to write a broken snapshot")
        if con.execute("SELECT count(*) FROM kv").fetchone()[0] != stats.keys:
            raise RuntimeError(f"listed {stats.keys} keys but stored a different number; refusing to finish")

        con.executemany("INSERT OR REPLACE INTO meta (name, value) VALUES (?, ?)",
                        [(k, json.dumps(v)) for k, v in meta.items()])
        con.commit()
    finally:
        reporter.cancel()
        await pool.close()
        con.close()

    tmp.replace(out)
    progress(f"done: {stats.line()} -> {out} ({out.stat().st_size / 1e9:.2f} GB)")
    return out

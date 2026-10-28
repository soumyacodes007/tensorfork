import asyncio
import json
import sqlite3

import pytest

from tensorfork.snapshot import SCHEMA, Snapshot, prefix_upper_bound
from tensorfork.snapshot_server import RpcError, SnapshotRpc

BLOCK = "0x" + "ab" * 32
KEYS = ["0x0100", "0x0101", "0x01ff", "0x01ffff", "0x02", "0x0200aa", "0xff", "0xffff"]


@pytest.fixture
def snap(tmp_path):
    path = tmp_path / "s.sqlite"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO kv VALUES (?, ?)", [(bytes.fromhex(k[2:]), b"\x01" + bytes([i])) for i, k in enumerate(KEYS)])
    meta = {"block_hash": BLOCK, "block_number": 7, "genesis_hash": "0x" + "00" * 32, "header": {"number": "0x7"}}
    con.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
    con.commit()
    con.close()
    return Snapshot(path)


def test_prefix_upper_bound():
    assert prefix_upper_bound(b"\x01") == b"\x02"
    assert prefix_upper_bound(b"\x01\xff") == b"\x02"
    assert prefix_upper_bound(b"\xff\xff") is None


def test_keys_paged_respects_prefix_and_exclusive_start(snap):
    assert snap.keys_paged("0x01", 10, None) == ["0x0100", "0x0101", "0x01ff", "0x01ffff"]
    assert snap.keys_paged("0x01", 10, "0x01") == ["0x0100", "0x0101", "0x01ff", "0x01ffff"]
    assert snap.keys_paged("0x01", 2, "0x0100") == ["0x0101", "0x01ff"]
    assert snap.keys_paged("0x01ff", 10, None) == ["0x01ff", "0x01ffff"]
    assert snap.keys_paged("0xff", 10, None) == ["0xff", "0xffff"]
    assert snap.keys_paged("0x", 3, "0x01ffff") == ["0x02", "0x0200aa", "0xff"]


def test_point_reads(snap):
    assert snap.get("0x0101") == "0x0101"
    assert snap.get("0x0103") is None


def test_rpc_rejects_other_blocks_and_batches_reads(snap):
    rpc = SnapshotRpc(snap)
    call = lambda m, p: asyncio.run(rpc.dispatch(m, p, lambda *a: None))  # noqa: E731
    assert call("chain_getBlockHash", [7]) == BLOCK
    assert call("chain_getBlockHash", [0]) == "0x" + "00" * 32
    assert call("state_queryStorageAt", [["0x02", "0x03"], BLOCK]) == [
        {"block": BLOCK, "changes": [["0x02", "0x0104"], ["0x03", None]]}
    ]
    with pytest.raises(RpcError):
        call("state_getStorage", ["0x02", "0x" + "cd" * 32])

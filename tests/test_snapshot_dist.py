import json
import sqlite3

import pytest

from tensorfork import paths, snapshot_dist
from tensorfork.snapshot import SCHEMA


@pytest.fixture
def snap_file(tmp_path):
    path = tmp_path / "finney-latest.sqlite"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO kv VALUES (?, ?)", [(bytes([i]), bytes([i]) * 50) for i in range(200)])
    meta = {"block_hash": "0x" + "ab" * 32, "block_number": 42, "system_chain": "Bittensor",
            "runtime_version": {"specVersion": 486}, "exported_at": 1}
    con.executemany("INSERT INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
    con.commit()
    con.close()
    return path


def test_pack_then_pull_roundtrip(snap_file, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SNAPSHOTS", tmp_path / "installed")
    manifest_path = snapshot_dist.pack(snap_file, tmp_path / "dist", progress=lambda m: None)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["block_number"] == 42 and manifest["keys"] == 200 and manifest["spec_version"] == 486

    installed = snapshot_dist.pull(str(manifest_path), progress=lambda m: None)
    assert installed.read_bytes() == snap_file.read_bytes()
    assert (tmp_path / "installed" / "finney-latest.sqlite").read_bytes() == snap_file.read_bytes()


def test_pull_rejects_tampered_archive(snap_file, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SNAPSHOTS", tmp_path / "installed")
    manifest_path = snapshot_dist.pack(snap_file, tmp_path / "dist", progress=lambda m: None)
    archive = tmp_path / "dist" / json.loads(manifest_path.read_text())["file"]
    archive.write_bytes(archive.read_bytes()[:-1] + b"\x00")
    with pytest.raises(ValueError, match="sha256"):
        snapshot_dist.pull(str(manifest_path), progress=lambda m: None)

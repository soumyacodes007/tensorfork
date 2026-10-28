"""Hosted snapshots: pack, publish, and pull with verification.

A published snapshot is two files on one GitHub release tag (`snapshots`), so
the download URL never changes:

    manifest.json                 what's in it + sha256 of both forms
    finney-<block>.sqlite.zst     the snapshot, zstd-compressed

`pull` downloads, checks the compressed sha256, decompresses, checks the raw
sha256, and installs it as ~/.tensorfork/snapshots/finney-latest.sqlite.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import zstandard

from tensorfork import paths
from tensorfork.snapshot import Snapshot

RELEASE_TAG = "snapshots"
DEFAULT_REPO = os.environ.get("TENSORFORK_SNAPSHOT_REPO", "0xrick/tensorfork")
CHUNK = 1 << 20


def manifest_url(repo: str = DEFAULT_REPO) -> str:
    return os.environ.get("TENSORFORK_SNAPSHOT_INDEX",
                          f"https://github.com/{repo}/releases/download/{RELEASE_TAG}/manifest.json")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def pack(snapshot: Path, out_dir: Path | None = None, level: int = 10, progress=print) -> Path:
    """Compress `snapshot` and write manifest.json next to it. Returns the manifest path."""
    snapshot = Path(snapshot)
    snap = Snapshot(snapshot)
    out_dir = Path(out_dir or snapshot.parent / "dist")
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"finney-{snap.block_number}.sqlite.zst"
    archive = out_dir / name

    t0 = time.perf_counter()
    cctx = zstandard.ZstdCompressor(level=level, threads=-1)
    with snapshot.open("rb") as src, archive.open("wb") as dst:
        cctx.copy_stream(src, dst, read_size=CHUNK, write_size=CHUNK)
    progress(f"compressed {snapshot.stat().st_size / 1e6:.0f} MB -> {archive.stat().st_size / 1e6:.0f} MB "
             f"in {time.perf_counter() - t0:.0f}s")

    rv = snap.meta.get("runtime_version", {})
    manifest = {
        "format": 1,
        "chain": snap.meta.get("system_chain"),
        "block_number": snap.block_number,
        "block_hash": snap.block_hash,
        "spec_version": rv.get("specVersion"),
        "keys": len(snap),
        "exported_at": snap.meta.get("exported_at"),
        "file": name,
        "size": archive.stat().st_size,
        "sha256": _sha256(archive),
        "raw_size": snapshot.stat().st_size,
        "raw_sha256": _sha256(snapshot),
    }
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=1))
    return path


def publish(manifest_path: Path, repo: str = DEFAULT_REPO) -> str:
    """Upload a packed snapshot to the `snapshots` release of `repo` (needs `gh` auth). Public!"""
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    archive = manifest_path.parent / manifest["file"]
    exists = subprocess.run(["gh", "release", "view", RELEASE_TAG, "--repo", repo],
                            capture_output=True).returncode == 0
    if not exists:
        subprocess.run(["gh", "release", "create", RELEASE_TAG, "--repo", repo, "--title", "State snapshots",
                        "--notes", "Bittensor mainnet state snapshots for `tensorfork snapshot pull`.",
                        "--latest=false"], check=True)
    subprocess.run(["gh", "release", "upload", RELEASE_TAG, str(archive), str(manifest_path),
                    "--repo", repo, "--clobber"], check=True)
    return manifest_url(repo)


def _download(url: str, dest: Path, progress=print) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "tensorfork"})
    with urllib.request.urlopen(req, timeout=60) as r, dest.open("wb") as f:  # noqa: S310 - https URL
        total = int(r.headers.get("Content-Length") or 0)
        done, last = 0, 0.0
        while chunk := r.read(CHUNK):
            f.write(chunk)
            done += len(chunk)
            if time.time() - last > 5:
                last = time.time()
                pct = f" ({done / total:.0%})" if total else ""
                progress(f"  {done / 1e6:.0f} MB{pct}")


def pull(source: str | None = None, progress=print) -> Path:
    """Download and verify a published snapshot; install it as finney-latest.sqlite.

    `source` is a manifest URL or a local manifest path (default: the hosted index).
    """
    source = source or manifest_url()
    if source.startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"User-Agent": "tensorfork"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
            manifest = json.loads(r.read())
        base = source.rsplit("/", 1)[0]
        fetch = lambda name, dest: _download(f"{base}/{name}", dest, progress)  # noqa: E731
    else:
        local = Path(source).expanduser()
        manifest = json.loads(local.read_text())
        fetch = lambda name, dest: shutil.copyfile(local.parent / name, dest)  # noqa: E731

    paths.SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    final = paths.SNAPSHOTS / f"finney-{manifest['block_number']}.sqlite"
    if final.exists() and _sha256(final) == manifest["raw_sha256"]:
        progress(f"already have block {manifest['block_number']}")
    else:
        archive = paths.SNAPSHOTS / (manifest["file"] + ".partial")
        progress(f"downloading block {manifest['block_number']} ({manifest['size'] / 1e6:.0f} MB)")
        fetch(manifest["file"], archive)
        if _sha256(archive) != manifest["sha256"]:
            archive.unlink(missing_ok=True)
            raise ValueError("downloaded snapshot does not match the manifest sha256")
        tmp = final.with_suffix(".sqlite.partial")
        with archive.open("rb") as src, tmp.open("wb") as dst:
            zstandard.ZstdDecompressor().copy_stream(src, dst, read_size=CHUNK, write_size=CHUNK)
        archive.unlink()
        if _sha256(tmp) != manifest["raw_sha256"]:
            tmp.unlink(missing_ok=True)
            raise ValueError("decompressed snapshot does not match the manifest raw_sha256")
        tmp.replace(final)
    latest = paths.SNAPSHOTS / "finney-latest.sqlite"
    if latest.resolve() != final.resolve():
        latest.unlink(missing_ok=True)
        try:
            latest.symlink_to(final.name)
        except OSError:  # Windows without symlink rights
            shutil.copyfile(final, latest)
    progress(f"installed {final} (block {manifest['block_number']}, spec {manifest['spec_version']})")
    return final

"""Resolve a runtime wasm: a local file, or a Subtensor release tag (vNNN).

Release wasm is downloaded from the GitHub release and checked against the
sha256 in the release's `subtensor-digest.json` (srtool output) before use.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path

from tensorfork import paths

RELEASES = "https://github.com/RaoFoundation/subtensor/releases/download"


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 - fixed https URL
        return r.read()


# Substrate prefixes zstd-compressed runtime blobs with this 8-byte magic.
_ZSTD_WASM_MAGIC = bytes.fromhex("52bc537646db8e05")


def wasm_bytes(code: bytes) -> bytes:
    """Raw wasm from a runtime blob (decompressing Substrate's compact format if needed)."""
    if code.startswith(_ZSTD_WASM_MAGIC):
        import zstandard

        return zstandard.ZstdDecompressor().decompressobj().decompress(code[len(_ZSTD_WASM_MAGIC):])
    return code


def _leb128(data: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        b = data[i]
        i += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, i
        shift += 7


def _scale_compact(data: bytes, i: int) -> tuple[int, int]:
    mode = data[i] & 0b11
    if mode == 0:
        return data[i] >> 2, i + 1
    if mode == 1:
        return int.from_bytes(data[i:i + 2], "little") >> 2, i + 2
    if mode == 2:
        return int.from_bytes(data[i:i + 4], "little") >> 2, i + 4
    n = (data[i] >> 2) + 4
    return int.from_bytes(data[i + 1:i + 1 + n], "little"), i + 1 + n


def runtime_version(code: bytes) -> dict:
    """spec_name/impl_name/spec_version/... from the wasm's `runtime_version` custom section.

    This is the same data `System.set_code` checks (via `Core_version`) before
    an upgrade, read without executing the runtime.
    """
    wasm = wasm_bytes(code)
    if wasm[:4] != b"\0asm":
        raise ValueError("not a wasm runtime")
    i = 8
    while i < len(wasm):
        section_id = wasm[i]
        size, i = _leb128(wasm, i + 1)
        end = i + size
        if section_id == 0:
            name_len, j = _leb128(wasm, i)
            if wasm[j:j + name_len] == b"runtime_version":
                p = j + name_len
                out = {}
                for field in ("spec_name", "impl_name"):
                    n, p = _scale_compact(wasm, p)
                    out[field] = wasm[p:p + n].decode()
                    p += n
                for field in ("authoring_version", "spec_version", "impl_version"):
                    out[field] = int.from_bytes(wasm[p:p + 4], "little")
                    p += 4
                return out
        i = end
    raise ValueError("runtime has no runtime_version section")


def resolve(source: str) -> Path:
    """Path to a verified runtime wasm for `source` (a file path or `v486`-style tag)."""
    if not re.fullmatch(r"v\d+", source):
        path = Path(source).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"runtime wasm not found: {path}")
        return path

    out = paths.RUNTIMES / f"{source}.wasm"
    digest_file = paths.RUNTIMES / f"{source}.digest.json"
    if not out.exists():
        paths.RUNTIMES.mkdir(parents=True, exist_ok=True)
        digest = json.loads(_download(f"{RELEASES}/{source}/subtensor-digest.json"))
        wasm = _download(f"{RELEASES}/{source}/subtensor.wasm")
        got = "0x" + hashlib.sha256(wasm).hexdigest()
        if got != digest["sha256"]:
            raise ValueError(f"{source}: wasm sha256 {got} does not match release digest {digest['sha256']}")
        digest_file.write_text(json.dumps(digest))
        out.write_bytes(wasm)
    return out

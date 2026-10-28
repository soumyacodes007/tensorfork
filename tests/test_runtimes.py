import zstandard

from tensorfork.runtimes import _ZSTD_WASM_MAGIC, runtime_version


def _leb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _scale_str(s: str) -> bytes:
    data = s.encode()
    return bytes([len(data) << 2]) + data


def _wasm_with_version(spec_name: str, spec_version: int) -> bytes:
    payload = (_scale_str(spec_name) + _scale_str("node-subtensor")
               + (1).to_bytes(4, "little") + spec_version.to_bytes(4, "little") + (1).to_bytes(4, "little"))
    name = b"runtime_version"
    custom = _leb(len(name)) + name + payload
    other = b"\x01" + _leb(3) + b"\x00\x00\x00"  # an unrelated (type) section first
    return b"\0asm\x01\x00\x00\x00" + other + b"\x00" + _leb(len(custom)) + custom


def test_reads_runtime_version_from_plain_wasm():
    v = runtime_version(_wasm_with_version("node-subtensor", 487))
    assert v["spec_name"] == "node-subtensor" and v["spec_version"] == 487


def test_reads_runtime_version_from_compressed_blob():
    blob = _ZSTD_WASM_MAGIC + zstandard.ZstdCompressor().compress(_wasm_with_version("node-subtensor", 490))
    assert runtime_version(blob)["spec_version"] == 490

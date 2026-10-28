"""`tensorfork setup`: install the engine's runtime into ~/.tensorfork, on any OS.

Downloads a pinned Node.js build for this OS/CPU (checked against nodejs.org's
SHASUMS256), installs a pinned Chopsticks with that Node's own npm, and patches
Chopsticks' plugin loader, which turns file URLs into paths with
`URL.pathname`: on Windows that yields "/C:/..." and the engine can't start.
`fileURLToPath` is correct everywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from tensorfork import paths

NODE_VERSION = "v22.20.0"
CHOPSTICKS_VERSION = "1.5.2"

_PLUGIN_LOADER = Path("node_modules/@acala-network/chopsticks/dist/cjs/plugins/index.js")
_BROKEN = "Promise.resolve(location.pathname)"
_FIXED = 'Promise.resolve(require("url").fileURLToPath(location))'


def _node_dist() -> tuple[str, str]:
    """(dist name without extension, archive extension) for this machine."""
    system = {"Linux": "linux", "Darwin": "darwin", "Windows": "win"}.get(platform.system())
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if not system or not arch:
        raise RuntimeError(f"unsupported platform {platform.system()}/{platform.machine()}")
    ext = {"linux": "tar.xz", "darwin": "tar.gz", "win": "zip"}[system]
    return f"node-{NODE_VERSION}-{system}-{arch}", ext


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tensorfork"})
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 - fixed https URL
        return r.read()


def install_node(progress=print) -> Path:
    if paths.NODE.exists():
        return paths.NODE
    name, ext = _node_dist()
    archive_name = f"{name}.{ext}"
    base = f"https://nodejs.org/dist/{NODE_VERSION}"
    progress(f"downloading {archive_name}")
    sums = _fetch(f"{base}/SHASUMS256.txt").decode()
    expected = next((line.split()[0] for line in sums.splitlines() if line.endswith(f"  {archive_name}")), None)
    if expected is None:
        raise RuntimeError(f"{archive_name} not listed in SHASUMS256.txt")
    data = _fetch(f"{base}/{archive_name}")
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError(f"{archive_name}: sha256 mismatch, refusing to install")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / archive_name
        archive.write_bytes(data)
        if ext == "zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
        else:
            with tarfile.open(archive) as t:
                t.extractall(tmp, filter="data")
        shutil.rmtree(paths.NODE_DIR, ignore_errors=True)
        paths.NODE_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(Path(tmp) / name), paths.NODE_DIR)
    progress(f"node {NODE_VERSION} -> {paths.NODE_DIR}")
    return paths.NODE


def _npm_cli() -> Path:
    unix = paths.NODE_DIR / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
    return unix if unix.exists() else paths.NODE_DIR / "node_modules" / "npm" / "bin" / "npm-cli.js"


def install_engine(progress=print) -> Path:
    paths.ENGINE_DIR.mkdir(parents=True, exist_ok=True)
    pkg = {"name": "tensorfork-engine", "private": True,
           "dependencies": {"@acala-network/chopsticks": CHOPSTICKS_VERSION}}
    pkg_file = paths.ENGINE_DIR / "package.json"
    if not paths.CHOPSTICKS_JS.exists() or json.loads(pkg_file.read_text()) != pkg:
        pkg_file.write_text(json.dumps(pkg, indent=1))
        progress(f"installing chopsticks {CHOPSTICKS_VERSION}")
        env = dict(os.environ, PATH=f"{paths.NODE_BIN}{os.pathsep}{os.environ.get('PATH', '')}")
        subprocess.run([str(paths.NODE), str(_npm_cli()), "install", "--no-fund", "--no-audit", "--loglevel=error"],
                       cwd=paths.ENGINE_DIR, env=env, check=True)
    patch_engine()
    return paths.CHOPSTICKS_JS


def patch_engine() -> bool:
    """Apply the plugin-loader path fix. Returns True if the file was changed."""
    loader = paths.ENGINE_DIR / _PLUGIN_LOADER
    text = loader.read_text()
    if _BROKEN not in text:
        return False
    loader.write_text(text.replace(_BROKEN, _FIXED))
    return True


def setup(progress=print) -> None:
    install_node(progress)
    install_engine(progress)
    try:
        import bittensor  # noqa: F401

        sdk = "bittensor SDK available"
    except ImportError:
        sdk = ("bittensor SDK not installed (no Windows wheel for bittensor-core yet): the fork runs, "
               "SDK-based commands (fund, advance --tempo, runtime, compare, verify, replay) need WSL2/Linux/macOS")
    progress(f"ready: engine at {paths.CHOPSTICKS_JS}; {sdk}")


if __name__ == "__main__":
    setup()
    sys.exit(0)

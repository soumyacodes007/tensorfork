"""Where TensorFork keeps its runtime pieces and per-fork state."""

from __future__ import annotations

import os
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"

HOME = Path(os.environ.get("TENSORFORK_HOME", Path.home() / ".tensorfork"))
NODE_DIR = HOME / "node"
# Node's Windows zip keeps node.exe at the top level; unix tarballs use bin/.
NODE_BIN = NODE_DIR if WINDOWS else NODE_DIR / "bin"
NODE = NODE_BIN / ("node.exe" if WINDOWS else "node")
ENGINE_DIR = HOME / "engine"
# Run Chopsticks' entry script with our node directly: no shell shims (.cmd/.bin), same on every OS.
CHOPSTICKS_JS = ENGINE_DIR / "node_modules" / "@acala-network" / "chopsticks" / "chopsticks.cjs"
SNAPSHOTS = HOME / "snapshots"
RUNTIMES = HOME / "runtimes"
RUNS = HOME / "run"
JS = Path(__file__).parent / "js"


def run_dir(name: str = "default") -> Path:
    d = RUNS / name
    d.mkdir(parents=True, exist_ok=True)
    return d

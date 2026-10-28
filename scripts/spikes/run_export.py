"""Export a full finney state snapshot from the local node."""

import asyncio
import os
import sys
from pathlib import Path

from tensorfork.snapshot import export_snapshot

ENDPOINT = os.environ.get("NODE_ENDPOINT", "ws://127.0.0.1:9946")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.tensorfork/snapshots/finney-latest.sqlite"))

asyncio.run(export_snapshot(ENDPOINT, OUT, progress=lambda m: print(m, flush=True)))

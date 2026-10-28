"""Spike: warm the engine cache for SubtensorModule + Commitments at a fork block."""

import asyncio
import os
import sys
from pathlib import Path

from tensorfork.prefetch import block_hash_for, pallet_prefix, prefetch

ENDPOINT = os.environ.get("FORK_ENDPOINT", "wss://archive.chain.opentensor.ai:443")
BLOCK = int(sys.argv[1]) if len(sys.argv) > 1 else 9382573
PALLETS = sys.argv[2].split(",") if len(sys.argv) > 2 else ["Commitments", "SubtensorModule"]
DB = Path(os.path.expanduser("~/.tensorfork/cache/finney.sqlite"))


async def main():
    block_hash = await block_hash_for(ENDPOINT, BLOCK)
    print("block", BLOCK, block_hash)
    await prefetch(ENDPOINT, DB, block_hash, [pallet_prefix(p) for p in PALLETS], connections=8,
                   progress=lambda m: print(m, flush=True))


asyncio.run(main())

"""Spike 2: can the unmodified bittensor SDK read real mainnet state through the fork?"""

import sys
import time

import bittensor as bt

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8000"
NETUID = int(sys.argv[2]) if len(sys.argv) > 2 else 64


def timed(label, fn):
    t0 = time.perf_counter()
    out = fn()
    print(f"{label}: {time.perf_counter() - t0:.2f}s")
    return out


sub = bt.Subtensor(URL)
print("block", sub.block, "spec", sub.spec_version)
print("subnets namespace:", [m for m in dir(sub.subnets) if not m.startswith("_")])

mg = timed(f"metagraph({NETUID}) cold", lambda: sub.subnets.metagraph(netuid=NETUID))
timed(f"metagraph({NETUID}) warm", lambda: sub.subnets.metagraph(netuid=NETUID))
print("metagraph type:", type(mg).__name__)
fields = [f for f in dir(mg) if not f.startswith("_")]
print("fields:", fields[:60])
for name in ("name", "n", "num_uids", "hotkeys", "stake", "total_stake", "tempo"):
    if hasattr(mg, name):
        val = getattr(mg, name)
        print(f"  {name}:", val[:3] if isinstance(val, (list, tuple)) else val)

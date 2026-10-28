"""End-to-end check on an offline fork (scripts/offline-stack.sh running).

1. Unmodified SDK reads the chain and SN<netuid>'s real metagraph (cold + warm).
2. Impersonation: transfer TAO out of a real account with no key, then check balances.
"""

import sys
import time

import bittensor as bt

from tensorfork.impersonate import ImpersonatedSigner

URL = "ws://127.0.0.1:9944"
NETUID = int(sys.argv[1]) if len(sys.argv) > 1 else 64
ALICE = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"


def timed(label, fn):
    t0 = time.perf_counter()
    out = fn()
    print(f"{label}: {time.perf_counter() - t0:.2f}s")
    return out


sub = bt.Subtensor(URL)
print("block", sub.block, "spec", sub.spec_version)

mg = timed(f"metagraph({NETUID}) cold", lambda: sub.subnets.metagraph(netuid=NETUID))
timed(f"metagraph({NETUID}) warm", lambda: sub.subnets.metagraph(netuid=NETUID))
neurons = getattr(mg, "neurons", None) or []
print(f"SN{NETUID}: {len(neurons)} neurons;", "public fields:",
      [f for f in dir(mg) if not f.startswith("_")][:25])

# Pick a real coldkey with free balance from the subnet to impersonate.
coldkeys = []
for n in neurons:
    ck = getattr(n, "coldkey", None) or getattr(n, "coldkey_ss58", None)
    if ck and ck not in coldkeys:
        coldkeys.append(ck)
whale, whale_bal = None, None
for ck in coldkeys[:40]:
    bal = sub.balances.get(ck)
    if bal.tao > 2:
        whale, whale_bal = ck, bal
        break
if whale is None:
    sys.exit("no coldkey with > 2 TAO free among the first 40 neurons")

print("impersonating", whale, "free", whale_bal)
before = sub.balances.get(ALICE)
result = sub.execute(bt.Transfer(dest_ss58=ALICE, amount_tao=1), ImpersonatedSigner(whale))
print("transfer success:", result.success, "error:", getattr(result, "error", None))
after = sub.balances.get(ALICE)
print("Alice balance", before, "->", after, "| whale now", sub.balances.get(whale))
print("block now", sub.block)

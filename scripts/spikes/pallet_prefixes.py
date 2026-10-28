"""Map the pallet prefixes seen in the engine cache to pallet names (twox128)."""

import os
import sqlite3
from collections import Counter

import xxhash


def twox128(name: str) -> str:
    data = name.encode()
    return (xxhash.xxh64(data, seed=0).intdigest().to_bytes(8, "little")
            + xxhash.xxh64(data, seed=1).intdigest().to_bytes(8, "little")).hex()


NAMES = ["System", "Balances", "SubtensorModule", "Commitments", "Swap", "Drand", "Crowdloan",
         "Proxy", "Timestamp", "AdminUtils", "Registry", "Sudo", "Multisig", "Scheduler",
         "Preimage", "Aura", "Grandpa", "Ethereum", "EVM", "BaseFee", "SafeMode", "Utility",
         "TransactionPayment", "Contracts", "MevShield", "Shield", "LiquidAlpha", "Balancer"]
by_prefix = {twox128(n): n for n in NAMES}

db = os.path.expanduser("~/.tensorfork/cache/finney.sqlite")
con = sqlite3.connect(db)
counts = Counter()
for (key,) in con.execute("select key from key_value_pair"):
    counts[key[2:34]] += 1
for prefix, n in counts.most_common():
    print(f"{by_prefix.get(prefix, '?' + prefix):>20}  {n}")
print("SubtensorModule =", twox128("SubtensorModule"))

"""Example `tensorfork compare` script: one subnet's economics as JSON.

    tensorfork compare --runtime v487 -- python examples/subnet_economics.py 64

TensorFork runs this on the current runtime and on the candidate (from the same
state, after the same number of blocks) and diffs the JSON.
"""

import json
import os
import sys

import bittensor as bt

NETUIDS = [int(x) for x in sys.argv[1:]] or [64]

out = {}
with bt.Subtensor(os.environ.get("TENSORFORK_URL", "local")) as sub:
    for netuid in NETUIDS:
        mg = sub.subnets.metagraph(netuid=netuid, commitments=False)
        neurons = mg.neurons
        top = sorted(neurons, key=lambda n: n.incentive, reverse=True)[:5]
        out[f"sn{netuid}"] = {
            "neurons": len(neurons),
            "validators": sum(1 for n in neurons if n.validator_permit),
            "emission_rao": sum(n.emission.rao for n in neurons),
            "incentive_sum": round(sum(n.incentive for n in neurons), 6),
            "dividends_sum": round(sum(n.dividends for n in neurons), 6),
            "top_miners_by_incentive": [n.uid for n in top],
        }
print(json.dumps(out))

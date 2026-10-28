"""Example: pytest against a fork of real mainnet state.

    tensorfork up            # or set TENSORFORK_SNAPSHOT=... and let the plugin start one
    pytest examples/test_on_fork.py

The `tensorfork` fixture resets the fork after every test, so tests can't leak
state into each other.
"""

import bittensor as bt

ALICE = "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY"
BOB = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"


def test_subnet_64_is_real(tensorfork):
    mg = tensorfork.subtensor().subnets.metagraph(netuid=64, commitments=False)
    assert len(mg.neurons) == 256
    assert any(n.validator_permit for n in mg.neurons)


def test_impersonated_transfer(tensorfork):
    sub = tensorfork.subtensor()
    tensorfork.fund(ALICE, 100)
    result = sub.execute(bt.Transfer(dest_ss58=BOB, amount_tao=40), tensorfork.impersonate(ALICE))
    assert result.success, result.error
    assert sub.balances.get(BOB).tao >= 40


def test_state_was_reset(tensorfork):
    # The previous test funded Alice and paid Bob; the fixture rolled that back.
    assert tensorfork.subtensor().balances.get(BOB).tao < 40

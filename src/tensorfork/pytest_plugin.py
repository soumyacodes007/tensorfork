"""pytest plugin: test your validator/miner against a fork of real mainnet state.

    def test_weights_land(tensorfork):
        sub = tensorfork.subtensor()
        ...

The session attaches to a running fork (`tensorfork up`), or starts one from
TENSORFORK_SNAPSHOT. Every test gets the fork reset to the same state: a
checkpoint taken before the test is restored after it.
"""

from __future__ import annotations

import os

import pytest

from tensorfork.engine import load_state
from tensorfork.fork import Fork


@pytest.fixture(scope="session")
def tensorfork_session():
    name = os.environ.get("TENSORFORK_NAME", "default")
    if load_state(name):
        yield Fork.attach(name)
        return
    snapshot = os.environ.get("TENSORFORK_SNAPSHOT")
    if not snapshot:
        pytest.skip("no running fork and TENSORFORK_SNAPSHOT is not set")
    with Fork.start(snapshot=snapshot, name=name) as fork:
        yield fork


@pytest.fixture
def tensorfork(tensorfork_session):
    label = "__pytest"
    tensorfork_session.checkpoint(label)
    try:
        yield tensorfork_session
    finally:
        tensorfork_session.restore(label)

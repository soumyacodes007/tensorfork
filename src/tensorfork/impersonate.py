"""Sign as any account on a fork, without its key.

The engine runs with `mock-signature-host`, which accepts any signature that
starts with 0xdeadbeef and is padded with 0xcd. `ImpersonatedSigner` satisfies
the SDK's `bittensor.Signer` protocol and always returns that magic signature,
so `sub.execute(intent, ImpersonatedSigner(ss58))` works for any address.

Only meaningful on a fork: a real chain rejects these signatures.
"""

from __future__ import annotations

from dataclasses import dataclass

MOCK_SIGNATURE = bytes.fromhex("deadbeef") + b"\xcd" * 60  # 64 bytes, sr25519-sized

CRYPTO_SR25519 = 1


@dataclass(frozen=True)
class ImpersonatedSigner:
    """A `bittensor.Signer` for `ss58_address` that signs with the fork's mock signature."""

    ss58_address: str

    @property
    def public_key(self) -> bytes:
        from bittensor.keyfiles import Keypair

        return bytes.fromhex(Keypair(ss58_address=self.ss58_address).public_key.hex().removeprefix("0x"))

    @property
    def crypto_type(self) -> int:
        return CRYPTO_SR25519

    def sign(self, payload: bytes) -> bytes:
        return MOCK_SIGNATURE

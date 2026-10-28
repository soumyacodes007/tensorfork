"""The TensorFork developer API: a running fork and what you can do to it.

    from tensorfork import Fork

    with Fork.start(snapshot="~/.tensorfork/snapshots/finney-latest.sqlite") as fork:
        sub = fork.subtensor()                       # unmodified bittensor SDK
        whale = fork.impersonate("5F...")            # sign as anyone, no key
        fork.fund("5F...", 1_000)                    # mint TAO
        fork.advance(tempo=64)                       # run until SN64's epoch
        fork.checkpoint("before")                    # instant save / restore
        fork.set_runtime("v487")                     # sudo-upgrade like mainnet would
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from websockets.sync.client import connect

from tensorfork import engine, paths, runtimes
from tensorfork.impersonate import ImpersonatedSigner

try:
    import bittensor as bt
except ImportError:  # native Windows: bittensor-core ships no Windows wheel yet
    bt = None

RAO_PER_TAO = 10**9
SUDO_FEE_FLOAT_TAO = 1_000  # balance given to the sudo account (if lower) before a set_code


class ForkError(RuntimeError):
    pass


def _sdk():
    if bt is None:
        raise ForkError("this needs the bittensor SDK, which isn't installed here (bittensor-core has no "
                        "Windows wheel yet). The fork itself runs; use WSL2, Linux or macOS for SDK features.")
    return bt


@dataclass
class Fork:
    state: engine.RunState
    owned: bool = False  # True when this object started the stack (and so stops it)
    _sub: bt.Subtensor | None = field(default=None, repr=False)

    # ------------------------------------------------------------- lifecycle

    @classmethod
    def start(cls, *, snapshot: str | Path | None = None, node: str | None = None,
              block: int | None = None, name: str = "default", **ports) -> "Fork":
        cfg = engine.StackConfig(name=name, snapshot=str(Path(snapshot).expanduser()) if snapshot else None,
                                 node=node, block=block, **ports)
        return cls(engine.start(cfg), owned=True)

    @classmethod
    def attach(cls, name: str = "default") -> "Fork":
        state = engine.load_state(name)
        if state is None:
            raise ForkError(f"no running fork '{name}'; start one with `tensorfork up`")
        return cls(state)

    def stop(self) -> None:
        if self._sub is not None:
            self._sub.close()
            self._sub = None
        engine.stop(self.name)

    def __enter__(self) -> "Fork":
        return self

    def __exit__(self, *exc) -> None:
        if self.owned:
            self.stop()

    @property
    def name(self) -> str:
        return self.state.config["name"]

    @property
    def url(self) -> str:
        """What clients connect to: `bt.Subtensor(fork.url)`, `btcli --network <url>`."""
        return self.state.url

    def subtensor(self) -> bt.Subtensor:
        if self._sub is None:
            self._sub = _sdk().Subtensor(self.url)
        return self._sub

    # ------------------------------------------------------------- engine RPC

    def rpc(self, method: str, params: list | None = None, timeout: float = 600) -> Any:
        """One JSON-RPC call straight to the engine (dev_* methods live here)."""
        with connect(self.state.engine_url, max_size=2**30, open_timeout=10) as ws:
            ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}))
            msg = json.loads(ws.recv(timeout=timeout))
        if "error" in msg:
            raise ForkError(f"{method}: {msg['error'].get('message', msg['error'])}")
        return msg["result"]

    def head(self) -> tuple[int, str]:
        header = self.rpc("chain_getHeader")
        return int(header["number"], 16), self.rpc("chain_getBlockHash", [])

    # ------------------------------------------------------------- powers

    def impersonate(self, ss58: str) -> ImpersonatedSigner:
        """A signer for any account. Pass it wherever the SDK takes a wallet."""
        return ImpersonatedSigner(ss58)

    def set_storage(self, storage: dict | list) -> str:
        """Overwrite storage at the head: `{Pallet: {Item: value}}` or raw `[[key, value], ...]`."""
        return self.rpc("dev_setStorage", [storage])

    def fund(self, ss58: str, tao: float) -> bt.Balance:
        """Set `ss58`'s free balance to `tao` (keeping nonce/reserved) and adjust total issuance."""
        sub = self.subtensor()
        account = sub.query(bt.storage.System.Account, [ss58])
        new_free = int(round(tao * RAO_PER_TAO))
        delta = new_free - int(account["data"]["free"])
        if account["providers"] == 0:
            account["providers"] = 1  # an account with a balance must have a provider reference
        account["data"]["free"] = new_free
        issuance = int(sub.query(bt.storage.Balances.TotalIssuance)) + delta
        self.set_storage({
            "System": {"Account": [[[ss58], _jsonable(account)]]},
            "Balances": {"TotalIssuance": str(issuance)},
        })
        self.rpc("dev_newBlock", [{}])  # make the write visible at a new head for every client
        return sub.balances.get(ss58)

    def advance(self, blocks: int | None = None, *, to: int | None = None, tempo: int | None = None) -> dict:
        """Build blocks: a count, or up `to` a block number; or run subnet `tempo`'s epoch now.

        Every fork block executes Subtensor's full coinbase (all subnets) in the
        engine's wasm interpreter, about 30 s each on a laptop, so building a
        whole tempo (~360 blocks) is slow. `tempo=netuid` instead schedules that
        subnet's epoch for the very next block through `PendingEpochAt` (the
        mechanism owner-triggered epochs use) and builds one block: the real
        epoch code runs on the real accrued emission.
        """
        before, _ = self.head()
        t0 = time.perf_counter()
        extra: dict = {}
        if tempo is not None:
            sub = self.subtensor()
            last_before = sub.query(bt.storage.SubtensorModule.LastEpochBlock, [tempo])
            self.set_storage({"SubtensorModule": {"PendingEpochAt": [[[tempo], before + 1]]}})
            self.rpc("dev_newBlock", [{}])
            self._fresh_client()
            last_after = self.subtensor().query(bt.storage.SubtensorModule.LastEpochBlock, [tempo])
            extra = {"netuid": tempo, "epoch_ran": last_after != last_before,
                     "last_epoch_block": {"before": last_before, "after": last_after}}
        else:
            self.rpc("dev_newBlock", [{"to": to} if to is not None else {"count": blocks or 1}])
            self._fresh_client()
        after, head_hash = self.head()
        return {"from": before, "to": after, "built": after - before,
                "seconds": round(time.perf_counter() - t0, 2), "head": head_hash, **extra}

    # ------------------------------------------------------------- checkpoints

    def _checkpoints_file(self) -> Path:
        return paths.run_dir(self.name) / "checkpoints.json"

    def checkpoints(self) -> dict[str, dict]:
        f = self._checkpoints_file()
        return json.loads(f.read_text()) if f.exists() else {}

    def checkpoint(self, label: str) -> dict:
        number, block_hash = self.head()
        cps = self.checkpoints()
        cps[label] = {"number": number, "hash": block_hash}
        self._checkpoints_file().write_text(json.dumps(cps, indent=1))
        return cps[label]

    def restore(self, label: str) -> dict:
        cp = self.checkpoints().get(label)
        if cp is None:
            raise ForkError(f"no checkpoint '{label}'")
        self.rpc("dev_setHead", [cp["hash"]])
        self._fresh_client()
        return cp

    def _fresh_client(self) -> None:
        # The SDK caches head-derived data; reconnect after the chain jumps.
        if self._sub is not None:
            self._sub.close()
            self._sub = None

    # ------------------------------------------------------------- runtime

    def set_runtime(self, source: str, *, unchecked: bool = False) -> dict:
        """Apply a runtime (wasm path or release tag like `v487`) the way mainnet does:
        the real sudo key (impersonated) dispatches the code change, then the next
        block runs the new runtime's migrations.

        `System.set_code` first checks the new runtime's version by instantiating
        it, which the engine can't do for Subtensor (it imports BLS12-381 host
        functions the engine lacks). So TensorFork runs the same checks itself on
        the wasm's embedded version (same spec_name, higher spec_version) and
        dispatches `set_code_without_checks`. `unchecked=True` skips the checks,
        e.g. to re-apply the same version or downgrade.
        """
        wasm = runtimes.resolve(source).read_bytes()
        new_version = runtimes.runtime_version(wasm)
        sub = self.subtensor()
        spec_before = sub.spec_version
        if not unchecked:
            current = self.rpc("state_getRuntimeVersion")
            if new_version["spec_name"] != current["specName"]:
                raise ForkError(f"spec_name {new_version['spec_name']!r} != chain's {current['specName']!r} "
                                "(System.set_code would reject this; pass unchecked to force)")
            if new_version["spec_version"] <= current["specVersion"]:
                raise ForkError(f"spec_version {new_version['spec_version']} is not above the chain's "
                                f"{current['specVersion']} (System.set_code would reject this; pass unchecked to force)")
        sudo = sub.query(bt.storage.Sudo.Key)
        # A ~2.7 MB set_code pays a large length fee; mainnet's sudo account may not hold it.
        topped_up = None
        if sub.balances.get(sudo).tao < SUDO_FEE_FLOAT_TAO:
            topped_up = str(self.fund(sudo, SUDO_FEE_FLOAT_TAO))
            sub = self.subtensor()
        inner = sub.compose(bt.calls.System.set_code_without_checks(code="0x" + wasm.hex()))
        result = sub.submit_call(bt.calls.Sudo.sudo(call=inner), self.impersonate(sudo))
        if not result.success:
            raise ForkError(f"sudo set_code failed: {result.error}")
        sudo_ok = [e for e in result.events if e.get("event_id") == "Sudid"]
        if sudo_ok and "Err" in json.dumps(sudo_ok[0].get("attributes")):
            raise ForkError(f"set_code dispatched but failed inside sudo: {sudo_ok[0]['attributes']}")
        code_updated = any(e.get("event_id") == "CodeUpdated" for e in result.events)
        self.rpc("dev_newBlock", [{}])  # the upgrade block: on_runtime_upgrade migrations run here
        self._fresh_client()
        sub = self.subtensor()
        events = sub.query(bt.storage.System.Events)
        return {"spec_before": spec_before, "spec_after": sub.spec_version, "new_runtime": new_version,
                "checks": "skipped" if unchecked else "spec_name equal, spec_version higher",
                "code_updated": code_updated,
                "sudo": sudo, "sudo_topped_up_to": topped_up, "upgrade_block": self.head()[0],
                "upgrade_block_events": _summarize_events(events)}

    # ------------------------------------------------------------- compare

    def compare(self, script: list[str], runtime: str, *, unchecked: bool = False) -> dict:
        """Run `script` on the current runtime and on `runtime`, from the same state, and diff.

        The script gets TENSORFORK_URL in its environment and must print one JSON
        object as its last stdout line. Both sides build the same number of blocks
        from the same state (the upgrade costs two: set_code, then the migration
        block), so the diff isolates the runtime's effect.
        """
        self.checkpoint("__compare_base")
        try:
            self.rpc("dev_newBlock", [{"count": 2}])
            self._fresh_client()
            before = _run_json(script, self.url)
            self.restore("__compare_base")
            upgrade = self.set_runtime(runtime, unchecked=unchecked)
            after = _run_json(script, self.url)
        finally:
            self.restore("__compare_base")
        return {"runtime": upgrade, "before": before, "after": after, "diff": diff(before, after)}

    # ------------------------------------------------------------- verify

    def verify(self, netuid: int, reference: str, *, block: int | None = None) -> dict:
        """Compare this fork's metagraph with a reference node's at the fork block."""
        bt = _sdk()
        sub = self.subtensor()
        block = block if block is not None else self.state_block()
        mine = sub.subnets.metagraph(netuid=netuid, block=block)
        with bt.Subtensor(reference) as ref:
            theirs = ref.subnets.metagraph(netuid=netuid, block=block)
        a, b = _metagraph_fingerprint(mine), _metagraph_fingerprint(theirs)
        mismatches = {k: {"fork": a[k], "reference": b.get(k)} for k in a if a[k] != b.get(k)}
        return {"netuid": netuid, "block": block, "neurons": a["n"], "match": not mismatches,
                "mismatches": mismatches}

    def state_block(self) -> int:
        """The block the fork was taken at (snapshot block or configured block)."""
        cfg = self.state.config
        if cfg.get("snapshot"):
            from tensorfork.snapshot import Snapshot

            return Snapshot(Path(cfg["snapshot"])).block_number
        return cfg.get("block") or self.head()[0]


# ----------------------------------------------------------------- replay


def replay(block: int, node: str, *, name: str = "replay", port: int = 9964,
           engine_port: int = 8010, upstream_port: int = 9965) -> dict:
    """Re-execute mainnet block `block` on a fork of its parent and compare outcomes.

    Needs a node that still holds state for `block - 1` (a lite node keeps its
    `--state-pruning` window). Signed extrinsics are re-applied in order; each
    one's result (success, or the decoded failure) is compared with mainnet's.
    """
    bt = _sdk()
    real_rpc = _RawRpc(node)
    block_hash = real_rpc.call("chain_getBlockHash", [block])
    raw_block = real_rpc.call("chain_getBlock", [block_hash])["block"]
    extrinsics = raw_block["extrinsics"]
    signed = [(i, x) for i, x in enumerate(extrinsics) if _is_signed(x)]
    with bt.Subtensor(node) as real:
        real_events = real.query(bt.storage.System.Events, block=block)
        real_info = real.block_info(block)

    with Fork.start(node=node, block=block - 1, name=name, port=port,
                    engine_port=engine_port, upstream_port=upstream_port) as fork:
        fork.rpc("dev_setBlockBuildMode", ["Manual"])
        if signed:
            fork.rpc("dev_newBlock", [{"transactions": [x for _, x in signed]}])
        else:
            fork.rpc("dev_newBlock", [{}])
        number, fork_hash = fork.head()
        fork_block = fork.rpc("chain_getBlock", [fork_hash])["block"]
        fork_signed_idx = [i for i, x in enumerate(fork_block["extrinsics"]) if _is_signed(x)]
        fork_events = fork.subtensor().query(bt.storage.System.Events)

    with bt.Subtensor(node) as real:
        namer = _error_namer(real)
        real_out = _outcomes(real_events, namer)
    fork_out = _outcomes(fork_events, namer)
    calls = [_call_name(x) for x in (real_info.extrinsics if real_info else [])]
    rows = []
    for pos, (real_idx, _) in enumerate(signed):
        fork_idx = fork_signed_idx[pos] if pos < len(fork_signed_idx) else None
        mainnet = real_out.get(real_idx, "missing")
        replayed = fork_out.get(fork_idx, "not included") if fork_idx is not None else "not included"
        rows.append({"index": real_idx, "call": calls[real_idx] if real_idx < len(calls) else "?",
                     "mainnet": mainnet, "replay": replayed, "same": mainnet == replayed})
    return {"block": block, "hash": block_hash, "replayed_at": number, "extrinsics": rows,
            "all_same": all(r["same"] for r in rows)}


class _RawRpc:
    def __init__(self, url: str):
        self.url = url

    def call(self, method: str, params: list) -> Any:
        with connect(self.url, max_size=2**30, open_timeout=15) as ws:
            ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
            msg = json.loads(ws.recv(timeout=120))
        if "error" in msg:
            raise ForkError(f"{method}: {msg['error']}")
        return msg["result"]


def _is_signed(extrinsic_hex: str) -> bool:
    """SCALE: compact length prefix, then a version byte whose top bit marks a signed extrinsic."""
    raw = bytes.fromhex(extrinsic_hex.removeprefix("0x"))
    mode = raw[0] & 0b11
    offset = {0: 1, 1: 2, 2: 4}.get(mode, 1 + (raw[0] >> 2) + 4)
    return bool(raw[offset] & 0x80)


def _outcomes(events: list, namer=None) -> dict[int, str]:
    out: dict[int, str] = {}
    for ev in events or []:
        idx = ev.get("extrinsic_idx")
        if idx is None:
            continue
        if ev.get("event_id") == "ExtrinsicSuccess":
            out[idx] = "ok"
        elif ev.get("event_id") == "ExtrinsicFailed":
            out[idx] = "failed: " + _dispatch_error(ev.get("attributes", {}), namer)
    return out


def _dispatch_error(attrs: dict, namer=None) -> str:
    err = attrs.get("dispatch_error", attrs)
    if isinstance(err, dict) and "Module" in err:
        mod = err["Module"]
        if mod.get("name"):
            return mod["name"]
        named = namer(err) if namer else None
        return named or f"module {mod.get('index')} error {mod.get('error')}"
    return json.dumps(err, default=str)


def _error_namer(sub: bt.Subtensor):
    """`dispatch_error -> "Pallet::Error"` via the SDK's runtime codec, or None.

    The codec is internal to the SDK, so this looks it up defensively; if the
    SDK's internals change, replay falls back to raw module/error indexes.
    """
    try:
        from bittensor._transport.utils.receipt import dispatch_error_message

        sub.block  # connect
        seen, todo, runtimes = set(), [sub], None
        while todo and runtimes is None:
            obj = todo.pop()
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            cand = getattr(obj, "_runtimes", None)
            if cand is not None and hasattr(cand, "codec_at"):
                runtimes = cand
                break
            todo.extend(v for k, v in vars(obj).items() if k.startswith("_") and hasattr(v, "__dict__"))
        if runtimes is None:
            return None
        codec = sub._call(runtimes.codec_at(None))
        pallets = {}

        def name(err: dict) -> str | None:
            msg = dispatch_error_message(err, codec)
            if not msg:
                return None
            index = err["Module"]["index"]
            pallet = pallets.get(index) or _pallet_name(codec, index)
            pallets[index] = pallet
            return f"{pallet}::{msg['name']}" if pallet else msg["name"]

        return name
    except Exception:  # noqa: BLE001 - naming is best-effort
        return None


def _pallet_name(codec, index: int) -> str | None:
    for attr in ("pallet_name", "module_name"):
        fn = getattr(codec, attr, None)
        if callable(fn):
            try:
                return fn(index)
            except Exception:  # noqa: BLE001
                return None
    return None


def _call_name(extrinsic: Any) -> str:
    call = extrinsic.get("call") if isinstance(extrinsic, dict) else None
    if isinstance(call, dict):
        return f"{call.get('call_module')}.{call.get('call_function')}"
    return "?"


def _summarize_events(events: list) -> dict[str, int]:
    """{"Pallet.Event": count}, most frequent first."""
    from collections import Counter

    counts = Counter(f"{e.get('module_id')}.{e.get('event_id')}" for e in events or [])
    return dict(counts.most_common())


def _jsonable(value: Any) -> Any:
    """Big ints as strings so the engine's JSON -> SCALE encoder keeps full precision."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53:
        return str(value)
    return value


def _metagraph_fingerprint(mg) -> dict:
    neurons = getattr(mg, "neurons", None) or []

    def col(attr: str) -> list:
        return [getattr(n, attr, None) for n in neurons]

    return {
        "n": len(neurons),
        "hotkeys": list(mg.hotkeys) if hasattr(mg, "hotkeys") else col("hotkey"),
        "coldkeys": list(mg.coldkeys) if hasattr(mg, "coldkeys") else col("coldkey"),
        "uids": col("uid"),
        "stake": [str(s) for s in col("stake")],
        "tempo": getattr(mg, "tempo", None),
        "last_step": getattr(mg, "last_step", None),
    }


def _run_json(script: list[str], url: str) -> dict:
    import os

    env = dict(os.environ, TENSORFORK_URL=url)
    proc = subprocess.run(script, capture_output=True, text=True, env=env, timeout=1800)
    if proc.returncode != 0:
        raise ForkError(f"script failed ({proc.returncode}):\n{proc.stderr[-2000:]}")
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        raise ForkError("script printed nothing; it must print one JSON object as its last line")
    return json.loads(lines[-1])


def diff(before: Any, after: Any, path: str = "") -> dict:
    """Flat {path: {before, after, delta?}} of every leaf that changed."""
    out: dict = {}
    if isinstance(before, dict) and isinstance(after, dict):
        for k in sorted(set(before) | set(after), key=str):
            out.update(diff(before.get(k), after.get(k), f"{path}.{k}" if path else str(k)))
        return out
    if before != after:
        entry: dict = {"before": before, "after": after}
        if isinstance(before, (int, float)) and isinstance(after, (int, float)) and not isinstance(before, bool):
            entry["delta"] = after - before
        out[path or "."] = entry
    return out


def main_print(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=1, default=str)
    sys.stdout.write("\n")

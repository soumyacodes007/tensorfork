# TensorFork

Fork Bittensor mainnet on your laptop: real subnets, stakes, validators and metagraphs, with no risk to real TAO. It's Bittensor's equivalent of Ethereum's `anvil --fork-url`.

**Why it exists:** Subtensor shipped 11 runtime releases between Oct 5 and Oct 26, 2026. Each one changed something a subnet depends on: emissions (eTAO), epoch ordering, proxies or fees. Subnet builders had three places to test, and none fit: an empty localnet, a testnet that doesn't match mainnet, and mainnet itself, with real money. The chain team tests every runtime against a mainnet clone. TensorFork gives every subnet builder the same thing in one command.

> Status: hackathon build (Oct 2026). Every feature below was verified end to end against real mainnet state; [research.md](research.md) has the measurements and the bugs found along the way, and [PRD.md](PRD.md) has the original plan.

```bash
pip install git+https://github.com/soumyacodes007/tensorfork   # or: docker build -t tensorfork .
tensorfork setup                               # Node + engine into ~/.tensorfork (once)
tensorfork snapshot pull                       # all of mainnet state: 176 MB download
tensorfork up                                  # offline fork
btcli subnets list --network ws://127.0.0.1:9944
```

```python
import bittensor as bt
from tensorfork import Fork

fork = Fork.attach()
sub = fork.subtensor()                          # the unmodified SDK
sub.subnets.metagraph(netuid=64)                # the real SN64, all 256 neurons
sub.execute(bt.Transfer(dest_ss58=me, amount_tao=1), fork.impersonate(whale))  # no key needed
```

## What you can do

| Command | What it does |
|---|---|
| `tensorfork up [--snapshot F \| --node URL --block N]` | Start a fork in the background. Offline from a snapshot, or lazy-loaded from a node you run |
| `tensorfork fund SS58 TAO` | Set any account's free balance; total issuance is kept consistent |
| `fork.impersonate(SS58)` | A `bittensor.Signer` that signs as any account (the engine accepts its mock signature) |
| `tensorfork advance --tempo NETUID` | Run that subnet's real epoch on the next block, via `PendingEpochAt` (the mechanism owner-triggered epochs use) |
| `tensorfork advance --blocks N \| --to N` | Build blocks one by one. This is exact but slow; see the performance section |
| `tensorfork checkpoint save\|load\|list LABEL` | Instant save and restore of fork state |
| `tensorfork runtime set v487 \| FILE [--unchecked]` | Upgrade the runtime the way mainnet does: the real sudo key (impersonated) dispatches the code change, then migrations run on the next block. Release wasm is checked against the release's sha256 digest. Before upgrading, TensorFork applies `set_code`'s own checks (same spec name, higher spec version) by reading the wasm's embedded version: the engine can't run them itself, since they load the new runtime, which imports BLS host functions. `--unchecked` skips the checks, e.g. to re-apply or downgrade |
| `tensorfork compare --runtime v487 -- python script.py` | Run your script before and after an upgrade, from the same state and after the same number of blocks, then diff its JSON output |
| `tensorfork replay BLOCK` | Re-execute a real mainnet block on a fork of its parent, and compare every extrinsic's result with mainnet's, with errors named |
| `tensorfork verify NETUID` | Check the fork's metagraph against an independent node at the fork block |
| `tensorfork snapshot export \| info` | Export full chain state from a node you run, or inspect a snapshot |
| `tensorfork snapshot pack \| publish \| pull` | Host snapshots: zstd-compressed with a manifest (sha256 of the archive and of the raw file), uploaded to one fixed GitHub release tag, then downloaded and verified on `pull` |
| pytest fixture `tensorfork` | A fork per session, reset after every test |

### Testing an unreleased runtime

```bash
bash scripts/build-runtime.sh main                    # srtool build, same recipe as the release train
tensorfork compare --runtime ~/.tensorfork/runtimes/main.wasm -- python examples/subnet_economics.py 64
bash scripts/extract-runtime.sh testnet out.wasm      # or take the runtime a published node image embeds
```

## How it works

```
your code / btcli ──> compat proxy :9944 ──> Chopsticks engine :8000 ──> upstream :9955
                      (SDK quirks)           (real runtime 486 wasm)     snapshot file (offline)
                                                                         or a node you run
```

- **Engine:** [Chopsticks](https://github.com/AcalaNetwork/chopsticks) runs Subtensor's real runtime against forked state. It runs with `mock-signature-host` (for impersonation) and `allow-unresolved-imports` (Subtensor imports BLS12-381 host functions that Chopsticks lacks).
- **Compat proxy** (`src/tensorfork/js/proxy.mjs`):
  - adds the missing `0x` to the SDK's hex parameters
  - aliases `account_nextIndex` to `system_accountNextIndex`
  - answers the SDK's runtime-version lookups for the block before the fork (which the engine never saw) with the current head's runtime
- **Snapshots** (`snapshot.py`, `snapshot_server.py`, `snapshot_dist.py`): the whole mainnet state at one block is **3.34M keys, 160 MB of values, a 0.56 GB SQLite file, and a 176 MB zstd download**. It's exported in about 15 minutes from a local node, and the engine reads it with no network.
- **Local node** (`scripts/local-node.sh`): the official image, warp-synced over P2P. It's used to export snapshots and to replay recent blocks. With `--history-backfill=skip` it stays at about 1.8 GB instead of backfilling toward about 128 GB. Public RPCs can't feed a lazy fork: they rate-limit it (HTTP 429).

## Performance (laptop, WSL2)

| Operation | Time |
|---|---|
| `tensorfork up` (offline) | ~10 s |
| SN64 metagraph (256 neurons) | 23 s cold, 6 s warm |
| One fork block | **~30–90 s**: each block runs Subtensor's full coinbase for all subnets in the engine's wasm interpreter |
| `advance --tempo` (a real epoch now) | ~90 s, instead of ~45 min to build 90 blocks |
| Checkpoint restore | instant |
| Replay of a 10-extrinsic block | a few minutes, mostly the fork's first block |

## Install

**pip** (Linux, macOS, Windows):

```bash
pip install git+https://github.com/soumyacodes007/tensorfork
tensorfork setup           # downloads a pinned, checksum-verified Node + Chopsticks into ~/.tensorfork
```

From a clone: `uv sync` (or `pip install .`), then `uv run pytest tests` for the unit tests.

**Docker:**

```bash
git clone https://github.com/soumyacodes007/tensorfork && cd tensorfork
docker build -t tensorfork .
docker run --rm -v ~/.tensorfork/snapshots:/data/snapshots tensorfork snapshot pull    # once
docker run --rm -p 127.0.0.1:9944:9944 -v ~/.tensorfork/snapshots:/data/snapshots tensorfork
```

The container runs `tensorfork up --foreground --host 0.0.0.0`. Publish the port on `127.0.0.1` as shown; `docker stop` shuts the fork down cleanly (about 0.3 s).

**Native Windows:** the fork engine runs natively. `tensorfork setup` patches Chopsticks' plugin loader, which builds `/C:/...` paths, and TensorFork avoids its habit of treating `C:\...` config paths as URLs. The bittensor SDK still has no Windows wheel (`bittensor-core`), so the commands that drive the SDK need WSL2, Linux or macOS: `fund`, `advance --tempo`, `runtime`, `compare`, `verify` and `replay`. Everything else works natively: `up`, `down`, `status`, `advance --blocks`, checkpoints, and snapshots. An SDK in WSL can drive a Windows-native fork over `127.0.0.1`.

**Your own node** (to export snapshots and to replay recent blocks):

```bash
bash scripts/local-node.sh start     # official image, warp sync, ~1.8 GB, RPC on 127.0.0.1:9946
tensorfork snapshot export           # ~15 min -> ~/.tensorfork/snapshots/finney-latest.sqlite
```

## Project layout

| Path | What it is |
|---|---|
| `src/tensorfork/cli.py` | The `tensorfork` command |
| `src/tensorfork/fork.py` | The Python API: `Fork`, `replay`, runtime upgrade, compare, verify |
| `src/tensorfork/engine.py` | Starts and stops the three-process stack (Windows, Linux, macOS) |
| `src/tensorfork/snapshot*.py` | Snapshot export, reader, offline upstream server, pack/publish/pull |
| `src/tensorfork/js/` | Compat proxy and upstream shield (Node) |
| `src/tensorfork/setup_engine.py` | `tensorfork setup`: Node, Chopsticks and the Windows patch |
| `src/tensorfork/pytest_plugin.py` | The `tensorfork` pytest fixture |
| `scripts/` | Local node, runtime build/extract, wheel and Docker tests; `spikes/` holds the day-1 probes |
| `examples/` | A compare script and example tests against a fork |

## Limits

- **Consensus and finality are mocked.** Only on-chain state transitions are real.
- **Calls that reach BLS12-381 host functions fail** on the fork (Subtensor's timelock and drand paths).
- **Building blocks is slow** (see Performance), so prefer `advance --tempo` over building whole tempos.
- **Replay needs a node with the parent block's state.** A lite node keeps its `--state-pruning` window, about 13 h here.
- **No hosted snapshot is published yet.** `snapshot pull` with no arguments reads the `snapshots` release of `$TENSORFORK_SNAPSHOT_REPO` (default `soumyacodes007/tensorfork`). Until a snapshot is published there, pass a manifest URL or path, or export your own.

# TensorFork — Product Requirements Document

**Status:** Draft v0.1 · **Date:** 2026-10-28 · **Context:** 15-day Bittensor hackathon · **Research:** [research.md](research.md)

> One command gives you a local copy of the real Bittensor chain: real subnets, stakes, validators and metagraphs. You can do anything on it without risking real TAO. It's Bittensor's equivalent of Ethereum's `anvil --fork-url`.

---

## 1. Problem

Bittensor's runtime changes faster than subnet code can keep up. There were **11 runtime release tags between Oct 5 and Oct 26, 2026**. Those releases changed:
- emission weighting (eTAO)
- epoch ordering (Null epochs)
- proxy types (Weights/Validate)
- coldkey-swap semantics
- fee paths

Any of these can change what a validator's weights earn, or break a miner's or validator's code.

Today a subnet developer has three places to test, and all three are wrong:

| Option | Problem |
|---|---|
| Localnet (Docker) | Empty chain: only subnets 0 and 1, with no real stake, validators or metagraphs |
| Testnet | Different subnets and stake from mainnet; slow; shared with everyone |
| Mainnet | Real money; you can't undo or replay anything |

The chain team already solved this **for themselves**. Subtensor ships a mainnet-clone regression harness (`clones/`) that every runtime PR runs through. But it's built for runtime CI:
- you need a Rust build of the node
- warp-syncing state takes about 35 min, and the chainspec is about 2 GB
- the database is wiped on every start
- there's no impersonation, no time travel, and no Python or `btcli` ergonomics

Evidence that upgrades bite real state: issue #2793, where a missed migration shrank subnet reserves at upgrade, and issue #3156, where TotalStake drifted away from the sum of SubnetTAO.

## 2. Users

| Persona | Job to be done | What they need |
|---|---|---|
| **Subnet owner** | "Will my incentive mechanism still behave after the next runtime, under eTAO and Null epochs?" | Run their validator against the real metagraph under the next runtime |
| **Validator operator** | "Why did my `set_weights` fail at block X?" / "Is the new Weights proxy set up correctly?" | Replay a block; try proxy and coldkey operations on real state |
| **Miner / tooling dev** | "Does my code work against SN64's real metagraph?" | Real metagraph, real hyperparameters, instant reset |
| **Researcher / analyst** | "What happens to SN X's emissions if stake moves like this?" | Impersonate whales, move stake, advance tempos, read the results |

Not targeted: Subtensor runtime developers. Their CI harness already serves them.

## 3. Goals and non-goals

**Goals (hackathon)**
1. Get from nothing to a usable fork of mainnet at block N in **≤ 5 minutes** on a laptop, and **≤ 30 s** on a re-run from cache.
2. Make the fork usable by unmodified `bittensor` SDK and `btcli` code: just point the network at `ws://127.0.0.1:9944`.
3. Offer four core powers on the fork: **impersonate**, **fund**, **advance time / tempo**, **swap runtime**.
4. **Replay** a historical mainnet block or extrinsic and explain what changed or why it failed.
5. Provide a **pytest fixture** so a subnet can run "my validator still works on the real chain" in CI.

**Non-goals**
- A hosted or shared fork service (later work).
- Real consensus, finality, networking or P2P behaviour.
- EVM/Frontier-specific tooling (P2).
- Replacing Subtensor's runtime CI harness. We complement it and could feed back upstream.
- Testing anything off-chain, such as miner–validator HTTP traffic. The fork covers chain state only.

## 4. Product shape

A **Python CLI plus library** (`pip install tensorfork`) that drives a fork engine (see §6), ships as a Docker image as well, and includes a pytest plugin.

```bash
tensorfork up                          # fork finney at latest finalized block
tensorfork up --block 9369470          # fork at a specific block
tensorfork up --runtime v487.wasm      # fork, then upgrade to a candidate runtime
tensorfork fund <ss58> 10000           # give any account TAO
tensorfork impersonate <ss58>          # print an impersonated-signer handle
tensorfork advance --blocks 360        # build blocks
tensorfork advance --tempo 64          # run until SN64's next epoch fires
tensorfork snapshot save before-test   # instant checkpoint
tensorfork snapshot load before-test   # instant reset
tensorfork replay --block 9342490      # re-execute a block, diff storage, decode errors
tensorfork compare --runtime v487.wasm -- python my_validator_check.py
                                       # run a script on live vs next runtime, diff results
```

```python
import bittensor as bt
from tensorfork import Fork

with Fork(block=9369470) as fork:            # starts or attaches to the engine
    owner = fork.impersonate("5F...owner")    # signs anything as this coldkey, no key needed
    fork.fund(owner, 1_000)
    sub = bt.Subtensor(fork.url)
    mg = sub.metagraph(64)                    # the real SN64 metagraph
    sub.set_weights(wallet=owner, netuid=64, uids=[...], weights=[...])
    fork.advance(tempo=64)                    # until SN64's epoch runs
    print(sub.metagraph(64).incentive)        # see the effect
```

```python
# pytest plugin
def test_validator_on_real_chain(tensorfork):   # fixture yields a fresh snapshot per test
    run_my_validator_once(tensorfork.url, netuid=64)
    assert tensorfork.events(name="WeightsSet")
```

## 5. Requirements

### P0: must ship for the demo
| ID | Requirement | Acceptance |
|---|---|---|
| F1 | `up`: fork finney at the latest block or a given block | RPC on `:9944`; `bt.Subtensor(url).block()` returns the fork block |
| F2 | Fidelity: real metagraphs, stake and hyperparameters at the fork block | `metagraph(64)` on the fork equals `metagraph(64)` read from a mainnet archive node at the same block (UIDs, stake, hotkeys) |
| F3 | Impersonation: submit any extrinsic as any account, with no key | Impersonated `set_weights` and `add_stake` succeed on the fork |
| F4 | Fund: mint TAO to any account | Balance changes after `fund` |
| F5 | Advance: build N blocks, or run until a subnet's next epoch | After `advance --tempo X`, SN X's incentives and dividends change in line with the weights set |
| F6 | Snapshot save/load | Restoring takes ≤ 5 s and state matches |
| F7 | Unmodified SDK and `btcli` work against the fork | `btcli subnets list --network ws://127.0.0.1:9944` works |

### P1: strong demo, ship if time allows
| ID | Requirement | Acceptance |
|---|---|---|
| F8 | Runtime swap: `up --runtime <wasm or vNNN>` applies a candidate runtime, including its migrations | The `system.version` spec matches the candidate; migration events are shown |
| F9 | `compare`: run one script on the current and the next runtime, then diff its output and key storage | A report shows emission and weight differences per subnet |
| F10 | `replay --block`: re-execute a historical block and show a storage diff plus decoded errors | For a block containing a failed `set_weights`, the report shows the exact error (for example `SettingWeightsTooFast`) and the relevant storage values |
| F11 | pytest plugin with a per-test snapshot reset | The example test passes in CI |

### P2: later
- Tracing for a single extrinsic (which storage keys it read and wrote).
- An EVM/precompile helper.
- A hosted, shareable fork URL.
- A "what changed in this runtime" summary per subnet, generated automatically on every new release.

### Non-functional
- Runs on Linux and macOS. Windows via WSL2 or Docker.
- No real key material is ever needed. Refuse to start if the RPC is bound to anything other than localhost unless `--expose` is passed, and warn that the fork's sudo key is public, the same rule the chain team applies to its clone previews.
- RPC load on public endpoints stays bounded: cache everything locally and prefetch in parallel within rate limits.

## 6. Technical approach

Two possible engines. Day 1 of the build decides which one leads.

| | **A. Chopsticks engine** (lazy fork) | **B. Native clone engine** (Subtensor's `build-patched-spec`) |
|---|---|---|
| How | Lazily fetch storage over RPC and run the real runtime wasm locally; cache in SQLite | Warp-sync the full state into a patched chainspec and run the real `node-subtensor` with Alice as authority and sudo |
| Time to first fork | Seconds to minutes (lazy loading) | About 35–45 min the first time; minutes after that (genesis init of the ~2 GB spec) |
| Impersonation | `mock-signature-host` plus storage overrides | Needs sudo calls or spec patching, so harder |
| Time travel | `dev_newBlock` with a count; storage overrides for epoch counters | 12 s mainnet blocks, unless the node is rebuilt with fast blocks |
| Replay | Built in (`run-block`, `dry-run`) | Has to be built |
| Fidelity | Runtime is exact; consensus and inherents are mocked | Highest: real node and real runtime |
| Fit | **Laptop UX: the product we're pitching** | CI-grade fidelity and a fallback |

**Plan:** lead with engine A for the UX. Keep engine B as `--engine native` for high-fidelity runs and as the fallback if A fails the spike.

**Day-1 spike (go/no-go for A):**
1. Point Chopsticks at a finney RPC and produce 10 blocks.
2. Read `metagraph(64)` with the Python SDK, timing both a cold read and a warm read.
3. Submit a `set_weights` with a mock signature, from the SDK through a TensorFork signer shim.
4. Check that the SDK's runtime-API calls (`state_call`) and any custom RPC methods it uses are answered.

If the custom RPCs are missing, implement them as Chopsticks RPC plugins that call the runtime APIs.

**Key components:**
- **Engine adapter:** start, stop and health-check the engine; manage ports and the cache directory.
- **Signer shim:** a `bittensor`-compatible keypair object that emits Chopsticks' accepted mock signature, so the SDK signs "as" any ss58 address.
- **Bittensor helpers:**
  - `fund`, which writes the `System.Account` balance and keeps `TotalIssuance` consistent
  - `advance(tempo=)`, which reads the subnet's `Tempo` and `BlocksSinceLastStep` (or uses owner- or sudo-triggered epochs if the current runtime exposes them; **to verify**) and builds blocks until the epoch event appears
- **Replay and report:** wrap `run-block` and decode storage diffs and errors with the runtime metadata into human-readable output.
- **Runtime fetcher:** resolve `vNNN` to the release wasm by downloading it from the GitHub release assets. v486 introduced reproducible runtime builds and published already-deployed runtimes. Verify the hash.

## 7. Demo script (3 minutes)

1. `tensorfork up`. In under a minute, show `btcli subnets list` on the fork listing all 128 real subnets.
2. **Impersonate** SN64's top validator, change its weights, then `advance --tempo 64`. Show incentives moving on real SN64 state.
3. **Time machine:** `replay` a real mainnet block where a `set_weights` failed, and show the decoded reason.
4. **Next runtime:** `compare --runtime v487.wasm -- python check_emissions.py`, and show the per-subnet emission difference the upgrade causes before it hits mainnet.
5. Close: "The chain team tests every runtime against a mainnet clone. Now every subnet can."

## 8. Success metrics

- **Hackathon:**
  - all P0 requirements pass their acceptance criteria
  - time to fork is under 5 min cold and under 30 s warm
  - the F2 fidelity check matches on 3 large subnets
  - at least one P1 feature (F8 or F10) works live
- **Adoption (after the hackathon):**
  - 10 subnets use the pytest plugin in CI
  - we catch one real breaking change before mainnet and document it publicly

## 9. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Chopsticks can't run the Subtensor runtime correctly (Aura/Frontier inherents, custom runtime APIs) | Medium | Day-1 spike; RPC plugins; fall back to engine B |
| Lazy loading a full metagraph over public RPC is slow or rate-limited | Medium-high | Parallel prefetch of the keys behind the hot pallets (SubtensorModule maps for the targeted netuids); SQLite cache; ship a prebuilt cache for a recent block |
| `advance --tempo` is slow (each block runs emissions for 128 subnets in wasm) | Medium | Measure in the spike; override epoch counters for the target subnet; document the trade-off |
| The chain team extends `clones/` into a public tool | Low-medium | Position TensorFork as the developer-facing layer and upstream useful pieces; use their snapshot if it becomes public |
| Runtime churn breaks TensorFork itself | Medium | Decode everything from metadata, never hardcode storage layouts; run our own clone-style smoke test against each new release |
| Misuse: people treat fork results as guarantees | Low | Every report states that "consensus/finality mocked; on-chain state transition only" |

## 10. 15-day plan

| Days | Work |
|---|---|
| 1 | Engine spike (go/no-go for A), measure timings, lock the architecture |
| 2–3 | `up`, engine adapter, cache, F7 compatibility with SDK and btcli |
| 4–5 | Signer shim plus impersonation (F3) and `fund` (F4) |
| 6–7 | `advance` blocks and tempo (F5); snapshots (F6) |
| 8 | Fidelity checker against an archive node (F2) on 3 subnets |
| 9–10 | Runtime swap (F8) plus the release-wasm fetcher |
| 11 | `replay` with a decoded report (F10) |
| 12 | `compare` (F9), if F8 is solid |
| 13 | pytest plugin (F11), Docker image, README |
| 14 | Demo hardening; record a backup video of every demo step |
| 15 | Deck, rehearsal, buffer |

**Cut order if behind:** F9 → F11 → F8. Never cut F2 (fidelity) or F3 (impersonation), because they are the product.

## 11. Open questions

1. Can Chopsticks drive Subtensor's runtime end to end? This is the Day-1 spike.
2. Does the current runtime expose owner- or sudo-triggered epochs? Issue #2633 has a spec for "Configurable Tempo & Owner-Triggered Epochs".
3. Which public archive RPC can we use for forks of historical blocks, and what are its rate limits?
4. Is the chain team's nightly `mainnet-snapshot` downloadable by the public? If it is, engine B's cold start drops from about 40 min to a download.
5. Distribution: pip with an auto-managed Node/Chopsticks, or Docker-first? Most subnet developers are Python-first.

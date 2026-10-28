# TensorFork — research notes

Collected 2026-10-28. These are the facts the PRD relies on, with sources. Items marked **unverified** still need a hands-on check.

## 1. Runtime release cadence (the "why now")

From `gh api repos/opentensor/subtensor/releases` (the repo now redirects to **RaoFoundation/subtensor**):

| Tag | Published | Notes from release body |
|---|---|---|
| v486 | 2026-10-26 | Reproducible runtime builds; finalized on mainnet from block 9,369,470 |
| v484 | 2026-10-23 | Liquid-alpha consensus controls in precompiles; root claims for 128-subnet baskets; metagraph symbol fix; stake reporting through subnet dissolution |
| v483 | 2026-10-26 | Finalized on mainnet from block 9,342,487 |
| v482 | 2026-10-21 | **Validate and Weights proxy types**; coldkey-swap merge; **due Null epoch runs first**; fee payment through 3 proxy levels |
| v481 | 2026-10-19 | **eTAO**: per-subnet emission weights with a baseline-anchored effective TAO weight; root paid its live baseline share every block |
| v479 | 2026-10-26 (tag) | Finalized on mainnet from block 9,286,202 |
| v478 | 2026-10-13 | Restore staking-hotkey associations in migration batches |
| v477 / v476 (proposed) / v475 / v473 | 2026-10-05 → 10-08 | — |

- **11 runtime release tags between Oct 5 and Oct 26** (one marked "proposed"). The pitch's "seven in three weeks" understates it.
- v479 → v486 went live on mainnet across 83,268 blocks, about 11.6 days at 12 s per block.
- Every feature the pitch names is real: eTAO (v481), Null epoch (v482), Weights/Validate proxies (v482).
- Upgrades really can break things. For example, issue #2793 (closed 2026-06): an unwired migration "shrinks every affected subnet's reserves at upgrade". Issue #3156: TotalStake drifted away from the sum of SubnetTAO.

## 2. What already exists

### a. Subtensor's internal mainnet-clone harness (`clones/` in the monorepo)
- `node-subtensor build-patched-spec` warp-syncs mainnet from a bootnode and exports the state as a **~2 GB raw chainspec**. It patches block authorship (Aura/Grandpa) and the sudo key over to **//Alice**.
- The warp-sync peer search alone has taken **up to 35 min**. A cold genesis-init of the 2 GB spec then takes several more minutes.
- `start-local-clone.sh` runs one local validator authoring as Alice on `ws://127.0.0.1:9944`. It **wipes the database on every start**.
- `update-runtime-with-alice.ts` sudo-upgrades the clone to a locally built wasm, which is how they preview a runtime.
- `local-clone-checkpoint.sh` handles create/restore by tarring the spec plus the database.
- CI uses it in three ways:
  - `runtime-checks.yml` runs on every PR: upgrade, smoke test, then the Python SDK metadata drift gate.
  - `refresh-mainnet-snapshot.yml` builds a nightly snapshot. It's stored as a 7-day GitHub artifact plus an R2 mirror for their CI fleet, and it isn't clear whether the public can download it.
  - `mainnet-clone-preview.yml` is triggered by a label. It keeps a clone of the PR's runtime alive for up to 6 h behind a public tunnel.
- **Who it serves:** runtime developers inside the monorepo. It needs a Rust build of the node, has no impersonation, no time travel, no Python ergonomics, and no Bittensor-level commands.

### b. Localnet (official)
- `docker run ghcr.io/raofoundation/subtensor-localnet:devnet`. It uses fast blocks (0.25 s) by default and pre-funds Alice with 1M TAO.
- Image tags: `devnet`, `testnet`, `main`, `sha-<commit>`, `v<spec_version>`.
- **Only subnets 0 and 1 exist.** It has no real stake, validators or metagraphs, which is exactly the "empty chain" problem.

### c. Chopsticks (Acala, generic Substrate)
- `npx @acala-network/chopsticks --endpoint=<wss>` forks any Substrate chain. Fetched storage is cached in SQLite (`db-{network}-{block}.sqlite`), and `--resume` continues from it.
- Built-in features:
  - `run-block`: replay a block and print changed storage, with HTML output.
  - `dry-run`: simulate an extrinsic.
  - `-s/--import-storage`: storage overrides.
  - `wasm-override`: swap in a different runtime.
  - `dev_newBlock`: manual block building.
  - `mock-signature-host`: fake signatures.
- Known limitations: the transaction pool, inherents and finality are mocked, and "anything beyond on-chain state transition may fail in production".
- Subtensor's `ts-tests/package.json` lists `@acala-network/chopsticks 1.2.3` as a dependency, but its Moonwall config only defines dev, zombienet and smoke environments. I found **no chopsticks environment and no public guide for forking finney with Chopsticks**. **Unverified:** whether Chopsticks runs Subtensor's runtime correctly (Aura, Frontier EVM, custom runtime APIs) and how fast it lazy-loads a full metagraph over public RPC.

### d. try-runtime-cli
Tests migrations against live state. It's aimed at Rust runtime authors and does nothing for a validator or miner.

### e. Testnet
It has its own subnets, stake and validators, so it doesn't match mainnet behaviour. It's also slow, and its state is shared with everyone.

## 3. Gap

Nobody gives **subnet builders and validator operators** a one-command fork of *real mainnet state* with:
- impersonation
- time travel to the next tempo
- replay of a failed extrinsic
- running their own code against today's runtime and the next one, side by side

The chain team's harness proves the approach works, but it's built for their CI, not for subnet developers.

## 4. Day-1 spike results (2026-10-28)

What works:
- Chopsticks 1.5.2 **forks finney and serves Subtensor runtime 486**. The fork was at block 9,382,573, and the unmodified bittensor 11.3.0 SDK connected and read the block and spec version.
- Two compatibility fixes were needed, both now in place:
  1. `--allow-unresolved-imports`: the runtime imports a custom host function, `ext_host_calls_bls12_381_mul_projective_g2_version_1`, that Chopsticks lacks. Calls that actually reach BLS code will still fail. **To check:** timelock/drand weight paths.
  2. A compatibility proxy (`src/tensorfork/js/proxy.mjs`). The SDK sends `state_call` and similar hex params **without `0x`**; a real node accepts that and Chopsticks rejects it. The proxy also exposes port 9944, so `--network local` works.

Platform:
- Chopsticks doesn't run on native Windows: plugin paths resolve to `/C:/...`, so the module isn't found.
- `bittensor-core` has no Windows wheel.
- **Windows means WSL2.** `scripts/setup-linux.sh` installs a checksum-verified Node plus Chopsticks into `~/.tensorfork`.

The blocker: public RPC can't feed a lazy fork.
- The metagraph runtime call pulls storage **one key per round trip, about 0.56 s each**. 763 reads took 431 s, and the SN64 metagraph needs thousands. A cold read would take about an hour.
- Any single upstream stall over 60 s kills the call (Chopsticks' internal timeout). Stalls happened on `:code` (the ~2.7 MB runtime) and on individual SubtensorModule keys.
- `entrypoint-finney` rejects bursts of historical reads (0 of 50 succeeded).
- `archive.chain.opentensor.ai` handles single bursts (50 of 50 in 0.9 s), but drops sockets (1006), stalls handshakes, and finally returned **HTTP 429** to parallel prefetching.
- The metagraph read touched two pallets: `SubtensorModule` (2,013 keys cached so far) and `Commitments` (1,005).
- Already built to cope: an upstream shield (`src/tensorfork/js/upstream.mjs`: socket pool, retries, chunked `queryStorageAt`) and a bulk prefetcher (`src/tensorfork/prefetch.py`, which writes straight into Chopsticks' `key_value_pair` cache). Both work, but neither gets around a 429.

Fix options, roughly in order of robustness:
1. **A local Subtensor node as the upstream.** Warp-sync over P2P, which has no RPC rate limits. The published ghcr image from `docker.yml` can serve as the base, run with `--state-pruning` above the fork's age. Chopsticks then lazy-loads from `ws://127.0.0.1` at LAN latency. Costs one sync of about 35–45 min and a few GB of disk.
2. **A dedicated RPC provider** (an archive endpoint with an API key). Fast to try, but it's a paid dependency.
3. **Gentle prefetch** against the public archive: 1–2 connections, cached once per pinned block. Free, but slow (likely hours for the full SubtensorModule).

## 5. Local node + offline snapshot (2026-10-28) — working

**Local node** (`scripts/local-node.sh`, official `ghcr.io/raofoundation/subtensor:latest`, WSL Docker):
- Warp sync reached the chain head and finality in **~14 min**, with no RPC rate limits involved.
- At the head, the volume was **1.6 GB**, which is the live state.
- The node then backfills block history from genesis, even with `--blocks-pruning`. At block #212,416 of 9.38M the volume was **4.1 GB** and still growing. That backfill is the source of the docs' ~128 GB figure, and a fork doesn't need it. **Open:** stop or cap the backfill.
- Memory use: about 4 GB of the 15 GB WSL has. Under export load the node fell about 180 blocks behind, then caught up.

**Snapshot export** (`tensorfork snapshot export`):
- **Full mainnet state at one block: 3,338,774 keys, 160 MB of values, a 0.56 GB SQLite file.**
- 395 storage prefixes. The export took **15 min** from the local node with 0 retries.
- Bug found and fixed: `state_getKeysPaged`'s start key is exclusive, so starting at the prefix skipped any key equal to it. That dropped `:code` and plain storage values. The export now starts from `null`, and refuses to finish if `:code` is missing or the stored count differs from the listed count.

**Offline fork** (`scripts/offline-stack.sh SNAPSHOT`: snapshot server → Chopsticks → compat proxy), with no network at all:
- The unmodified SDK reads block 9,383,352 and runtime spec 486.
- **SN64 metagraph: all 256 neurons. 23 s cold, 6 s warm.** For comparison, over public RPC this failed and was estimated at about an hour.
- **Impersonation:** using `tensorfork.impersonate.ImpersonatedSigner`, a 1 TAO transfer from a real coldkey (`5Gxh84…`) succeeded with no key. Alice went from 0 to 1 TAO, and the fork built block 9,383,353.
- The proxy needed two more shims:
  1. The SDK asks for the runtime version at the fork block's *parent*, which the engine never saw. A `null` answer now falls back to the head's version.
  2. The SDK calls `account_nextIndex`; Chopsticks only implements it as `system_accountNextIndex`, so the proxy renames it.

## 6. Disk fix and all PRD features (2026-10-28)

**Disk:** the node has a `--history-backfill` flag, default `keep`. With `skip` (and a fresh volume) it reached the head at **1.76 GB**, with zero history-backfill activity. It was at 1.79 GB after 10 more minutes, filling the 4,000-block pruning window. Before the fix it was 4.4 GB and climbing toward ~128 GB.

**Features, each verified end to end on the offline fork from snapshot block 9,383,352:**

| PRD | Feature | Result |
|---|---|---|
| F1 | `tensorfork up` | Background stack up in ~10 s |
| F2 | `verify 64` | Matches the public archive at the fork block: 256 neurons, hotkeys, coldkeys, UIDs, stake, tempo, last_step |
| F3 | impersonate | 1 TAO transfer from a real coldkey with no key; pytest transfer test passes |
| F4 | `fund` | Alice set to τ5,000; TotalIssuance adjusted |
| F5 | `advance --tempo 64` | Real SN64 epoch ran: `LastEpochBlock` 9,383,083 → 9,383,353, in 90 s |
| F6 | checkpoints | Instant restore to a saved head |
| F7 | SDK + btcli | `btcli subnets list --network ws://127.0.0.1:9944` lists the real subnets |
| F8 | `runtime set v486 --unchecked` | Downloaded and sha256-verified the release wasm, topped up the sudo account for the length fee, then the impersonated sudo dispatched `Sudo.Sudid` and `System.CodeUpdated`; upgrade block built |
| F9 | `compare` | Same runtime gives an **empty diff** on SN64 economics, so the fork is deterministic |
| F10 | `replay 9383847` | All 8 signed extrinsics reproduce mainnet's results, including a failed `Utility.batch_all` (`RootClaimTooHeavy`) |
| F11 | pytest plugin | 3 example tests pass, including a check that state resets between tests |

**Performance finding:** the engine builds Subtensor blocks at **~2 per minute** (30–90 s each, CPU-bound). Every block runs the full coinbase for all subnets in the engine's wasm interpreter. Building a full tempo is impractical, so `advance --tempo` sets `PendingEpochAt[netuid]` to the next block (the runtime's own owner-trigger path; see `should_run_epoch_with_tempo`) and builds one block.

**Not done yet:** a hosted snapshot download, pip/Docker packaging, testing a *newer* runtime (v486 is the latest release), and native (non-WSL) Windows.

## 7. Packaging, hosting, Windows, newer runtime (2026-10-28/29)

The repo moved to its own folder (`~/Desktop/tensorfork`). The bash stack scripts named in sections 4–5 (`setup-linux.sh`, `spike-*.sh`, `offline-stack.sh`) were replaced by `tensorfork setup` and `tensorfork up`.

**Hosted snapshots:** `snapshot pack` compresses the 563 MB snapshot to a **176 MB** zstd archive in 8 s and writes a manifest with the sha256 of both the archive and the raw file. `pull` checks both hashes; a test confirms it rejects a tampered archive. `publish` uploads to a fixed `snapshots` release tag; it hasn't been run, because it's public.

**pip:** a clean-room test (fresh venv, empty `TENSORFORK_HOME`) passed: wheel install, `tensorfork setup` (checksum-verified Node 22.20 plus Chopsticks 1.5.2, with the patch), `up`, an SDK read, `down`. The wheel includes `js/*.mjs` and the pytest entry point. `websockets` is now declared directly; it used to arrive only through bittensor.

**Docker:** a 1.5 GB image whose command is `up --foreground --host 0.0.0.0`. The host SDK reads the fork through `127.0.0.1:9944`.
- Bug found: as PID 1, `tensorfork` left its exited children as zombies, `kill(pid, 0)` still reported them alive, and each stop waited out its full 5 s grace period. `docker stop` took 11 s and ended in SIGKILL.
- Fix: reap with `waitpid(WNOHANG)`. Now the fork stops in **0.3 s** and the container exits 0.

**Native Windows** (Python 3.12, Node 22 win-x64, no WSL). It works for the engine and the SDK-free commands:
- `setup`, `snapshot pull`, `up`, `status` (the processes survive the CLI exiting), a raw SN64 `get_metagraph` call (51 KB in 14.9 s), `advance --blocks 1` (54 s), checkpoint save and load.
- The WSL bittensor SDK read SN64 (256 neurons) from the Windows-native fork.

Fixes needed:
1. Chopsticks' plugin loader (`URL.pathname`, which gives `/C:/...`), patched to `fileURLToPath` by `setup`.
2. Chopsticks treats `C:\...` config paths as URLs and fetches them with axios. The engine now starts in its run directory with `--config=./chopsticks.yml`.
3. `os.kill(pid, 0)` *terminates* processes on Windows, so liveness now uses `OpenProcess`/`GetExitCodeProcess`; stopping uses `taskkill /T /F`.
4. bittensor is skipped on win32 by an environment marker, and SDK commands fail with a clear message.

Also found: Chopsticks silently moves to the next port when its port is taken. The engine's real port is now read from its "listening" line.

**Newer runtime:**
- `main` is at spec 487 (release-487 merged 2026-10-27), but every published node image predates that merge. The extracted runtimes were: `:devnet` 484, `:main` 486, `:latest` 486.
- `runtimes.runtime_version()` reads the wasm's `runtime_version` section without running it (zstd blobs included), which is how those versions were found.
- The engine **can't run `System.set_code`'s checks**: they load the new runtime, which imports BLS12-381 host functions. TensorFork now runs those checks itself (same spec name, higher spec version) and then dispatches `set_code_without_checks`. A same-version upgrade is now refused with "System.set_code would reject this".
- **Not tested:** an actual upgrade to 487. Building it needs `scripts/build-runtime.sh main` (the release-train srtool recipe), and the session's permission classifier blocked running external source builds. Once built, or once a v487 release exists: `tensorfork compare --runtime <487 wasm> -- python examples/subnet_economics.py 64`.

## Sources
- Subtensor releases: https://github.com/RaoFoundation/subtensor/releases
- Clone harness README: https://github.com/RaoFoundation/subtensor/tree/main/clones
- Mainnet clone docs: `docs/internals/mainnet-clone.mdx` in the same repo
- Nightly snapshot workflow: `.github/workflows/refresh-mainnet-snapshot.yml`
- Clone preview workflow: `.github/workflows/mainnet-clone-preview.yml`
- Local development guide: `docs/guides/local-development.mdx`
- Issues #2793, #3156: https://github.com/RaoFoundation/subtensor/issues
- Chopsticks: https://github.com/AcalaNetwork/chopsticks
- Learn Bittensor local build: https://docs.learnbittensor.org/local-build/deploy

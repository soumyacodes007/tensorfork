"""tensorfork command line.

  tensorfork setup                                            install Node + engine (once)
  tensorfork up [--snapshot FILE | --node URL [--block N]]   start a fork (background; --foreground to stay)
  tensorfork down | status
  tensorfork fund SS58 TAO
  tensorfork advance [--blocks N | --to N | --tempo NETUID]
  tensorfork checkpoint save|load|list [LABEL]
  tensorfork runtime set (vNNN | FILE) [--unchecked]
  tensorfork compare --runtime (vNNN | FILE) -- CMD...       diff a script's JSON across runtimes
  tensorfork replay BLOCK [--node URL]                        re-execute a mainnet block
  tensorfork verify NETUID --reference URL                    fork metagraph == reference?
  tensorfork snapshot export [--node URL] [--block N] [-o FILE]
  tensorfork snapshot info FILE
  tensorfork snapshot pack FILE | publish MANIFEST | pull [MANIFEST_URL]
  tensorfork serve FILE [--port 9955]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from tensorfork import engine, paths, snapshot_server
from tensorfork.fork import Fork, ForkError, replay
from tensorfork.snapshot import Snapshot, export_snapshot

DEFAULT_SNAPSHOT = paths.SNAPSHOTS / "finney-latest.sqlite"
DEFAULT_NODE = "ws://127.0.0.1:9946"


def _print(obj) -> None:
    print(json.dumps(obj, indent=1, default=str))


def _fork(a) -> Fork:
    return Fork.attach(a.name)


def _cmd_up(a) -> None:
    snapshot = None if a.node else str(a.snapshot or DEFAULT_SNAPSHOT)
    if snapshot and not Path(snapshot).exists():
        sys.exit(f"tensorfork: no snapshot at {snapshot}. Get one with `tensorfork snapshot pull` "
                 "(or export your own from a node with `tensorfork snapshot export`).")
    fork = Fork.start(snapshot=snapshot, node=a.node, block=a.block, name=a.name, port=a.port, host=a.host,
                      engine_port=a.engine_port, upstream_port=a.upstream_port)
    number, block_hash = fork.head()
    source = f"snapshot {snapshot}" if snapshot else f"node {a.node}"
    print(f"fork '{fork.name}' up at block {number} ({block_hash[:18]}…) from {source}", flush=True)
    print(f"connect: bt.Subtensor('{fork.url}')  |  btcli ... --network {fork.url}", flush=True)
    if a.foreground:
        sys.exit(engine.wait(a.name))


def _cmd_setup(a) -> None:
    from tensorfork import setup_engine

    setup_engine.setup(progress=lambda m: print(m, flush=True))


def _cmd_down(a) -> None:
    print("stopped" if engine.stop(a.name) else f"no fork '{a.name}' running")


def _cmd_status(a) -> None:
    state = engine.load_state(a.name)
    if state is None:
        print(f"no fork '{a.name}' running")
        return
    fork = Fork(state)
    number, block_hash = fork.head()
    cfg = state.config
    _print({"name": a.name, "url": fork.url, "head": number, "hash": block_hash,
            "upstream": cfg.get("snapshot") or cfg.get("node"), "pids": state.pids,
            "checkpoints": list(fork.checkpoints())})


def _cmd_fund(a) -> None:
    print(f"{a.ss58}: {_fork(a).fund(a.ss58, a.tao)}")


def _cmd_advance(a) -> None:
    _print(_fork(a).advance(a.blocks, to=a.to, tempo=a.tempo))


def _cmd_checkpoint(a) -> None:
    fork = _fork(a)
    if a.action == "list":
        _print(fork.checkpoints())
    elif not a.label:
        sys.exit("checkpoint save/load needs a LABEL")
    elif a.action == "save":
        _print(fork.checkpoint(a.label))
    else:
        _print(fork.restore(a.label))


def _cmd_runtime(a) -> None:
    _print(_fork(a).set_runtime(a.source, unchecked=a.unchecked))


def _cmd_compare(a) -> None:
    if not a.cmd:
        sys.exit("give the script after --, e.g. tensorfork compare --runtime v487 -- python check.py")
    cmd = a.cmd[1:] if a.cmd[0] == "--" else a.cmd
    out = _fork(a).compare(cmd, a.runtime, unchecked=a.unchecked)
    _print({"runtime": {k: out["runtime"][k] for k in ("spec_before", "spec_after")}, "diff": out["diff"]})


def _cmd_replay(a) -> None:
    _print(replay(a.block, a.node))


def _cmd_verify(a) -> None:
    out = _fork(a).verify(a.netuid, a.reference, block=a.block)
    _print(out)
    sys.exit(0 if out["match"] else 1)


def _cmd_export(a) -> None:
    out = a.output or paths.SNAPSHOTS / f"finney-{a.block or 'latest'}.sqlite"
    asyncio.run(export_snapshot(a.node, out, block=a.block, connections=a.connections,
                                progress=lambda m: print(m, flush=True)))


def _cmd_info(a) -> None:
    snap = Snapshot(a.file)
    m = snap.meta
    rv = m.get("runtime_version", {})
    when = dt.datetime.fromtimestamp(m.get("exported_at", 0), dt.UTC).isoformat()
    print(f"chain      {m.get('system_chain')}")
    print(f"block      {snap.block_number} {snap.block_hash}")
    print(f"runtime    {rv.get('specName')} v{rv.get('specVersion')}")
    print(f"keys       {len(snap):,}")
    print(f"size       {a.file.stat().st_size / 1e9:.2f} GB")
    print(f"exported   {when} from {m.get('source')}")


def _cmd_pack(a) -> None:
    from tensorfork import snapshot_dist

    m = snapshot_dist.pack(a.file, a.out, level=a.level)
    print(f"wrote {m} and {json.loads(m.read_text())['file']}")


def _cmd_publish(a) -> None:
    from tensorfork import snapshot_dist

    url = snapshot_dist.publish(a.manifest, a.repo or snapshot_dist.DEFAULT_REPO)
    print(f"published; pull with: tensorfork snapshot pull {url}")


def _cmd_pull(a) -> None:
    from tensorfork import snapshot_dist

    snapshot_dist.pull(a.source, progress=lambda m: print(m, flush=True))


def _cmd_serve(a) -> None:
    asyncio.run(snapshot_server.serve(a.file, a.host, a.port))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tensorfork", description="Fork Bittensor mainnet on your laptop.")
    p.add_argument("--name", default="default", help="which fork (several can run on different ports)")
    cmds = p.add_subparsers(dest="cmd", required=True)

    up = cmds.add_parser("up", help="start a fork in the background")
    up.add_argument("--snapshot", type=Path, help=f"offline fork from a snapshot (default {DEFAULT_SNAPSHOT})")
    up.add_argument("--node", help="lazy fork from a node RPC instead (needed for historical blocks)")
    up.add_argument("--block", type=int, help="fork block (with --node)")
    up.add_argument("--port", type=int, default=9944)
    up.add_argument("--host", default="127.0.0.1", help="client-facing bind address (0.0.0.0 in containers)")
    up.add_argument("--engine-port", type=int, default=8000)
    up.add_argument("--upstream-port", type=int, default=9955)
    up.add_argument("--foreground", action="store_true", help="stay attached; stop the fork on Ctrl-C/SIGTERM")
    up.set_defaults(fn=_cmd_up)

    cmds.add_parser("setup", help="install Node + the Chopsticks engine into ~/.tensorfork").set_defaults(fn=_cmd_setup)

    cmds.add_parser("down", help="stop the fork").set_defaults(fn=_cmd_down)
    cmds.add_parser("status", help="show the running fork").set_defaults(fn=_cmd_status)

    f = cmds.add_parser("fund", help="set an account's free TAO")
    f.add_argument("ss58")
    f.add_argument("tao", type=float)
    f.set_defaults(fn=_cmd_fund)

    adv = cmds.add_parser("advance", help="build blocks / jump to a subnet's next epoch")
    g = adv.add_mutually_exclusive_group()
    g.add_argument("--blocks", type=int)
    g.add_argument("--to", type=int)
    g.add_argument("--tempo", type=int, metavar="NETUID")
    adv.set_defaults(fn=_cmd_advance)

    cp = cmds.add_parser("checkpoint", help="instant save/restore of fork state")
    cp.add_argument("action", choices=["save", "load", "list"])
    cp.add_argument("label", nargs="?")
    cp.set_defaults(fn=_cmd_checkpoint)

    rt = cmds.add_parser("runtime", help="apply a runtime via the (impersonated) sudo key")
    rt.add_argument("action", choices=["set"])
    rt.add_argument("source", help="release tag (v487) or wasm file")
    rt.add_argument("--unchecked", action="store_true", help="set_code_without_checks (same/lower spec)")
    rt.set_defaults(fn=_cmd_runtime)

    cmp_ = cmds.add_parser("compare", help="run a script before/after a runtime upgrade and diff its JSON")
    cmp_.add_argument("--runtime", required=True)
    cmp_.add_argument("--unchecked", action="store_true")
    cmp_.add_argument("cmd", nargs=argparse.REMAINDER)
    cmp_.set_defaults(fn=_cmd_compare)

    rp = cmds.add_parser("replay", help="re-execute a mainnet block on a fork of its parent")
    rp.add_argument("block", type=int)
    rp.add_argument("--node", default=DEFAULT_NODE, help="node holding state for BLOCK-1")
    rp.set_defaults(fn=_cmd_replay)

    vf = cmds.add_parser("verify", help="check the fork's metagraph against a reference node")
    vf.add_argument("netuid", type=int)
    vf.add_argument("--reference", default="wss://archive.chain.opentensor.ai:443")
    vf.add_argument("--block", type=int)
    vf.set_defaults(fn=_cmd_verify)

    snap = cmds.add_parser("snapshot", help="export or inspect state snapshots").add_subparsers(dest="sub", required=True)
    ex = snap.add_parser("export", help="export full chain state at a block from a node")
    ex.add_argument("--node", default=DEFAULT_NODE, help="node RPC to export from (run your own: RPCs rate-limit)")
    ex.add_argument("--block", type=int, default=None, help="block number (default: latest finalized)")
    ex.add_argument("-o", "--output", type=Path, default=None)
    ex.add_argument("--connections", type=int, default=16)
    ex.set_defaults(fn=_cmd_export)
    info = snap.add_parser("info", help="describe a snapshot file")
    info.add_argument("file", type=Path)
    info.set_defaults(fn=_cmd_info)
    pk = snap.add_parser("pack", help="compress a snapshot and write its manifest for hosting")
    pk.add_argument("file", type=Path)
    pk.add_argument("--out", type=Path, default=None, help="output dir (default: <snapshot dir>/dist)")
    pk.add_argument("--level", type=int, default=10, help="zstd level")
    pk.set_defaults(fn=_cmd_pack)
    pb = snap.add_parser("publish", help="upload a packed snapshot to a GitHub release (public!)")
    pb.add_argument("manifest", type=Path)
    pb.add_argument("--repo", default=None, help="owner/repo (default: $TENSORFORK_SNAPSHOT_REPO)")
    pb.set_defaults(fn=_cmd_publish)
    pl = snap.add_parser("pull", help="download + verify a hosted snapshot, install as finney-latest")
    pl.add_argument("source", nargs="?", default=None, help="manifest URL or local manifest.json")
    pl.set_defaults(fn=_cmd_pull)

    sv = cmds.add_parser("serve", help="serve a snapshot as the engine's offline upstream")
    sv.add_argument("file", type=Path)
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=9955)
    sv.set_defaults(fn=_cmd_serve)
    return p


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    try:
        a.fn(a)
    except (ForkError, engine.StackError, FileNotFoundError, ValueError) as e:
        sys.exit(f"tensorfork: {e}")


if __name__ == "__main__":
    main()

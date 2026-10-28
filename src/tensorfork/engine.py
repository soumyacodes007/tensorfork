"""Start, stop and find a fork's process stack.

A fork is three local processes:

    upstream (snapshot server | shield over a node)  127.0.0.1:upstream_port
        -> Chopsticks engine (runs the real runtime)  127.0.0.1:engine_port
        -> compat proxy (what clients connect to)      host:port   (host 127.0.0.1 unless asked)

The upstream is either a snapshot file (fully offline) or a node RPC you run
(needed to replay historical blocks). State for a running fork lives in
~/.tensorfork/run/<name>/state.json so other commands can find it.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tensorfork import paths


@dataclass
class StackConfig:
    name: str = "default"
    snapshot: str | None = None  # offline: serve storage from this snapshot file
    node: str | None = None  # online: lazy-load from this node RPC (e.g. ws://127.0.0.1:9946)
    block: int | None = None  # fork block (node mode); a snapshot fixes its own block
    port: int = 9944
    host: str = "127.0.0.1"  # where the client-facing proxy listens (0.0.0.0 inside a container)
    engine_port: int = 8000
    upstream_port: int = 9955
    build_mode: str = "Instant"  # one block per submitted extrinsic; dev_newBlock still works
    resume: bool = False  # keep blocks built on a previous run of this fork


@dataclass
class RunState:
    config: dict
    pids: dict = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.config['port']}"

    @property
    def engine_url(self) -> str:
        return f"ws://127.0.0.1:{self.config['engine_port']}"


class StackError(RuntimeError):
    pass


def _state_file(name: str) -> Path:
    return paths.run_dir(name) / "state.json"


def load_state(name: str = "default") -> RunState | None:
    f = _state_file(name)
    if not f.exists():
        return None
    state = RunState(**json.loads(f.read_text()))
    if not all(_alive(pid) for pid in state.pids.values()):
        return None
    return state


# ------------------------------------------------------------------ processes


def _alive(pid: int) -> bool:
    if paths.WINDOWS:
        # os.kill(pid, 0) would terminate the process on Windows; ask the kernel instead.
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        # Reap it if it's our exited child (e.g. foreground mode as PID 1): a zombie still answers kill(0).
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False
    except ChildProcessError:
        pass  # not our child (started by another tensorfork process)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _spawn(cmd: list[str], log: Path, cwd: Path | None = None) -> subprocess.Popen:
    kwargs: dict = {"cwd": cwd}
    if paths.WINDOWS:
        # No console, own group: the fork keeps running after the CLI exits.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True  # survive the CLI exiting; own process group
    return subprocess.Popen(cmd, stdout=log.open("w"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            env=_env(), **kwargs)


def _kill(pid: int, grace: float = 5) -> None:
    if paths.WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.time() + grace
    while time.time() < deadline and _alive(pid):
        time.sleep(0.1)
    if _alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass


def _env() -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{paths.NODE_BIN}{os.pathsep}{env.get('PATH', '')}"
    env["TENSORFORK_ENGINE_DIR"] = str(paths.ENGINE_DIR)
    return env


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _port_free(port: int) -> bool:
    """Can we bind it? (Stricter than "nothing answers": catches listeners on other addresses.)"""
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _wait_engine(proc: subprocess.Popen, log: Path, timeout: float) -> int:
    """Wait for Chopsticks' "listening on ...:PORT" line and return the port it really bound.

    Chopsticks silently moves to the next port when its port is taken, so the
    configured port can't be trusted.
    """
    import re

    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise StackError(f"engine exited ({proc.returncode}); last log lines:\n{_tail(log)}")
        try:
            m = re.search(r"listening on http://[^ ]+:(\d+)", log.read_text(errors="replace"))
        except OSError:
            m = None
        if m:
            return int(m.group(1))
        time.sleep(0.25)
    raise StackError(f"engine did not start within {timeout:.0f}s; log:\n{_tail(log)}")


def _wait_port(port: int, proc: subprocess.Popen, log: Path, what: str, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise StackError(f"{what} exited ({proc.returncode}); last log lines:\n{_tail(log)}")
        if _port_open(port):
            return
        time.sleep(0.25)
    raise StackError(f"{what} did not open port {port} within {timeout:.0f}s; log:\n{_tail(log)}")


def _tail(log: Path, n: int = 15) -> str:
    try:
        return "\n".join(log.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return "(no log)"


# ------------------------------------------------------------------ lifecycle


def _chopsticks_config(cfg: StackConfig, run: Path) -> Path:
    lines = [
        f"endpoint: ws://127.0.0.1:{cfg.upstream_port}",
        f"port: {cfg.engine_port}",
        "host: 127.0.0.1",
        "mock-signature-host: true",
        # Subtensor imports BLS12-381 host functions Chopsticks lacks; only calls that reach them fail.
        "allow-unresolved-imports: true",
        f"build-block-mode: {cfg.build_mode}",
    ]
    if cfg.block is not None and cfg.node:
        lines.append(f"block: {cfg.block}")
    f = run / "chopsticks.yml"
    f.write_text("\n".join(lines) + "\n")
    return f


def start(cfg: StackConfig, timeout: float = 120) -> RunState:
    if (cfg.snapshot is None) == (cfg.node is None):
        raise StackError("give exactly one upstream: a snapshot file or a node RPC")
    if not (paths.NODE.exists() and paths.CHOPSTICKS_JS.exists()):
        raise StackError("engine not installed; run `tensorfork setup`")
    if load_state(cfg.name):
        raise StackError(f"fork '{cfg.name}' is already running; `tensorfork down` first")
    for port in (cfg.port, cfg.engine_port, cfg.upstream_port):
        if not _port_free(port):
            raise StackError(f"port {port} is in use (pick others with --port/--engine-port/--upstream-port)")

    run = paths.run_dir(cfg.name)
    pids: dict[str, int] = {}
    procs: list[subprocess.Popen] = []
    try:
        log = run / "upstream.log"
        if cfg.snapshot:
            cmd = [sys.executable, "-m", "tensorfork.snapshot_server", str(cfg.snapshot), "--port", str(cfg.upstream_port)]
        else:
            cmd = [str(paths.NODE), str(paths.JS / "upstream.mjs"),
                   "--listen", str(cfg.upstream_port), "--upstream", cfg.node]
        procs.append(p := _spawn(cmd, log))
        pids["upstream"] = p.pid
        _wait_port(cfg.upstream_port, p, log, "upstream", timeout)

        engine_db = run / "engine.sqlite"
        if not cfg.resume:
            engine_db.unlink(missing_ok=True)
            # Checkpoints point at blocks in the old engine DB; a fresh fork can't restore them.
            (run / "checkpoints.json").unlink(missing_ok=True)
        log = run / "engine.log"
        _chopsticks_config(cfg, run)
        # Relative config path, engine started in the run dir: Chopsticks treats "C:\..." as a URL
        # (and a bare "name.yml" as a named config to download), so "./chopsticks.yml" is the safe form.
        cmd = [str(paths.NODE), str(paths.CHOPSTICKS_JS), "--config=./chopsticks.yml", f"--db={engine_db}"]
        if cfg.resume:
            cmd.append("--resume=true")
        procs.append(p := _spawn(cmd, log, cwd=run))
        pids["engine"] = p.pid
        cfg.engine_port = _wait_engine(p, log, timeout)

        log = run / "proxy.log"
        cmd = [str(paths.NODE), str(paths.JS / "proxy.mjs"), "--host", cfg.host,
               "--listen", str(cfg.port), "--upstream", f"ws://127.0.0.1:{cfg.engine_port}"]
        procs.append(p := _spawn(cmd, log))
        pids["proxy"] = p.pid
        _wait_port(cfg.port, p, log, "proxy", timeout)
    except BaseException:
        for p in procs:
            _kill(p.pid)
        raise

    state = RunState(config=asdict(cfg), pids=pids)
    _state_file(cfg.name).write_text(json.dumps(asdict(state), indent=1))
    return state


def stop(name: str = "default") -> bool:
    f = _state_file(name)
    if not f.exists():
        return False
    state = RunState(**json.loads(f.read_text()))
    for pid in reversed(list(state.pids.values())):
        _kill(pid)
    f.unlink(missing_ok=True)
    return True


def wait(name: str = "default", poll: float = 2.0) -> int:
    """Block while the fork runs (foreground mode, e.g. in a container); stop it on SIGINT/SIGTERM.

    Returns 0 on a requested stop, 1 if one of the fork's processes died.
    """
    stopping = False

    def _stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    try:
        while not stopping:
            state = load_state(name)
            if state is None:
                print("a fork process exited; stopping", flush=True)
                return 1
            time.sleep(poll)
        print("signal received; stopping fork", flush=True)
        return 0
    finally:
        t0 = time.time()
        stop(name)
        print(f"fork stopped in {time.time() - t0:.1f}s", flush=True)

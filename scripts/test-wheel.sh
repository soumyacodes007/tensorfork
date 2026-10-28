#!/usr/bin/env bash
# Clean-room check of the pip package: fresh venv + empty TENSORFORK_HOME, then
# setup -> up (from an existing snapshot) -> one SDK read -> down.
# usage: test-wheel.sh dist/tensorfork-*.whl SNAPSHOT.sqlite
set -euo pipefail
WHEEL="$(realpath "${1:?wheel}")"
SNAPSHOT="$(realpath "${2:?snapshot}")"
WORK="$(mktemp -d)"
trap 'TENSORFORK_HOME="$WORK/home" "$WORK/venv/bin/tensorfork" down >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT

uv venv -q --python 3.12 "$WORK/venv"
VIRTUAL_ENV="$WORK/venv" uv pip install -q "$WHEEL"
export TENSORFORK_HOME="$WORK/home"
tf="$WORK/venv/bin/tensorfork"

$tf setup
grep -q 'fileURLToPath(location)' "$TENSORFORK_HOME/engine/node_modules/@acala-network/chopsticks/dist/cjs/plugins/index.js" \
  && echo "engine patch: applied"
$tf up --snapshot "$SNAPSHOT"
"$WORK/venv/bin/python" - <<'PY'
import bittensor as bt
sub = bt.Subtensor("ws://127.0.0.1:9944")
print("wheel install reads chain: block", sub.block, "spec", sub.spec_version)
PY
$tf down
echo "wheel test OK"

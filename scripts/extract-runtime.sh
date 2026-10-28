#!/usr/bin/env bash
# Extract the runtime wasm a published Subtensor node image embeds (e.g. an unreleased candidate).
#
# Every node binary carries its runtime; `build-spec --raw` writes it into the
# genesis `:code`. This lets you test an upgrade to a candidate runtime (from the
# :main / :devnet / :testnet image) before it has a GitHub release.
#
# usage: extract-runtime.sh IMAGE_TAG OUT.wasm      e.g. extract-runtime.sh devnet ~/.tensorfork/runtimes/devnet.wasm
set -euo pipefail
TAG="${1:?usage: extract-runtime.sh IMAGE_TAG OUT.wasm}"
OUT="${2:?usage: extract-runtime.sh IMAGE_TAG OUT.wasm}"
IMAGE="ghcr.io/raofoundation/subtensor:${TAG}"
PY="${TENSORFORK_HOME:-$HOME/.tensorfork}/venv/bin/python"

docker pull -q "$IMAGE" >/dev/null
mkdir -p "$(dirname "$OUT")"
docker run --rm "$IMAGE" build-spec --chain local --raw --disable-default-bootnode 2>/dev/null \
  | "$PY" -c '
import json, sys
text = sys.stdin.read()  # the image entrypoint prints status lines before the JSON
spec, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
code = spec["genesis"]["raw"]["top"]["0x3a636f6465"]
open(sys.argv[1], "wb").write(bytes.fromhex(code[2:]))
print(f"{sys.argv[1]}: {len(code) // 2 - 1} bytes")
' "$OUT"

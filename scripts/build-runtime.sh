#!/usr/bin/env bash
# Build a Subtensor runtime wasm from source exactly like the release train does
# (pinned srtool image, metadata-hash feature, production profile), so you can
# test an upgrade to a runtime that has no GitHub release yet.
#
# usage: build-runtime.sh [GIT_REF] [OUT.wasm]     default: main -> ~/.tensorfork/runtimes/<ref>.wasm
# Needs Docker; first run builds the srtool image (~10 min) and the runtime (~30-60 min).
set -euo pipefail
REF="${1:-main}"
OUT="${2:-$HOME/.tensorfork/runtimes/${REF//\//-}.wasm}"
SRC="${TENSORFORK_HOME:-$HOME/.tensorfork}/src/subtensor"

if [ ! -d "$SRC/.git" ]; then
  git clone --filter=blob:none https://github.com/RaoFoundation/subtensor "$SRC"
fi
git -C "$SRC" fetch --quiet origin "$REF"
git -C "$SRC" checkout --quiet --detach FETCH_HEAD
echo "building runtime at $(git -C "$SRC" rev-parse --short HEAD): $(grep -m1 -E 'spec_version: [0-9]+' "$SRC/runtime/src/lib.rs" | tr -d ' ,')"

cd "$SRC"
docker image inspect srtool >/dev/null 2>&1 || ./scripts/srtool/build-srtool-image.sh
cd runtime && { [ -L node-subtensor ] || ln -s . node-subtensor; } && cd ..
mkdir -p "$HOME/.cargo-srtool"
docker run --rm --user root --platform=linux/amd64 \
  -e PACKAGE=node-subtensor-runtime \
  -e BUILD_OPTS="--features=metadata-hash" \
  -e PROFILE=production \
  -e CONST_RANDOM_SEED=subtensor-runtime \
  -v "$HOME/.cargo-srtool":/cargo-home \
  -v "$(pwd)":/build \
  srtool bash -c "git config --global --add safe.directory /build && \
    /srtool/build --app > /build/runtime/node-subtensor/srtool-output.log; \
    code=\$?; [ \$code -ne 0 ] && tail -40 /build/runtime/node-subtensor/srtool-output.log && exit \$code; \
    tail -n 1 /build/runtime/node-subtensor/srtool-output.log > /build/runtime/node-subtensor/subtensor-digest.json"

wasm=$(find runtime -name 'node_subtensor_runtime.compact.compressed.wasm' -path '*srtool*' | head -n 1)
mkdir -p "$(dirname "$OUT")"
cp "$wasm" "$OUT"
cp runtime/subtensor-digest.json "${OUT%.wasm}.digest.json"
echo "runtime -> $OUT ($(stat -c %s "$OUT") bytes)"

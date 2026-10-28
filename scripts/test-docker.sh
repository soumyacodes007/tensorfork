#!/usr/bin/env bash
# Run the image against a local snapshot dir, read through the published port, stop cleanly.
# usage: test-docker.sh IMAGE SNAPSHOT_DIR
set -euo pipefail
IMAGE="${1:-tensorfork:dev}"
SNAPDIR="$(realpath "${2:-$HOME/.tensorfork/snapshots}")"
docker rm -f tf-test >/dev/null 2>&1 || true
docker run -d --name tf-test -p 127.0.0.1:9944:9944 -v "$SNAPDIR":/data/snapshots "$IMAGE" >/dev/null
for _ in $(seq 1 120); do
  docker logs tf-test 2>&1 | grep -q "connect:" && break
  docker ps -q -f name=tf-test | grep -q . || { docker logs tf-test; exit 1; }
  sleep 1
done
docker logs tf-test 2>&1 | head -2
"${TENSORFORK_HOME:-$HOME/.tensorfork}/venv/bin/python" - <<'PY'
import bittensor as bt
sub = bt.Subtensor("ws://127.0.0.1:9944")
print("host reads container fork: block", sub.block, "spec", sub.spec_version)
PY
start=$(date +%s)
docker stop tf-test >/dev/null
echo "docker stop took $(( $(date +%s) - start ))s, exit code $(docker inspect -f '{{.State.ExitCode}}' tf-test)"
docker rm tf-test >/dev/null

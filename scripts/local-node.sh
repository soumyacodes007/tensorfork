#!/usr/bin/env bash
# Run a local mainnet (finney) lite node as the fork's upstream.
#
# Public RPCs rate-limit lazy forking (HTTP 429), so TensorFork reads from a node
# you run: it warp-syncs over P2P and then serves state locally with no limits.
# Flags follow the repo's docker-compose `mainnet-lite` service, with:
# - RPC published on 127.0.0.1 only (never exposed), P2P outbound only
# - --state-pruning widened (default keeps ~300 blocks) so a pinned fork block
#   stays readable for hours
# - --history-backfill=skip: after warp sync the node otherwise backfills block
#   history to genesis (default `keep`), even with --blocks-pruning; that is
#   where the docs' ~128 GB goes. A fork needs recent state, not history
#   (measured: state at the head is ~1.6 GB). The chain team's own
#   build-patched-spec clone uses `skip` by default.
# - --blocks-pruning: keep only recent block bodies
# - smaller caches than compose (it assumes a 40 GB box)
#
# usage: local-node.sh start|stop|reset|logs|status
set -euo pipefail

NAME="${TENSORFORK_NODE_NAME:-tensorfork-node}"
IMAGE="${TENSORFORK_NODE_IMAGE:-ghcr.io/raofoundation/subtensor:latest}"
VOLUME="${TENSORFORK_NODE_VOLUME:-tensorfork-node-data}"
RPC_PORT="${TENSORFORK_NODE_RPC_PORT:-9946}"
STATE_PRUNING="${TENSORFORK_STATE_PRUNING:-4000}"   # ~13 h of 12 s blocks

case "${1:-start}" in
  start)
    if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
      echo "$NAME already running"; exit 0
    fi
    docker rm "$NAME" >/dev/null 2>&1 || true
    docker pull "$IMAGE"
    docker run -d --name "$NAME" --restart unless-stopped \
      -p "127.0.0.1:${RPC_PORT}:9944" \
      -v "${VOLUME}:/data" \
      "$IMAGE" \
      --base-path=/data \
      --chain=./chainspecs/raw_spec_finney.json \
      --rpc-external --rpc-cors=all \
      --rpc-max-response-size=64 \
      --no-mdns \
      --bootnodes=/dns/bootnode.finney.chain.opentensor.ai/tcp/30333/ws/p2p/12D3KooWRwbMb85RWnT8DSXSYMWQtuDwh4LJzndoRrTDotTR5gDC \
      --sync=warp \
      --history-backfill=skip \
      --state-pruning="$STATE_PRUNING" \
      --blocks-pruning="$STATE_PRUNING" \
      --database paritydb \
      --db-cache 1024 \
      --trie-cache-size 1024
    echo "started $NAME; RPC ws://127.0.0.1:${RPC_PORT} once synced"
    ;;
  stop) docker stop "$NAME" ;;
  reset)
    # Drop the node and its chain data (re-downloadable via warp sync).
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker volume rm "$VOLUME"
    ;;
  logs) docker logs --tail "${2:-50}" "$NAME" ;;
  status) docker ps --filter "name=^${NAME}$" --format '{{.Names}} {{.Status}}' ;;
  *) echo "usage: $0 start|stop|reset|logs|status" >&2; exit 2 ;;
esac

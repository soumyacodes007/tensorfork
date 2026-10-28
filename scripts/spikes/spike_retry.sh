#!/usr/bin/env bash
# Re-run the read spike until it succeeds; every attempt warms the engine's SQLite cache.
PY=/home/soumy/.tensorfork/venv/bin/python
SPIKE=/mnt/c/Users/soumy/Desktop/jev-halo-prism/tensorfork/scripts/spikes/spike_read.py
DB=$HOME/.tensorfork/cache/finney.sqlite
for attempt in 1 2 3 4 5 6; do
  start=$(date +%s)
  if out=$("$PY" "$SPIKE" ws://127.0.0.1:9944 "${1:-64}" 2>&1); then
    echo "attempt $attempt: OK in $(( $(date +%s) - start ))s"
    echo "$out"
    exit 0
  fi
  echo "attempt $attempt: failed after $(( $(date +%s) - start ))s: $(echo "$out" | tail -1)"
  echo "  cache db: $(du -h "$DB" 2>/dev/null | cut -f1)"
done
exit 1

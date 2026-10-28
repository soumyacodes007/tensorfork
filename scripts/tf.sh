#!/usr/bin/env bash
# Run the tensorfork CLI from the WSL venv (handy from Windows: wsl.exe -e bash scripts/tf.sh <args>).
exec "${TENSORFORK_HOME:-$HOME/.tensorfork}/venv/bin/tensorfork" "$@"

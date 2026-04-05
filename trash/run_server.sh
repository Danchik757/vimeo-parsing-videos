#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "run_server.sh is intended for Linux servers."
  echo "For local smoke tests use ./run.sh or python run_workers.py."
  exit 1
fi

if ! command -v xvfb-run >/dev/null 2>&1; then
  echo "xvfb-run not found. Install Xvfb first."
  exit 1
fi

source venv/bin/activate
xvfb-run -a -s "-screen 0 1920x1080x24" python run_batches.py "$@"

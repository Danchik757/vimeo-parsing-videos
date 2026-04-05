#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
source venv/bin/activate
python download_vimeo_seleniumbase_v3.py "$@"

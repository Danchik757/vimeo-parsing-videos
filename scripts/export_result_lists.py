#!/usr/bin/env python3
"""Export URL lists from an aggregate or per-worker results manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from result_exports import write_result_url_lists


def parse_args():
    parser = argparse.ArgumentParser(description="Export URL lists from results manifest")
    parser.add_argument("manifest", help="Path to results manifest JSON")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for exported URL lists (default: manifest parent)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    manifest_path = Path(args.manifest).resolve()
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    output_dir = Path(args.output_dir).resolve() if args.output_dir else manifest_path.parent
    written = write_result_url_lists(manifest.get("items") or [], output_dir)
    for bucket, path in written.items():
        print(f"{bucket}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

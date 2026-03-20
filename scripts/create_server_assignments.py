#!/usr/bin/env python3
"""Split the unique Vimeo list into fixed-size shard JSONs assigned to server folders."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_ASSIGNMENTS = {
    "parse-1": 24,
    "parse-2": 18,
    "parse-3": 28,
}


def parse_args():
    parser = argparse.ArgumentParser(description="Create per-server shard assignments")
    parser.add_argument(
        "--source",
        default="need_parse_unique.json",
        help="Source JSON list of unique Vimeo URLs",
    )
    parser.add_argument(
        "--output-root",
        default="data_shards/server_assignments_10000",
        help="Output directory root",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10000,
        help="URLs per shard",
    )
    return parser.parse_args()


def chunk_urls(urls, batch_size):
    return [urls[i : i + batch_size] for i in range(0, len(urls), batch_size)]


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def main():
    args = parse_args()
    source_path = Path(args.source).resolve()
    output_root = Path(args.output_root).resolve()

    with open(source_path, "r", encoding="utf-8") as f:
        urls = json.load(f)

    shards = chunk_urls(urls, int(args.batch_size))
    expected_total_shards = sum(DEFAULT_ASSIGNMENTS.values())
    if len(shards) != expected_total_shards:
        raise SystemExit(
            f"Expected {expected_total_shards} shards from assignment layout, got {len(shards)}"
        )

    assignment_manifest = {
        "source_json": str(source_path.name),
        "total_urls": len(urls),
        "batch_size": int(args.batch_size),
        "total_shards": len(shards),
        "servers": {},
    }

    shard_index = 0
    for server_name, shard_count in DEFAULT_ASSIGNMENTS.items():
        server_dir = output_root / server_name
        server_entries = []
        for _ in range(shard_count):
            shard_index += 1
            shard_urls = shards[shard_index - 1]
            shard_path = server_dir / f"batch_{shard_index:04d}.json"
            write_json(shard_path, shard_urls)
            server_entries.append(
                {
                    "batch_number": shard_index,
                    "filename": shard_path.name,
                    "path": shard_path.name,
                    "url_count": len(shard_urls),
                }
            )

        server_manifest = {
            "server_name": server_name,
            "source_json": str(Path("..") / ".." / ".." / source_path.name),
            "batch_size": int(args.batch_size),
            "shard_count": len(server_entries),
            "total_urls": sum(item["url_count"] for item in server_entries),
            "batches": server_entries,
        }
        write_json(server_dir / "manifest.json", server_manifest)
        assignment_manifest["servers"][server_name] = {
            "shard_count": len(server_entries),
            "total_urls": server_manifest["total_urls"],
            "manifest": str(Path(server_name) / "manifest.json"),
        }

    write_json(output_root / "assignment_manifest.json", assignment_manifest)
    readme_path = output_root / "README.txt"
    readme_path.write_text(
        "\n".join(
            [
                "# Server Assignments",
                "",
                f"Source: `{source_path}`",
                f"Batch size: `{args.batch_size}`",
                "",
                "Distribution:",
                "",
                "- `parse-1`: 24 shard JSON files",
                "- `parse-2`: 18 shard JSON files",
                "- `parse-3`: 28 shard JSON files",
                "",
                "See `assignment_manifest.json` and each server `manifest.json` for exact file lists.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

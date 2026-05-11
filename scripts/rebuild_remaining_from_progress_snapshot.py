#!/usr/bin/env python3
"""Rebuild a remaining URL list and fresh batches from count-based batch progress."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Rebuild remaining URLs from a batch progress snapshot"
    )
    parser.add_argument(
        "--assignment-root",
        default="data_shards/server_assignments_10000",
        help="Root directory with existing shard assignments",
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        help="Path to snapshot JSON with completed_batches and partial_batches",
    )
    parser.add_argument(
        "--output-root",
        required=True,
        help="Directory where the rebuilt remaining list and fresh batches will be written",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10000,
        help="URLs per new batch",
    )
    parser.add_argument(
        "--server-name",
        default="remaining-recovery",
        help="server_name value for the generated manifest",
    )
    return parser.parse_args()


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def resolve_manifest_batch_path(manifest_path, batch_item):
    manifest_path = Path(manifest_path).resolve()
    filename_value = batch_item.get("filename")
    if filename_value:
        sibling_candidate = (manifest_path.parent / filename_value).resolve()
        if sibling_candidate.exists():
            return sibling_candidate

    source_value = batch_item.get("path") or batch_item.get("source_json") or filename_value
    if not source_value:
        raise ValueError(f"Batch entry is missing path/source_json in {manifest_path}")

    candidate = Path(source_value)
    if candidate.is_absolute():
        if candidate.exists():
            return candidate
        fallback_candidate = (manifest_path.parent / candidate.name).resolve()
        if fallback_candidate.exists():
            return fallback_candidate
        return candidate

    resolved_candidate = (manifest_path.parent / candidate).resolve()
    if resolved_candidate.exists():
        return resolved_candidate
    return (manifest_path.parent / candidate.name).resolve()


def load_assignment_batches(assignment_root):
    assignment_root = Path(assignment_root).resolve()
    manifests = sorted(assignment_root.glob("*/manifest.json"))
    if not manifests:
        raise ValueError(f"No server manifests found in {assignment_root}")

    batches = []
    for manifest_path in manifests:
        payload = read_json(manifest_path)
        server_name = payload.get("server_name") or manifest_path.parent.name
        for item in payload.get("batches", []):
            batch_number = int(item["batch_number"])
            batch_path = resolve_manifest_batch_path(manifest_path, item)
            batches.append(
                {
                    "batch_number": batch_number,
                    "server_name": server_name,
                    "batch_path": batch_path,
                }
            )

    batches.sort(key=lambda item: item["batch_number"])
    return batches


def chunk_urls(urls, batch_size):
    return [urls[index : index + batch_size] for index in range(0, len(urls), batch_size)]


def main():
    args = parse_args()
    assignment_batches = load_assignment_batches(args.assignment_root)
    snapshot_path = Path(args.snapshot).resolve()
    snapshot = read_json(snapshot_path)
    output_root = Path(args.output_root).resolve()

    completed_batches = {int(value) for value in snapshot.get("completed_batches", [])}
    partial_batches = {
        int(batch_number): int(processed_count)
        for batch_number, processed_count in (snapshot.get("partial_batches", {}) or {}).items()
    }

    known_batches = {item["batch_number"] for item in assignment_batches}
    unknown_completed = sorted(completed_batches - known_batches)
    unknown_partial = sorted(set(partial_batches) - known_batches)
    if unknown_completed or unknown_partial:
        raise SystemExit(
            f"Snapshot references unknown batches: completed={unknown_completed} partial={unknown_partial}"
        )

    remaining_urls = []
    batch_report = []
    removed_total = 0

    for batch_entry in assignment_batches:
        batch_number = batch_entry["batch_number"]
        urls = read_json(batch_entry["batch_path"])
        total_urls = len(urls)
        action = "keep_all"
        removed_count = 0

        if batch_number in completed_batches:
            action = "drop_all"
            removed_count = total_urls
            kept_urls = []
        elif batch_number in partial_batches:
            action = "drop_prefix"
            removed_count = max(0, min(total_urls, partial_batches[batch_number]))
            kept_urls = urls[removed_count:]
        else:
            kept_urls = urls

        remaining_urls.extend(kept_urls)
        removed_total += removed_count
        batch_report.append(
            {
                "batch_number": batch_number,
                "server_name": batch_entry["server_name"],
                "batch_path": str(batch_entry["batch_path"]),
                "original_urls": total_urls,
                "removed_urls": removed_count,
                "kept_urls": len(kept_urls),
                "action": action,
            }
        )

    output_root.mkdir(parents=True, exist_ok=True)
    remaining_source_name = (
        snapshot.get("remaining_source_name")
        or f"{snapshot_path.stem}.remaining_urls.json"
    )
    remaining_source_path = output_root / remaining_source_name
    write_json(remaining_source_path, remaining_urls)

    new_batches = chunk_urls(remaining_urls, int(args.batch_size))
    manifest_batches = []
    for index, batch_urls in enumerate(new_batches, start=1):
        batch_name = f"batch_{index:04d}.json"
        batch_path = output_root / batch_name
        write_json(batch_path, batch_urls)
        manifest_batches.append(
            {
                "batch_number": index,
                "filename": batch_name,
                "path": batch_name,
                "url_count": len(batch_urls),
            }
        )

    manifest_payload = {
        "server_name": args.server_name,
        "source_json": remaining_source_path.name,
        "batch_size": int(args.batch_size),
        "shard_count": len(manifest_batches),
        "total_urls": len(remaining_urls),
        "batches": manifest_batches,
    }
    write_json(output_root / "manifest.json", manifest_payload)

    report_payload = {
        "snapshot_path": str(snapshot_path),
        "assignment_root": str(Path(args.assignment_root).resolve()),
        "server_name": args.server_name,
        "batch_size": int(args.batch_size),
        "original_batch_count": len(assignment_batches),
        "original_total_urls": removed_total + len(remaining_urls),
        "removed_total_urls": removed_total,
        "remaining_total_urls": len(remaining_urls),
        "completed_batches": sorted(completed_batches),
        "partial_batches": {str(key): partial_batches[key] for key in sorted(partial_batches)},
        "assumption": snapshot.get("assumption"),
        "notes": snapshot.get("notes", []),
        "batch_report": batch_report,
        "generated_files": {
            "remaining_source_json": str(remaining_source_path),
            "manifest_json": str(output_root / "manifest.json"),
        },
    }
    write_json(output_root / "rebuild_report.json", report_payload)

    readme_lines = [
        "# Remaining URL Recovery Output",
        "",
        f"Snapshot: {snapshot_path.name}",
        f"Assignment root: {Path(args.assignment_root).resolve()}",
        f"Original total URLs: {report_payload['original_total_urls']}",
        f"Removed URLs: {removed_total}",
        f"Remaining URLs: {len(remaining_urls)}",
        f"Generated batches: {len(manifest_batches)}",
        "",
        "Important assumption:",
        str(snapshot.get("assumption") or "count-based prefix removal"),
        "",
        "Generated files:",
        f"- {remaining_source_path.name}",
        "- manifest.json",
        "- rebuild_report.json",
        "",
    ]
    (output_root / "README.txt").write_text("\n".join(readme_lines), encoding="utf-8")

    print(output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

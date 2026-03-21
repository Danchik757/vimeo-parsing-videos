#!/usr/bin/env python3
"""Summarize failed/not-downloaded Vimeo worker results."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract failed/not-downloaded URLs and reasons from aggregate_results_manifest.json"
    )
    parser.add_argument("manifest", type=Path, help="Path to aggregate_results_manifest.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output JSON path (defaults next to manifest)",
    )
    return parser.parse_args()


def pick_reason(item: dict) -> str:
    for key in ("error", "probe_error", "policy_reason", "reason"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def pick_video_id(item: dict) -> str | None:
    for key in ("video_id", "canonical_video_id"):
        value = item.get(key)
        if value:
            return str(value)
    url = item.get("url")
    if isinstance(url, str) and "/" in url:
        return url.rstrip("/").rsplit("/", 1)[-1]
    return None


def normalize_status(item: dict) -> str:
    status = item.get("status")
    if isinstance(status, str) and status.strip():
        return status.strip()
    if item.get("success") is True:
        return "downloaded"
    if item.get("skipped_by_policy"):
        return "skipped"
    if item.get("error") or item.get("probe_error"):
        return "failed"
    return "unknown"


def build_report(payload: dict) -> dict:
    items = payload.get("items") or []

    failed_like = []
    reason_counter = Counter()
    status_counter = Counter()
    worker_counter = Counter()

    for item in items:
        if not isinstance(item, dict):
            continue

        status = normalize_status(item)
        status_counter[status] += 1

        if status == "downloaded":
            continue

        worker_name = item.get("worker_name") or "unknown-worker"
        worker_counter[worker_name] += 1
        reason = pick_reason(item)
        reason_counter[reason] += 1

        failed_like.append(
            {
                "worker_name": worker_name,
                "url": item.get("url"),
                "video_id": pick_video_id(item),
                "status": status,
                "reason": reason,
                "download_source": item.get("download_source"),
                "selected_quality": item.get("selected_quality"),
                "is_original": item.get("is_original"),
                "button_found": item.get("button_found"),
                "download_link_found": item.get("download_link_found"),
                "available_options_count": item.get("available_options_count"),
                "page_url": item.get("page_url"),
            }
        )

    failed_like.sort(
        key=lambda row: (
            row.get("status") or "",
            row.get("worker_name") or "",
            row.get("video_id") or "",
        )
    )

    return {
        "manifest": str(payload.get("manifest_path") or ""),
        "worker_count": payload.get("worker_count"),
        "total_urls": payload.get("total_urls"),
        "status_counts": dict(status_counter),
        "top_reasons": dict(reason_counter.most_common()),
        "not_downloaded_by_worker": dict(worker_counter),
        "items": failed_like,
    }


def main() -> int:
    args = parse_args()
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = build_report(payload)

    output_path = args.output
    if output_path is None:
        output_path = args.manifest.with_name("aggregate_failure_report.json")

    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"saved_report = {output_path}")
    print(f"total_not_downloaded = {len(report['items'])}")
    print("status_counts =", json.dumps(report["status_counts"], ensure_ascii=False))
    print("top_reasons =", json.dumps(report["top_reasons"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

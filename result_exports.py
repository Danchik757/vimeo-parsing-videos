"""Helpers for exporting URL lists from result manifests."""

from __future__ import annotations

from pathlib import Path


RESULT_LIST_FILENAMES = {
    "downloaded_original": "downloaded_original_urls.txt",
    "not_downloaded_downloadable": "not_downloaded_downloadable_urls.txt",
    "no_links": "no_links_urls.txt",
}


def build_result_url_lists(items):
    buckets = {
        "downloaded_original": [],
        "not_downloaded_downloadable": [],
        "no_links": [],
    }

    seen = {key: set() for key in buckets}

    for item in items or []:
        url = item.get("url")
        if not url:
            continue

        bucket = None
        status = item.get("status")
        if status == "downloaded":
            bucket = "downloaded_original"
        elif item.get("downloadable") or item.get("download_link"):
            bucket = "not_downloaded_downloadable"
        else:
            bucket = "no_links"

        if url in seen[bucket]:
            continue
        seen[bucket].add(url)
        buckets[bucket].append(url)

    return buckets


def write_result_url_lists(items, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    buckets = build_result_url_lists(items)
    written = {}
    for bucket, filename in RESULT_LIST_FILENAMES.items():
        path = output_dir / filename
        lines = buckets[bucket]
        text = "\n".join(lines)
        if text:
            text += "\n"
        path.write_text(text, encoding="utf-8")
        written[bucket] = str(path)
    return written

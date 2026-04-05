"""Launch multiple logged-in original-visibility inspector workers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from download_vimeo_seleniumbase_v3 import PROJECT_ROOT as DOWNLOADER_PROJECT_ROOT, load_config, setup_logger


def parse_args():
    parser = argparse.ArgumentParser(
        description="Launch multiple logged-in original-visibility inspector workers"
    )
    parser.add_argument(
        "--config",
        default=str(DOWNLOADER_PROJECT_ROOT / "config.json"),
        help="Path to master config JSON",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of workers to start",
    )
    return parser.parse_args()


def shard_urls(urls, worker_count):
    shards = [[] for _ in range(worker_count)]
    for index, url in enumerate(urls):
        shards[index % worker_count].append(url)
    return shards


def build_worker_config(master_config, worker_dir, shard_path, worker_index, worker_count):
    worker_name = f"worker-{worker_index:02d}"
    worker_config = json.loads(json.dumps(master_config))

    worker_config["files"]["source_json"] = str(shard_path)
    worker_config["files"]["logs_dir"] = str(worker_dir)
    worker_config["files"]["log_file"] = str(worker_dir / "download.log")
    worker_config["files"]["summary_file"] = str(worker_dir / "summary.json")
    worker_config["files"]["results_file"] = str(worker_dir / "results_manifest.json")

    worker_config.setdefault("runtime", {})
    worker_config["runtime"]["worker_name"] = worker_name
    worker_config["runtime"]["worker_index"] = worker_index
    worker_config["runtime"]["worker_count"] = worker_count
    worker_config["runtime"]["vimeo_authenticated_session"] = True

    worker_config.setdefault("telegram", {})
    worker_config["telegram"]["enabled"] = False
    return worker_name, worker_config


def load_worker_summary(worker_dir):
    summary_path = Path(worker_dir) / "summary.json"
    if not summary_path.exists():
        return None
    with open(summary_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_worker_results(worker_dir):
    results_path = Path(worker_dir) / "results_manifest.json"
    if not results_path.exists():
        return None
    with open(results_path, "r", encoding="utf-8") as f:
        return json.load(f)


def summarize_worker_results(worker_dir, worker_name):
    worker_results = load_worker_results(worker_dir)
    if not worker_results:
        return None

    items = worker_results.get("items", [])
    total = len(items)
    downloadable = 0
    has_original = 0
    cloudflare_blocked = 0
    errors = 0
    pending = 0

    for item in items:
        status = item.get("status")
        if status == "downloadable_original":
            downloadable += 1
            has_original += 1
        elif status == "downloadable_non_original":
            downloadable += 1
        elif status == "cloudflare_blocked":
            cloudflare_blocked += 1
        elif status in {"page_error", "download_modal_not_found", "no_download_options", "no_usable_download_links"}:
            errors += 1
        elif status == "pending":
            pending += 1

    processed = total - pending
    return {
        "worker_name": worker_name,
        "total": total,
        "processed": processed,
        "downloadable": downloadable,
        "has_original": has_original,
        "without_original": downloadable - has_original,
        "cloudflare_blocked": cloudflare_blocked,
        "errors": errors,
        "pending": pending,
        "updated_at": worker_results.get("updated_at"),
    }


def build_progress_lines(processes, total_urls):
    worker_summaries = []
    totals = {
        "processed": 0,
        "downloadable": 0,
        "has_original": 0,
        "without_original": 0,
        "cloudflare_blocked": 0,
        "errors": 0,
        "pending": 0,
    }

    for item in processes:
        summary = summarize_worker_results(item["worker_dir"], item["worker_name"])
        if summary is None:
            continue
        worker_summaries.append(summary)
        for key in totals:
            totals[key] += summary[key]

    progress_pct = (totals["processed"] / total_urls * 100) if total_urls else 0
    lines = [
        f"processed: {totals['processed']}/{total_urls} ({progress_pct:.1f}%)",
        f"downloadable: {totals['downloadable']}",
        f"has_original: {totals['has_original']}",
        f"without_original: {totals['without_original']}",
        f"cloudflare_blocked: {totals['cloudflare_blocked']}",
        f"errors: {totals['errors']}",
        f"pending: {totals['pending']}",
    ]
    for summary in worker_summaries:
        lines.append(
            " / ".join(
                [
                    summary["worker_name"],
                    f"done {summary['processed']}/{summary['total']}",
                    f"dl {summary['downloadable']}",
                    f"orig {summary['has_original']}",
                    f"nonorig {summary['without_original']}",
                    f"cf {summary['cloudflare_blocked']}",
                    f"err {summary['errors']}",
                    f"p {summary['pending']}",
                ]
            )
        )
    return lines


def main():
    args = parse_args()
    master_config = load_config(args.config)
    worker_count = args.workers or int(master_config.get("workers", {}).get("count", 1))
    worker_count = max(1, worker_count)

    with open(master_config["files"]["source_json"], "r", encoding="utf-8") as f:
        urls = json.load(f)
    if master_config["settings"]["test_mode"]:
        urls = urls[: int(master_config["settings"]["test_limit"])]
    if not urls:
        print("No URLs to process")
        return 0

    worker_count = min(worker_count, len(urls))
    shards = shard_urls(urls, worker_count)

    logs_dir = Path(master_config["files"]["logs_dir"])
    workers_root = logs_dir / "workers"
    workers_root.mkdir(parents=True, exist_ok=True)

    coordinator_log_file = workers_root / "coordinator.log"
    coordinator_logger = setup_logger(coordinator_log_file)
    coordinator_logger.info("Coordinator config: %s", master_config["_meta"]["config_path"])
    coordinator_logger.info("Worker count: %d", worker_count)
    coordinator_logger.info("Total URLs after test mode: %d", len(urls))
    coordinator_logger.info("Authenticated session: True")

    processes = []
    python_bin = sys.executable
    worker_script = PROJECT_ROOT / "original_visibility_dry_run" / "inspect_logged_in.py"
    stagger_seconds = int(master_config.get("workers", {}).get("stagger_start_seconds", 3))
    progress_every_seconds = max(
        30,
        int(master_config.get("telegram", {}).get("notify_coordinator_progress_every_seconds", 60) or 60),
    )
    last_progress_ts = 0.0

    for idx, shard in enumerate(shards, start=1):
        worker_dir = workers_root / f"worker_{idx:02d}"
        worker_dir.mkdir(parents=True, exist_ok=True)

        shard_path = worker_dir / "source.json"
        with open(shard_path, "w", encoding="utf-8") as f:
            json.dump(shard, f, indent=2, ensure_ascii=False)

        worker_name, worker_config = build_worker_config(
            master_config,
            worker_dir,
            shard_path,
            idx,
            worker_count,
        )
        worker_config_path = worker_dir / "config.json"
        with open(worker_config_path, "w", encoding="utf-8") as f:
            json.dump(worker_config, f, indent=2, ensure_ascii=False)

        coordinator_logger.info(
            "Starting %s with %d URLs (config=%s)",
            worker_name,
            len(shard),
            worker_config_path,
        )
        process = subprocess.Popen(
            [python_bin, str(worker_script), "--config", str(worker_config_path)],
            cwd=str(DOWNLOADER_PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            env=dict(os.environ),
        )
        processes.append(
            {
                "worker_name": worker_name,
                "worker_dir": worker_dir,
                "config_path": worker_config_path,
                "process": process,
            }
        )
        if idx < len(shards):
            time.sleep(stagger_seconds)

    exit_code = 0
    unfinished = {item["worker_name"] for item in processes}
    while unfinished:
        time.sleep(5)
        now = time.time()
        if now - last_progress_ts >= progress_every_seconds:
            for line in build_progress_lines(processes, len(urls)):
                coordinator_logger.info(line)
            last_progress_ts = now

        for item in processes:
            worker_name = item["worker_name"]
            if worker_name not in unfinished:
                continue
            return_code = item["process"].poll()
            if return_code is None:
                continue
            unfinished.remove(worker_name)
            coordinator_logger.info("%s finished with exit code %s", worker_name, return_code)
            if return_code != 0:
                exit_code = 1

    summaries = []
    aggregate_items = []
    for item in processes:
        summary = load_worker_summary(item["worker_dir"])
        if summary is None:
            exit_code = 1
            coordinator_logger.error("Missing summary for %s", item["worker_name"])
            continue
        summaries.append(summary)

        results = load_worker_results(item["worker_dir"])
        if results:
            for result_item in results.get("items", []):
                aggregate_items.append(
                    {
                        "worker_name": item["worker_name"],
                        **result_item,
                    }
                )

    aggregate = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "worker_count": worker_count,
        "total_urls": len(urls),
        "processed": sum(item.get("processed", 0) for item in summaries),
        "downloadable": sum(item.get("downloadable", 0) for item in summaries),
        "has_original": sum(item.get("has_original", 0) for item in summaries),
        "without_original": sum(item.get("without_original", 0) for item in summaries),
        "cloudflare_blocked": sum(item.get("cloudflare_blocked", 0) for item in summaries),
        "download_modal_not_found": sum(item.get("download_modal_not_found", 0) for item in summaries),
        "no_download_options": sum(item.get("no_download_options", 0) for item in summaries),
        "no_usable_download_links": sum(item.get("no_usable_download_links", 0) for item in summaries),
        "page_error": sum(item.get("page_error", 0) for item in summaries),
        "workers": summaries,
        "exit_code": exit_code,
    }
    aggregate["pending"] = max(0, len(urls) - aggregate["processed"])
    aggregate["has_original_pct_total"] = (
        aggregate["has_original"] / len(urls) * 100 if urls else 0.0
    )
    aggregate["has_original_pct_downloadable"] = (
        aggregate["has_original"] / aggregate["downloadable"] * 100
        if aggregate["downloadable"]
        else 0.0
    )

    aggregate_results_manifest = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "worker_count": worker_count,
        "total_urls": len(urls),
        "items": aggregate_items,
    }

    aggregate_results_manifest_path = workers_root / "aggregate_results_manifest.json"
    with open(aggregate_results_manifest_path, "w", encoding="utf-8") as f:
        json.dump(aggregate_results_manifest, f, indent=2, ensure_ascii=False)

    aggregate_summary_path = workers_root / "aggregate_summary.json"
    with open(aggregate_summary_path, "w", encoding="utf-8") as f:
        json.dump(aggregate, f, indent=2, ensure_ascii=False)

    coordinator_logger.info("Aggregate summary saved to %s", aggregate_summary_path)
    for line in build_progress_lines(processes, len(urls)):
        coordinator_logger.info(line)
    coordinator_logger.info(
        "Original visibility total: %d/%d (%.1f%%)",
        aggregate["has_original"],
        len(urls),
        aggregate["has_original_pct_total"],
    )
    coordinator_logger.info(
        "Original visibility among downloadable: %d/%d (%.1f%%)",
        aggregate["has_original"],
        aggregate["downloadable"],
        aggregate["has_original_pct_downloadable"],
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

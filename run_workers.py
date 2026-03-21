"""Launch multiple Vimeo downloader workers with separate configs and logs."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from download_vimeo_seleniumbase_v3 import PROJECT_ROOT, load_config, setup_logger
from launcher_support import configure_launched_worker_notifications, get_storage_snapshot
from result_exports import write_result_url_lists
from telegram_notifier import TelegramNotifier


def parse_args():
    parser = argparse.ArgumentParser(description="Launch multiple Vimeo downloader workers")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"), help="Path to master config JSON")
    parser.add_argument("--workers", type=int, default=None, help="Number of workers to start")
    return parser.parse_args()


def shard_urls(urls, worker_count):
    shards = [[] for _ in range(worker_count)]
    for index, url in enumerate(urls):
        shards[index % worker_count].append(url)
    return shards


def build_worker_config(master_config, worker_dir, shard_path, worker_index, worker_count):
    worker_name = f"worker-{worker_index:02d}"
    worker_config = json.loads(json.dumps(master_config))
    # Worker configs are written into per-worker output directories, so they must
    # be self-contained and must not try to resolve the original profile secrets
    # file relative to the worker directory.
    worker_config.pop("secrets_file", None)
    worker_config.pop("_meta", None)
    shared_media_dirs = bool(master_config.get("workers", {}).get("shared_media_dirs", False))
    api_pool = master_config.get("workers", {}).get("api_pool") or []

    worker_config["files"]["source_json"] = str(shard_path)
    if shared_media_dirs:
        worker_config["files"]["videos_dir"] = master_config["files"]["videos_dir"]
        worker_config["files"]["jsons_dir"] = master_config["files"]["jsons_dir"]
    else:
        worker_config["files"]["videos_dir"] = str(worker_dir / "videos")
        worker_config["files"]["jsons_dir"] = str(worker_dir / "jsons")
    worker_config["files"]["logs_dir"] = str(worker_dir)
    worker_config["files"]["failed_downloads"] = str(worker_dir / "failed_downloads.json")
    worker_config["files"]["log_file"] = str(worker_dir / "download.log")
    worker_config["files"]["summary_file"] = str(worker_dir / "summary.json")
    worker_config["files"]["results_file"] = str(worker_dir / "results_manifest.json")

    worker_config.setdefault("runtime", {})
    worker_config["runtime"]["worker_name"] = worker_name
    worker_config["runtime"]["worker_index"] = worker_index
    worker_config["runtime"]["worker_count"] = worker_count
    worker_config["runtime"]["vimeo_authenticated_session"] = bool(
        worker_config.get("runtime", {}).get("vimeo_authenticated_session", False)
    )

    worker_config.setdefault("resume", {})
    worker_config["resume"]["state_file"] = str(worker_dir / "resume_state.json")
    worker_config.setdefault("workers", {})
    worker_config["workers"]["shared_media_dirs"] = shared_media_dirs

    if worker_index <= len(api_pool):
        api_creds = api_pool[worker_index - 1] or {}
        if all(api_creds.get(key) for key in ("client_id", "client_secret", "token")):
            worker_config.setdefault("vimeo_api", {})
            worker_config["vimeo_api"]["client_id"] = api_creds["client_id"]
            worker_config["vimeo_api"]["secret"] = api_creds["client_secret"]
            worker_config["vimeo_api"]["token"] = api_creds["token"]
            worker_config["runtime"]["api_pool_slot"] = worker_index
        else:
            worker_config["runtime"]["api_pool_slot"] = None
    else:
        worker_config["runtime"]["api_pool_slot"] = None

    configure_launched_worker_notifications(worker_config)
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
    downloaded = 0
    skipped = 0
    failed = 0
    pending = 0

    for item in items:
        status = item.get("status")
        if status == "downloaded":
            downloaded += 1
        elif status == "skipped":
            skipped += 1
        elif status == "failed":
            failed += 1
        else:
            pending += 1

    processed = downloaded + skipped + failed
    return {
        "worker_name": worker_name,
        "total": total,
        "processed": processed,
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "pending": pending,
        "updated_at": worker_results.get("updated_at"),
    }


def build_coordinator_progress_lines(processes, total_urls, storage_root=None):
    worker_summaries = []
    totals = {
        "processed": 0,
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "pending": 0,
    }

    for item in processes:
        summary = summarize_worker_results(item["worker_dir"], item["worker_name"])
        if summary is None:
            continue
        worker_summaries.append(summary)
        totals["processed"] += summary["processed"]
        totals["downloaded"] += summary["downloaded"]
        totals["skipped"] += summary["skipped"]
        totals["failed"] += summary["failed"]
        totals["pending"] += summary["pending"]

    progress_pct = (totals["processed"] / total_urls * 100) if total_urls else 0
    lines = [
        f"processed: <code>{totals['processed']}/{total_urls} ({progress_pct:.1f}%)</code>",
        f"downloaded: <code>{totals['downloaded']}</code>",
        f"skipped: <code>{totals['skipped']}</code>",
        f"failed: <code>{totals['failed']}</code>",
        f"pending: <code>{totals['pending']}</code>",
    ]

    storage = get_storage_snapshot(storage_root)
    if storage:
        lines.append(f"storage_used: <code>{storage['human']}</code>")

    for summary in worker_summaries:
        lines.append(
            " / ".join(
                [
                    f"{summary['worker_name']}",
                    f"done <code>{summary['processed']}/{summary['total']}</code>",
                    f"d <code>{summary['downloaded']}</code>",
                    f"s <code>{summary['skipped']}</code>",
                    f"f <code>{summary['failed']}</code>",
                    f"p <code>{summary['pending']}</code>",
                ]
            )
        )

    return lines


def main():
    args = parse_args()
    master_config = load_config(args.config)
    workers_cfg = master_config.setdefault("workers", {})
    worker_count = args.workers or int(workers_cfg.get("count", 1))
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
    coordinator_logger.info(
        "Shared media dirs: %s",
        bool(workers_cfg.get("shared_media_dirs", False)),
    )
    storage_root = master_config.get("offload", {}).get("storage_root")

    telegram = TelegramNotifier(
        master_config,
        worker_name="coordinator",
        job_name=master_config["runtime"]["job_name"],
    )
    telegram.notify_custom(
        "Coordinator start",
        [
            f"workers: {worker_count}",
            f"urls: {len(urls)}",
            f"config: <code>{master_config['_meta']['config_path']}</code>",
            f"storage_root: <code>{storage_root}</code>" if storage_root else "storage_root: <code>-</code>",
        ],
    )
    last_coordinator_progress_ts = 0.0

    processes = []
    stagger_seconds = int(workers_cfg.get("stagger_start_seconds", 3))
    python_bin = sys.executable
    worker_script = PROJECT_ROOT / "download_vimeo_seleniumbase_v3.py"

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
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
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
        for item in processes:
            worker_name = item["worker_name"]
            if worker_name not in unfinished:
                continue

            process = item["process"]
            return_code = process.poll()
            if return_code is None:
                continue

            unfinished.remove(worker_name)
            coordinator_logger.info("%s finished with exit code %s", worker_name, return_code)
            if return_code != 0:
                exit_code = 1
                if telegram.notify_on_error:
                    telegram.notify_custom(
                        "Worker exited with error",
                        [
                            f"worker: <code>{worker_name}</code>",
                            f"exit_code: {return_code}",
                            f"config: <code>{item['config_path']}</code>",
                        ],
                    )

        progress_every_seconds = telegram.notify_coordinator_progress_every_seconds
        now = time.time()
        if progress_every_seconds > 0 and now - last_coordinator_progress_ts >= progress_every_seconds:
            telegram.notify_custom(
                "Coordinator progress",
                build_coordinator_progress_lines(processes, len(urls), storage_root=storage_root),
            )
            last_coordinator_progress_ts = now

    summaries = []
    for item in processes:
        summary = load_worker_summary(item["worker_dir"])
        if summary is None:
            exit_code = 1
            coordinator_logger.error("Missing summary for %s", item["worker_name"])
            telegram.notify_custom(
                "Missing worker summary",
                [
                    f"worker: <code>{item['worker_name']}</code>",
                    f"dir: <code>{item['worker_dir']}</code>",
                ],
            )
            continue
        summaries.append(summary)

    aggregate = {
        "worker_count": worker_count,
        "total_urls": len(urls),
        "downloaded": sum(item.get("downloaded", 0) for item in summaries),
        "skipped": sum(item.get("skipped", 0) for item in summaries),
        "failed": sum(item.get("failed", 0) for item in summaries),
        "workers": summaries,
        "exit_code": exit_code,
    }
    aggregate["total_processed"] = (
        aggregate["downloaded"] + aggregate["skipped"] + aggregate["failed"]
    )
    storage = get_storage_snapshot(storage_root)
    if storage:
        aggregate["storage"] = storage

    aggregate_results_items = []
    for item in processes:
        worker_results = load_worker_results(item["worker_dir"])
        if not worker_results:
            continue
        for result_item in worker_results.get("items", []):
            aggregate_results_items.append(
                {
                    "worker_name": item["worker_name"],
                    **result_item,
                }
            )

    aggregate_results_manifest = {
        "worker_count": worker_count,
        "total_urls": len(urls),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "items": aggregate_results_items,
    }
    aggregate_results_manifest_path = workers_root / "aggregate_results_manifest.json"
    with open(aggregate_results_manifest_path, "w", encoding="utf-8") as f:
        json.dump(aggregate_results_manifest, f, indent=2, ensure_ascii=False)

    exported_lists = write_result_url_lists(aggregate_results_items, workers_root)
    aggregate["url_list_exports"] = exported_lists

    aggregate_summary_path = workers_root / "aggregate_summary.json"
    with open(aggregate_summary_path, "w", encoding="utf-8") as f:
        json.dump(aggregate, f, indent=2, ensure_ascii=False)

    coordinator_logger.info("Aggregate summary saved to %s", aggregate_summary_path)
    for bucket, path in exported_lists.items():
        coordinator_logger.info("Exported %s URLs to %s", bucket, path)
    telegram.notify_custom(
        "Coordinator finish",
        [
            f"workers: {worker_count}",
            f"processed: {aggregate['total_processed']}/{len(urls)}",
            f"downloaded: {aggregate['downloaded']}",
            f"skipped: {aggregate['skipped']}",
            f"failed: {aggregate['failed']}",
            f"exit_code: {exit_code}",
            f"storage_used: <code>{storage['human']}</code>" if storage else "storage_used: <code>missing</code>",
            f"storage_root: <code>{storage['path']}</code>" if storage else "storage_root: <code>-</code>",
            f"summary: <code>{aggregate_summary_path}</code>",
            f"results: <code>{aggregate_results_manifest_path}</code>",
            f"downloaded_urls: <code>{exported_lists['downloaded_original']}</code>",
            f"not_downloaded_urls: <code>{exported_lists['not_downloaded_downloadable']}</code>",
            f"no_links_urls: <code>{exported_lists['no_links']}</code>",
        ],
        wait=True,
    )
    telegram.shutdown(timeout=10)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

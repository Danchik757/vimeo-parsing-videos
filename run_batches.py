"""Batch coordinator for sequential 10k Vimeo URL runs with global progress tracking."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from download_vimeo_seleniumbase_v3 import (
    PROJECT_ROOT,
    compute_urls_signature,
    load_config,
    setup_logger,
)
from telegram_notifier import TelegramNotifier


FINALIZED_STATUSES = {"downloaded", "skipped"}


def now_string():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def parse_args():
    parser = argparse.ArgumentParser(description="Run Vimeo downloader in sequential batches")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"), help="Path to config JSON")
    parser.add_argument("--workers", type=int, default=None, help="Number of workers per batch")
    parser.add_argument("--batch-size", type=int, default=None, help="Batch size override")
    parser.add_argument("--batch-start", type=int, default=None, help="First batch number to process")
    parser.add_argument("--batch-end", type=int, default=None, help="Last batch number to process")
    parser.add_argument("--max-batches", type=int, default=None, help="Stop after this many batches")
    parser.add_argument("--shards-dir", default=None, help="Override directory for generated batch shard JSON files")
    parser.add_argument("--prepare-only", action="store_true", help="Only split the source list into shard files")
    return parser.parse_args()


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    temp_path.replace(path)


def load_master_urls(config):
    source_path = Path(config["files"]["source_json"])
    urls = read_json(source_path)
    if config["settings"]["test_mode"]:
        return urls[: int(config["settings"]["test_limit"])]
    return urls


def default_batches_manifest(config, urls, source_signature, batch_size):
    total_batches = (len(urls) + batch_size - 1) // batch_size
    batches = []
    for batch_number in range(1, total_batches + 1):
        start_index = (batch_number - 1) * batch_size
        end_index = min(start_index + batch_size, len(urls))
        shard_path = Path(config["batches"]["shards_dir"]) / f"batch_{batch_number:04d}.json"
        batches.append(
            {
                "batch_number": batch_number,
                "start_index": start_index + 1,
                "end_index": end_index,
                "total_urls": end_index - start_index,
                "source_json": str(shard_path),
                "status": "pending",
                "summary_file": None,
                "updated_at": None,
            }
        )

    return {
        "source_json": config["files"]["source_json"],
        "source_signature": source_signature,
        "batch_size": batch_size,
        "total_urls": len(urls),
        "total_batches": total_batches,
        "updated_at": None,
        "batches": batches,
    }


def prepare_batches_manifest(config, urls, source_signature, batch_size, logger):
    manifest_path = Path(config["batches"]["manifest_file"])
    default_manifest = default_batches_manifest(config, urls, source_signature, batch_size)
    batches = default_manifest["batches"]

    valid_existing = False
    if (
        config["batches"]["reuse_existing_shards"]
        and manifest_path.exists()
    ):
        try:
            existing = read_json(manifest_path)
            valid_existing = (
                existing.get("source_json") == config["files"]["source_json"]
                and existing.get("source_signature") == source_signature
                and int(existing.get("batch_size", 0)) == batch_size
                and int(existing.get("total_urls", -1)) == len(urls)
                and int(existing.get("total_batches", -1)) == default_manifest["total_batches"]
                and isinstance(existing.get("batches"), list)
                and len(existing["batches"]) == len(batches)
                and all(Path(item["source_json"]).exists() for item in existing["batches"])
            )
            if valid_existing:
                logger.info("Reusing existing shard manifest %s", manifest_path)
                return existing
        except Exception as exc:
            logger.warning("Failed to reuse batch manifest %s: %s", manifest_path, exc)

    logger.info(
        "Preparing %d batch shard files in %s",
        len(batches),
        config["batches"]["shards_dir"],
    )
    for batch in batches:
        start = batch["start_index"] - 1
        end = batch["end_index"]
        shard_urls = urls[start:end]
        write_json(batch["source_json"], shard_urls)

    write_json(manifest_path, default_manifest)
    logger.info("Batch manifest saved to %s", manifest_path)
    return default_manifest


def default_batch_state(config, source_signature, total_urls, total_batches, batch_size):
    return {
        "source_json": config["files"]["source_json"],
        "source_signature": source_signature,
        "batch_size": batch_size,
        "total_urls": total_urls,
        "total_batches": total_batches,
        "current_batch_number": None,
        "next_batch_number": 1,
        "completed_batches": [],
        "completed": False,
        "last_batch_number": None,
        "last_exit_code": None,
        "updated_at": None,
    }


def load_batch_state(config, source_signature, total_urls, total_batches, batch_size, logger):
    state_path = Path(config["batches"]["state_file"])
    state = default_batch_state(config, source_signature, total_urls, total_batches, batch_size)
    if not state_path.exists():
        return state

    try:
        loaded = read_json(state_path)
    except Exception as exc:
        logger.warning("Failed to read batch state %s: %s. Starting fresh.", state_path, exc)
        return state

    if loaded.get("source_json") != config["files"]["source_json"]:
        logger.warning("Batch state source mismatch. Starting fresh.")
        return state
    if loaded.get("source_signature") != source_signature:
        logger.warning("Batch state signature mismatch. Starting fresh.")
        return state
    if int(loaded.get("batch_size", 0)) != batch_size:
        logger.warning("Batch state batch_size mismatch. Starting fresh.")
        return state
    if int(loaded.get("total_urls", -1)) != total_urls:
        logger.warning("Batch state total_urls mismatch. Starting fresh.")
        return state
    if int(loaded.get("total_batches", -1)) != total_batches:
        logger.warning("Batch state total_batches mismatch. Starting fresh.")
        return state

    state.update(loaded)
    state["completed_batches"] = sorted({int(item) for item in state.get("completed_batches", [])})
    state["next_batch_number"] = max(1, min(int(state.get("next_batch_number", 1)), total_batches + 1))
    current_batch = state.get("current_batch_number")
    if current_batch is not None:
        state["current_batch_number"] = int(current_batch)
    state["completed"] = bool(state.get("completed", False))
    return state


def save_batch_state(config, state):
    payload = dict(state)
    payload["updated_at"] = now_string()
    write_json(config["batches"]["state_file"], payload)


def default_global_results(config, source_signature, total_urls, batch_size):
    return {
        "source_json": config["files"]["source_json"],
        "source_signature": source_signature,
        "batch_size": batch_size,
        "total_urls": total_urls,
        "updated_at": None,
        "counts": {
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "finalized": 0,
        },
        "items": [],
    }


def load_global_results(config, source_signature, total_urls, batch_size, logger):
    results_path = Path(config["batches"]["global_results_file"])
    default_results = default_global_results(config, source_signature, total_urls, batch_size)
    if not results_path.exists():
        return default_results

    try:
        loaded = read_json(results_path)
    except Exception as exc:
        logger.warning("Failed to read global results %s: %s. Starting fresh.", results_path, exc)
        return default_results

    if loaded.get("source_json") != config["files"]["source_json"]:
        logger.warning("Global results source mismatch. Starting fresh.")
        return default_results
    if loaded.get("source_signature") != source_signature:
        logger.warning("Global results signature mismatch. Starting fresh.")
        return default_results
    if int(loaded.get("batch_size", 0)) != batch_size:
        logger.warning("Global results batch_size mismatch. Starting fresh.")
        return default_results
    if int(loaded.get("total_urls", -1)) != total_urls:
        logger.warning("Global results total_urls mismatch. Starting fresh.")
        return default_results

    items = loaded.get("items")
    if not isinstance(items, list):
        logger.warning("Global results items invalid. Starting fresh.")
        return default_results
    return loaded


def build_global_index(global_results):
    index = {}
    for item in global_results.get("items", []):
        url = item.get("url")
        if url:
            index[url] = item
    return index


def recalculate_global_counts(global_results):
    counts = {
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "finalized": 0,
    }
    for item in global_results.get("items", []):
        status = item.get("status")
        if status in counts:
            counts[status] += 1
        if status in FINALIZED_STATUSES:
            counts["finalized"] += 1
    global_results["counts"] = counts
    global_results["updated_at"] = now_string()


def save_global_results(config, global_results):
    recalculate_global_counts(global_results)
    write_json(config["batches"]["global_results_file"], global_results)


def write_global_downloaded(config, global_results):
    downloaded_items = [
        item for item in global_results.get("items", []) if item.get("status") == "downloaded"
    ]
    payload = {
        "source_json": global_results["source_json"],
        "source_signature": global_results["source_signature"],
        "updated_at": now_string(),
        "downloaded": len(downloaded_items),
        "items": downloaded_items,
    }
    write_json(config["batches"]["global_downloaded_file"], payload)


def write_global_summary(config, global_results, batch_state):
    counts = global_results.get("counts", {})
    total_urls = int(global_results.get("total_urls", 0))
    finalized = int(counts.get("finalized", 0))
    summary = {
        "source_json": global_results["source_json"],
        "source_signature": global_results["source_signature"],
        "updated_at": now_string(),
        "total_urls": total_urls,
        "downloaded": int(counts.get("downloaded", 0)),
        "skipped": int(counts.get("skipped", 0)),
        "failed_logged": int(counts.get("failed", 0)),
        "finalized_urls": finalized,
        "remaining_urls": max(0, total_urls - finalized),
        "completed_batches": len(batch_state.get("completed_batches", [])),
        "total_batches": batch_state.get("total_batches", 0),
        "next_batch_number": batch_state.get("next_batch_number"),
        "current_batch_number": batch_state.get("current_batch_number"),
        "completed": bool(batch_state.get("completed", False)),
    }
    write_json(config["batches"]["global_summary_file"], summary)


def build_batch_config(master_config, batch_dir, source_json_path, batch_number, total_batches):
    batch_config = json.loads(json.dumps(master_config))
    batch_name = f"batch-{batch_number:04d}"

    batch_config["files"]["source_json"] = str(source_json_path)
    batch_config["files"]["logs_dir"] = str(batch_dir)
    batch_config["files"]["failed_downloads"] = str(batch_dir / "failed_downloads.json")
    batch_config["files"]["log_file"] = str(batch_dir / "batch.log")
    batch_config["files"]["summary_file"] = str(batch_dir / "summary.json")
    batch_config["files"]["results_file"] = str(batch_dir / "results_manifest.json")

    batch_config["settings"]["test_mode"] = False

    batch_config.setdefault("runtime", {})
    batch_config["runtime"]["job_name"] = f"{master_config['runtime']['job_name']} / {batch_name}"
    batch_config["runtime"]["worker_name"] = "coordinator"
    batch_config["runtime"]["batch_number"] = batch_number
    batch_config["runtime"]["batch_count"] = total_batches

    batch_config.setdefault("workers", {})
    batch_config["workers"]["shared_media_dirs"] = True

    batch_config.setdefault("batches", {})
    batch_config["batches"]["enabled"] = False

    return batch_config


def load_batch_pending_count(batch_config_path):
    config = read_json(batch_config_path)
    return len(read_json(config["files"]["source_json"]))


def choose_next_batch(batch_state):
    current_batch = batch_state.get("current_batch_number")
    completed_batches = set(batch_state.get("completed_batches", []))
    if current_batch and current_batch not in completed_batches:
        return current_batch
    return int(batch_state.get("next_batch_number", 1))


def should_skip_by_global_registry(item):
    if not item:
        return False
    return item.get("status") in FINALIZED_STATUSES


def merge_batch_results(global_results, batch_results, batch_number, url_to_index):
    items_by_url = build_global_index(global_results)
    for item in batch_results.get("items", []):
        status = item.get("status")
        url = item.get("url")
        if status not in {"downloaded", "skipped", "failed"} or not url:
            continue

        existing = items_by_url.get(url, {})
        merged = dict(existing)
        merged.update(item)
        merged["batch_number"] = batch_number
        merged["source_index"] = url_to_index.get(url)
        merged["finalized"] = status in FINALIZED_STATUSES

        if existing.get("status") == "downloaded" and status != "downloaded":
            merged = dict(existing)
        items_by_url[url] = merged

    global_results["items"] = sorted(
        items_by_url.values(),
        key=lambda item: (
            int(item.get("source_index") or 10**12),
            str(item.get("url") or ""),
        ),
    )


def finalize_batch_manifest(batch_manifest, batch_number, status, summary_file):
    batch_entry = batch_manifest["batches"][batch_number - 1]
    batch_entry["status"] = status
    batch_entry["summary_file"] = str(summary_file) if summary_file else None
    batch_entry["updated_at"] = now_string()
    batch_manifest["updated_at"] = now_string()


def create_empty_batch_summary(batch_number, batch_dir, original_urls, skipped_as_processed):
    summary = {
        "batch_number": batch_number,
        "original_urls": original_urls,
        "pending_urls": 0,
        "already_finalized_before_start": skipped_as_processed,
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "total_processed": 0,
        "exit_code": 0,
        "status": "already_finalized",
        "updated_at": now_string(),
    }
    write_json(Path(batch_dir) / "batch_summary.json", summary)
    return summary


def wrap_batch_summary(batch_number, original_urls, pending_urls, already_finalized, aggregate_summary):
    return {
        "batch_number": batch_number,
        "original_urls": original_urls,
        "pending_urls": pending_urls,
        "already_finalized_before_start": already_finalized,
        "downloaded": int(aggregate_summary.get("downloaded", 0)),
        "skipped": int(aggregate_summary.get("skipped", 0)),
        "failed": int(aggregate_summary.get("failed", 0)),
        "total_processed": int(aggregate_summary.get("total_processed", 0)),
        "exit_code": int(aggregate_summary.get("exit_code", 0)),
        "status": "completed" if int(aggregate_summary.get("exit_code", 0)) == 0 else "failed",
        "updated_at": now_string(),
        "aggregate_summary_file": aggregate_summary.get("summary_file"),
        "aggregate_results_file": aggregate_summary.get("results_file"),
    }


def run_batch(batch_config_path, workers):
    cmd = [sys.executable, str(PROJECT_ROOT / "run_workers.py"), "--config", str(batch_config_path)]
    if workers is not None:
        cmd.extend(["--workers", str(workers)])
    return subprocess.call(cmd, cwd=str(PROJECT_ROOT))


def main():
    args = parse_args()
    master_config = load_config(args.config)
    if args.shards_dir:
        shards_dir = Path(args.shards_dir)
        if not shards_dir.is_absolute():
            shards_dir = (Path(master_config["_meta"]["config_dir"]) / shards_dir).resolve()
        master_config["batches"]["shards_dir"] = str(shards_dir)
    urls = load_master_urls(master_config)
    if not urls:
        print("No URLs to process")
        return 0

    batch_size = args.batch_size or int(master_config["batches"].get("batch_size", 10000))
    if args.batch_size is None and not master_config["batches"].get("enabled", False):
        batch_size = len(urls)
    batch_size = max(1, batch_size)
    max_batches = args.max_batches
    if max_batches is None:
        max_batches = int(master_config["batches"].get("max_batches", 0))
    max_batches = max(0, int(max_batches or 0))

    batch_logs_root = Path(master_config["batches"]["runs_dir"])
    batch_logs_root.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(batch_logs_root / "batch_coordinator.log")
    logger.info("Batch coordinator config: %s", master_config["_meta"]["config_path"])
    logger.info("Total URLs after test mode: %d", len(urls))
    logger.info("Batch size: %d", batch_size)

    source_signature = compute_urls_signature(urls)
    batch_manifest = prepare_batches_manifest(master_config, urls, source_signature, batch_size, logger)
    total_batches = int(batch_manifest["total_batches"])
    batch_start = max(1, int(args.batch_start or 1))
    batch_end = min(total_batches, int(args.batch_end or total_batches))
    if batch_start > batch_end:
        logger.error("Invalid batch range: start=%s end=%s total=%s", batch_start, batch_end, total_batches)
        return 2

    if args.prepare_only:
        logger.info(
            "Prepare-only mode finished. Created %d shard files in range %04d-%04d.",
            total_batches,
            batch_start,
            batch_end,
        )
        return 0

    telegram = TelegramNotifier(
        master_config,
        worker_name="batch-coordinator",
        job_name=master_config["runtime"]["job_name"],
    )
    global_results = load_global_results(
        master_config,
        source_signature,
        len(urls),
        batch_size,
        logger,
    )
    batch_state = load_batch_state(
        master_config,
        source_signature,
        len(urls),
        total_batches,
        batch_size,
        logger,
    )
    write_global_summary(master_config, global_results, batch_state)

    if batch_state.get("completed"):
        logger.info("All batches already completed according to %s", master_config["batches"]["state_file"])
        return 0

    telegram.notify_custom(
        "batch_run_start",
        [
            f"total_urls: <code>{len(urls)}</code>",
            f"batch_size: <code>{batch_size}</code>",
            f"total_batches: <code>{total_batches}</code>",
            f"range: <code>{batch_start}-{batch_end}</code>",
            f"config: <code>{master_config['_meta']['config_path']}</code>",
        ],
    )

    url_to_index = {url: idx for idx, url in enumerate(urls, start=1)}
    handled_batches = 0
    batch_number = choose_next_batch(batch_state)
    if batch_number < batch_start or batch_number > batch_end:
        batch_number = batch_start

    while batch_number <= batch_end:
        if max_batches and handled_batches >= max_batches:
            logger.info("Reached max_batches=%d, stopping.", max_batches)
            break

        batch_entry = batch_manifest["batches"][batch_number - 1]
        batch_dir = batch_logs_root / f"batch_{batch_number:04d}"
        batch_dir.mkdir(parents=True, exist_ok=True)
        batch_config_path = batch_dir / "config.json"
        batch_source_path = Path(batch_entry["source_json"])
        batch_summary_path = batch_dir / "batch_summary.json"

        if (
            batch_state.get("current_batch_number") == batch_number
            and batch_config_path.exists()
        ):
            pending_urls_count = load_batch_pending_count(batch_config_path)
            logger.info(
                "Resuming incomplete batch %04d with %d pending URLs",
                batch_number,
                pending_urls_count,
            )
            already_finalized = batch_entry["total_urls"] - pending_urls_count
        else:
            global_index = build_global_index(global_results)
            source_urls = read_json(batch_source_path)
            pending_urls = [
                url for url in source_urls if not should_skip_by_global_registry(global_index.get(url))
            ]
            already_finalized = len(source_urls) - len(pending_urls)

            if not pending_urls:
                logger.info(
                    "Batch %04d already finalized globally (%d/%d URLs)",
                    batch_number,
                    already_finalized,
                    len(source_urls),
                )
                create_empty_batch_summary(
                    batch_number,
                    batch_dir,
                    len(source_urls),
                    already_finalized,
                )
                batch_state["completed_batches"] = sorted(
                    set(batch_state.get("completed_batches", [])) | {batch_number}
                )
                batch_state["current_batch_number"] = None
                batch_state["last_batch_number"] = batch_number
                batch_state["last_exit_code"] = 0
                batch_state["next_batch_number"] = batch_number + 1
                batch_state["completed"] = len(batch_state["completed_batches"]) >= total_batches
                save_batch_state(master_config, batch_state)
                finalize_batch_manifest(batch_manifest, batch_number, "already_finalized", batch_summary_path)
                write_json(master_config["batches"]["manifest_file"], batch_manifest)
                write_global_summary(master_config, global_results, batch_state)
                handled_batches += 1
                if not master_config["batches"].get("auto_advance", True):
                    break
                batch_number += 1
                continue

            pending_source_path = batch_dir / "source_pending.json"
            write_json(pending_source_path, pending_urls)
            batch_config = build_batch_config(
                master_config,
                batch_dir,
                pending_source_path,
                batch_number,
                total_batches,
            )
            write_json(batch_config_path, batch_config)
            batch_state["current_batch_number"] = batch_number
            batch_state["last_batch_number"] = batch_number
            save_batch_state(master_config, batch_state)
            pending_urls_count = len(pending_urls)

        logger.info(
            "Starting batch %04d: original=%d pending=%d already_finalized=%d",
            batch_number,
            batch_entry["total_urls"],
            pending_urls_count,
            already_finalized,
        )
        telegram.notify_custom(
            "batch_start",
            [
                f"batch: <code>{batch_number}/{total_batches}</code>",
                f"original_urls: <code>{batch_entry['total_urls']}</code>",
                f"pending_urls: <code>{pending_urls_count}</code>",
                f"already_finalized: <code>{already_finalized}</code>",
                f"config: <code>{batch_config_path}</code>",
            ],
        )

        return_code = run_batch(batch_config_path, args.workers)
        aggregate_summary_path = batch_dir / "workers" / "aggregate_summary.json"
        aggregate_results_path = batch_dir / "workers" / "aggregate_results_manifest.json"

        if not aggregate_summary_path.exists() or not aggregate_results_path.exists():
            logger.error(
                "Batch %04d did not produce aggregate files (exit_code=%s)",
                batch_number,
                return_code,
            )
            batch_state["last_exit_code"] = return_code
            save_batch_state(master_config, batch_state)
            telegram.notify_custom(
                "batch_error",
                [
                    f"batch: <code>{batch_number}/{total_batches}</code>",
                    f"exit_code: <code>{return_code}</code>",
                    f"aggregate_summary: <code>{aggregate_summary_path}</code>",
                    f"aggregate_results: <code>{aggregate_results_path}</code>",
                ],
            )
            return 1

        aggregate_summary = read_json(aggregate_summary_path)
        aggregate_results = read_json(aggregate_results_path)
        aggregate_summary["summary_file"] = str(aggregate_summary_path)
        aggregate_summary["results_file"] = str(aggregate_results_path)

        if return_code != 0:
            logger.error("Batch %04d finished with non-zero exit code %s", batch_number, return_code)
            batch_state["last_exit_code"] = return_code
            save_batch_state(master_config, batch_state)
            telegram.notify_custom(
                "batch_error",
                [
                    f"batch: <code>{batch_number}/{total_batches}</code>",
                    f"exit_code: <code>{return_code}</code>",
                    f"summary: <code>{aggregate_summary_path}</code>",
                ],
            )
            return return_code

        batch_summary = wrap_batch_summary(
            batch_number,
            batch_entry["total_urls"],
            pending_urls_count,
            already_finalized,
            aggregate_summary,
        )
        write_json(batch_summary_path, batch_summary)
        merge_batch_results(global_results, aggregate_results, batch_number, url_to_index)
        save_global_results(master_config, global_results)
        write_global_downloaded(master_config, global_results)

        batch_state["completed_batches"] = sorted(
            set(batch_state.get("completed_batches", [])) | {batch_number}
        )
        batch_state["current_batch_number"] = None
        batch_state["next_batch_number"] = batch_number + 1
        batch_state["last_exit_code"] = 0
        batch_state["completed"] = len(batch_state["completed_batches"]) >= total_batches
        save_batch_state(master_config, batch_state)
        write_global_summary(master_config, global_results, batch_state)

        finalize_batch_manifest(batch_manifest, batch_number, "completed", batch_summary_path)
        write_json(master_config["batches"]["manifest_file"], batch_manifest)

        logger.info(
            "Finished batch %04d: downloaded=%d skipped=%d failed=%d",
            batch_number,
            batch_summary["downloaded"],
            batch_summary["skipped"],
            batch_summary["failed"],
        )
        telegram.notify_custom(
            "batch_finish",
            [
                f"batch: <code>{batch_number}/{total_batches}</code>",
                f"downloaded: <code>{batch_summary['downloaded']}</code>",
                f"skipped: <code>{batch_summary['skipped']}</code>",
                f"failed: <code>{batch_summary['failed']}</code>",
                f"summary: <code>{batch_summary_path}</code>",
            ],
        )

        handled_batches += 1
        if not master_config["batches"].get("auto_advance", True):
            break
        batch_number += 1

    if batch_state.get("completed"):
        logger.info("All %d batches completed.", total_batches)

    telegram.notify_custom(
        "batch_run_finish",
        [
            f"completed_batches: <code>{len(batch_state.get('completed_batches', []))}/{total_batches}</code>",
            f"range: <code>{batch_start}-{batch_end}</code>",
            f"next_batch: <code>{batch_state.get('next_batch_number')}</code>",
            f"global_summary: <code>{master_config['batches']['global_summary_file']}</code>",
            f"global_results: <code>{master_config['batches']['global_results_file']}</code>",
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

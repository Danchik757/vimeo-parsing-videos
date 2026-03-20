"""Run pre-generated shard assignments with a dynamic worker queue."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from download_vimeo_seleniumbase_v3 import PROJECT_ROOT, load_config, setup_logger
from result_exports import write_result_url_lists
from telegram_notifier import TelegramNotifier


FINALIZED_STATUSES = {"downloaded", "skipped"}


def now_string():
    return time.strftime("%Y-%m-%d %H:%M:%S")


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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run server shard assignments with dynamic worker queue scheduling"
    )
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "config.json"),
        help="Path to profile config JSON",
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to server assignment manifest.json",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of worker slots to use",
    )
    parser.add_argument("--batch-start", type=int, default=None, help="First shard batch number to run")
    parser.add_argument("--batch-end", type=int, default=None, help="Last shard batch number to run")
    parser.add_argument(
        "--max-batches",
        type=int,
        default=0,
        help="Limit how many shard batches to process from the selected range (0 = all)",
    )
    return parser.parse_args()


def normalize_assignment_manifest(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    payload = read_json(manifest_path)
    batches = []
    for item in payload.get("batches", []):
        batch_number = int(item["batch_number"])
        source_json = item.get("path") or item.get("source_json")
        if not source_json:
            raise ValueError(f"Manifest batch {batch_number} is missing path/source_json")
        batches.append(
            {
                "batch_number": batch_number,
                "source_json": str(Path(source_json).resolve()),
                "filename": item.get("filename") or Path(source_json).name,
                "total_urls": int(item.get("url_count") or item.get("total_urls") or 0),
            }
        )

    if not batches:
        raise ValueError(f"No batches found in manifest: {manifest_path}")

    batches.sort(key=lambda item: item["batch_number"])
    return {
        "manifest_path": str(manifest_path),
        "server_name": payload.get("server_name") or payload.get("server") or manifest_path.parent.name,
        "source_json": payload.get("source_json"),
        "batch_size": int(payload.get("batch_size") or 0),
        "shard_count": int(payload.get("shard_count") or payload.get("total_batches") or len(batches)),
        "total_urls": int(payload.get("total_urls") or sum(item["total_urls"] for item in batches)),
        "batches": batches,
    }


def select_batches(manifest, batch_start=None, batch_end=None, max_batches=0):
    batches = manifest["batches"]
    min_batch = batches[0]["batch_number"]
    max_batch = batches[-1]["batch_number"]
    batch_start = min_batch if batch_start is None else max(min_batch, int(batch_start))
    batch_end = max_batch if batch_end is None else min(max_batch, int(batch_end))
    if batch_start > batch_end:
        raise ValueError(
            f"Invalid batch range: start={batch_start} end={batch_end} available={min_batch}-{max_batch}"
        )

    selected = [
        item for item in batches if batch_start <= item["batch_number"] <= batch_end
    ]
    if max_batches:
        selected = selected[: max(0, int(max_batches))]
    return selected


def build_assignment_state(manifest, selected_batches):
    items = {}
    for batch in selected_batches:
        batch_number = str(batch["batch_number"])
        items[batch_number] = {
            "batch_number": batch["batch_number"],
            "source_json": batch["source_json"],
            "total_urls": batch["total_urls"],
            "status": "pending",
            "assigned_worker": None,
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "batch_dir": None,
        }
    return {
        "manifest_path": manifest["manifest_path"],
        "server_name": manifest["server_name"],
        "updated_at": None,
        "items": items,
    }


def load_assignment_state(state_path, manifest, selected_batches, logger):
    default_state = build_assignment_state(manifest, selected_batches)
    state_path = Path(state_path)
    if not state_path.exists():
        return default_state

    try:
        loaded = read_json(state_path)
    except Exception as exc:
        logger.warning("Failed to read assignment state %s: %s. Starting fresh.", state_path, exc)
        return default_state

    if loaded.get("manifest_path") != manifest["manifest_path"]:
        logger.warning("Assignment state manifest mismatch. Starting fresh.")
        return default_state

    loaded_items = loaded.get("items") if isinstance(loaded.get("items"), dict) else {}
    selected_numbers = {str(item["batch_number"]) for item in selected_batches}
    merged_items = {}
    for batch in selected_batches:
        key = str(batch["batch_number"])
        item = dict(default_state["items"][key])
        item.update(loaded_items.get(key, {}))
        if item.get("status") == "running":
            item["status"] = "pending"
            item["assigned_worker"] = None
        merged_items[key] = item

    stale_items = sorted(set(loaded_items) - selected_numbers)
    if stale_items:
        logger.info("Ignoring %d stale assignment-state entries outside selected batch range", len(stale_items))

    default_state["items"] = merged_items
    return default_state


def save_assignment_state(state_path, state):
    payload = dict(state)
    payload["updated_at"] = now_string()
    write_json(state_path, payload)


def build_results_index(global_results):
    index = {}
    for item in global_results.get("items", []):
        url = item.get("url")
        if url:
            index[url] = item
    return index


def build_assignment_results(manifest, selected_batches):
    return {
        "manifest_path": manifest["manifest_path"],
        "server_name": manifest["server_name"],
        "updated_at": None,
        "total_batches": len(selected_batches),
        "total_urls": sum(item["total_urls"] for item in selected_batches),
        "counts": {
            "downloaded": 0,
            "skipped": 0,
            "failed": 0,
            "finalized": 0,
        },
        "items": [],
    }


def load_assignment_results(results_path, manifest, selected_batches, logger):
    default_results = build_assignment_results(manifest, selected_batches)
    results_path = Path(results_path)
    if not results_path.exists():
        return default_results

    try:
        loaded = read_json(results_path)
    except Exception as exc:
        logger.warning("Failed to read assignment results %s: %s. Starting fresh.", results_path, exc)
        return default_results

    if loaded.get("manifest_path") != manifest["manifest_path"]:
        logger.warning("Assignment results manifest mismatch. Starting fresh.")
        return default_results

    if int(loaded.get("total_batches", -1)) != len(selected_batches):
        logger.warning("Assignment results batch count mismatch. Starting fresh.")
        return default_results

    items = loaded.get("items")
    if not isinstance(items, list):
        logger.warning("Assignment results items invalid. Starting fresh.")
        return default_results

    return loaded


def recalculate_assignment_counts(global_results):
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


def save_assignment_results(results_path, global_results):
    recalculate_assignment_counts(global_results)
    write_json(results_path, global_results)


def merge_shard_results(global_results, shard_results, batch_number, worker_name):
    items_by_url = build_results_index(global_results)
    for item in shard_results.get("items", []):
        status = item.get("status")
        url = item.get("url")
        if status not in {"downloaded", "skipped", "failed"} or not url:
            continue

        existing = items_by_url.get(url, {})
        merged = dict(existing)
        merged.update(item)
        merged["worker_name"] = worker_name
        merged["batch_number"] = batch_number
        merged["finalized"] = status in FINALIZED_STATUSES

        if existing.get("status") == "downloaded" and status != "downloaded":
            merged = dict(existing)
        items_by_url[url] = merged

    global_results["items"] = sorted(
        items_by_url.values(),
        key=lambda item: (
            int(item.get("batch_number") or 10**12),
            str(item.get("url") or ""),
        ),
    )


def build_assignment_summary(manifest, selected_batches, state, global_results, worker_count):
    items = state.get("items", {})
    completed_batches = sum(1 for item in items.values() if item.get("status") == "completed")
    running_batches = sum(1 for item in items.values() if item.get("status") == "running")
    failed_batches = sum(1 for item in items.values() if item.get("status") == "failed")
    pending_batches = sum(1 for item in items.values() if item.get("status") == "pending")
    counts = global_results.get("counts", {})
    total_urls = int(global_results.get("total_urls", 0))
    finalized = int(counts.get("finalized", 0))

    return {
        "server_name": manifest["server_name"],
        "manifest_path": manifest["manifest_path"],
        "updated_at": now_string(),
        "worker_count": worker_count,
        "total_batches": len(selected_batches),
        "completed_batches": completed_batches,
        "running_batches": running_batches,
        "failed_batches": failed_batches,
        "pending_batches": pending_batches,
        "total_urls": total_urls,
        "downloaded": int(counts.get("downloaded", 0)),
        "skipped": int(counts.get("skipped", 0)),
        "failed_logged": int(counts.get("failed", 0)),
        "finalized_urls": finalized,
        "remaining_urls": max(0, total_urls - finalized),
        "completed": pending_batches == 0 and running_batches == 0 and failed_batches == 0,
    }


def build_worker_slot_summary(slot_name, slot_index, batch_number=None):
    return {
        "worker_name": slot_name,
        "worker_index": slot_index,
        "active_batch_number": batch_number,
        "completed_batches": [],
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "total_processed": 0,
        "updated_at": now_string(),
    }


def load_worker_slot_summary(slot_dir, slot_name, slot_index):
    path = Path(slot_dir) / "slot_summary.json"
    if not path.exists():
        return build_worker_slot_summary(slot_name, slot_index)
    payload = read_json(path)
    payload.setdefault("worker_name", slot_name)
    payload.setdefault("worker_index", slot_index)
    payload.setdefault("active_batch_number", None)
    payload.setdefault("completed_batches", [])
    payload.setdefault("downloaded", 0)
    payload.setdefault("skipped", 0)
    payload.setdefault("failed", 0)
    payload.setdefault("total_processed", 0)
    return payload


def save_worker_slot_summary(slot_dir, payload):
    payload = dict(payload)
    payload["updated_at"] = now_string()
    write_json(Path(slot_dir) / "slot_summary.json", payload)


def build_batch_worker_config(master_config, batch_dir, source_json_path, batch_number, total_batches, worker_index, worker_count):
    worker_name = f"worker-{worker_index:02d}"
    batch_config = json.loads(json.dumps(master_config))
    batch_config.pop("secrets_file", None)
    batch_config.pop("_meta", None)

    batch_config["files"]["source_json"] = str(source_json_path)
    batch_config["files"]["videos_dir"] = master_config["files"]["videos_dir"]
    batch_config["files"]["jsons_dir"] = master_config["files"]["jsons_dir"]
    batch_config["files"]["logs_dir"] = str(batch_dir)
    batch_config["files"]["failed_downloads"] = str(batch_dir / "failed_downloads.json")
    batch_config["files"]["log_file"] = str(batch_dir / "download.log")
    batch_config["files"]["summary_file"] = str(batch_dir / "summary.json")
    batch_config["files"]["results_file"] = str(batch_dir / "results_manifest.json")

    batch_config.setdefault("runtime", {})
    batch_config["runtime"]["worker_name"] = worker_name
    batch_config["runtime"]["worker_index"] = worker_index
    batch_config["runtime"]["worker_count"] = worker_count
    batch_config["runtime"]["batch_number"] = batch_number
    batch_config["runtime"]["batch_count"] = total_batches

    batch_config.setdefault("resume", {})
    batch_config["resume"]["state_file"] = str(batch_dir / "resume_state.json")
    batch_config.setdefault("workers", {})
    batch_config["workers"]["shared_media_dirs"] = True

    api_pool = master_config.get("workers", {}).get("api_pool") or []
    if worker_index <= len(api_pool):
        api_creds = api_pool[worker_index - 1] or {}
        if all(api_creds.get(key) for key in ("client_id", "client_secret", "token")):
            batch_config.setdefault("vimeo_api", {})
            batch_config["vimeo_api"]["client_id"] = api_creds["client_id"]
            batch_config["vimeo_api"]["secret"] = api_creds["client_secret"]
            batch_config["vimeo_api"]["token"] = api_creds["token"]
            batch_config["runtime"]["api_pool_slot"] = worker_index
        else:
            batch_config["runtime"]["api_pool_slot"] = None
    else:
        batch_config["runtime"]["api_pool_slot"] = None

    return worker_name, batch_config


def reset_running_batches_to_pending(state):
    for item in state.get("items", {}).values():
        if item.get("status") == "running":
            item["status"] = "pending"
            item["assigned_worker"] = None


def next_pending_batch(state):
    for key in sorted(state.get("items", {}), key=lambda value: int(value)):
        item = state["items"][key]
        if item.get("status") == "pending":
            return item
    return None


def build_progress_lines(state, global_results, active_processes):
    counts = global_results.get("counts", {})
    items = state.get("items", {})
    completed_batches = sum(1 for item in items.values() if item.get("status") == "completed")
    running_batches = sum(1 for item in items.values() if item.get("status") == "running")
    failed_batches = sum(1 for item in items.values() if item.get("status") == "failed")
    pending_batches = sum(1 for item in items.values() if item.get("status") == "pending")
    total_urls = int(global_results.get("total_urls", 0))
    finalized = int(counts.get("finalized", 0))
    pct = (finalized / total_urls * 100) if total_urls else 0.0
    lines = [
        f"finalized_urls: <code>{finalized}/{total_urls} ({pct:.1f}%)</code>",
        f"downloaded: <code>{int(counts.get('downloaded', 0))}</code>",
        f"skipped: <code>{int(counts.get('skipped', 0))}</code>",
        f"failed_logged: <code>{int(counts.get('failed', 0))}</code>",
        f"batches: <code>completed={completed_batches} running={running_batches} pending={pending_batches} failed={failed_batches}</code>",
    ]
    for worker_name in sorted(active_processes):
        item = active_processes[worker_name]
        lines.append(
            f"{worker_name} / batch <code>{item['batch_number']:04d}</code> / shard <code>{Path(item['source_json']).name}</code>"
        )
    return lines


def terminate_process(process, logger, label):
    try:
        process.terminate()
    except Exception:
        return

    deadline = time.time() + 10
    while time.time() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.5)

    logger.warning("Force-killing %s after terminate timeout", label)
    try:
        process.kill()
    except Exception:
        pass


def main():
    args = parse_args()
    master_config = load_config(args.config)
    assignment = normalize_assignment_manifest(args.manifest)
    selected_batches = select_batches(
        assignment,
        batch_start=args.batch_start,
        batch_end=args.batch_end,
        max_batches=args.max_batches,
    )
    if not selected_batches:
        print("No shard batches selected")
        return 0

    worker_count = args.workers or int(master_config.get("workers", {}).get("count", 1))
    worker_count = max(1, min(worker_count, len(selected_batches)))

    logs_root = Path(master_config["files"]["logs_dir"])
    workers_root = logs_root / "workers"
    shards_root = logs_root / "shards"
    worker_slots_root = workers_root
    workers_root.mkdir(parents=True, exist_ok=True)
    shards_root.mkdir(parents=True, exist_ok=True)

    logger = setup_logger(workers_root / "coordinator.log")
    logger.info("Assignment coordinator config: %s", master_config["_meta"]["config_path"])
    logger.info("Assignment manifest: %s", assignment["manifest_path"])
    logger.info("Server name: %s", assignment["server_name"])
    logger.info("Selected shard batches: %d", len(selected_batches))
    logger.info("Worker slots: %d", worker_count)

    state_path = workers_root / "assignment_state.json"
    results_path = workers_root / "aggregate_results_manifest.json"
    summary_path = workers_root / "aggregate_summary.json"

    state = load_assignment_state(state_path, assignment, selected_batches, logger)
    reset_running_batches_to_pending(state)
    save_assignment_state(state_path, state)

    global_results = load_assignment_results(results_path, assignment, selected_batches, logger)
    save_assignment_results(results_path, global_results)
    aggregate_summary = build_assignment_summary(assignment, selected_batches, state, global_results, worker_count)
    write_json(summary_path, aggregate_summary)
    exported_lists = write_result_url_lists(global_results.get("items", []), workers_root)

    telegram = TelegramNotifier(
        master_config,
        worker_name="coordinator",
        job_name=master_config["runtime"]["job_name"],
    )
    telegram.notify_custom(
        "Assignment queue start",
        [
            f"server: <code>{assignment['server_name']}</code>",
            f"workers: <code>{worker_count}</code>",
            f"selected_batches: <code>{len(selected_batches)}</code>",
            f"manifest: <code>{assignment['manifest_path']}</code>",
        ],
    )

    python_bin = sys.executable
    worker_script = PROJECT_ROOT / "download_vimeo_seleniumbase_v3.py"
    active_processes = {}
    slot_summaries = {}
    for slot_index in range(1, worker_count + 1):
        slot_name = f"worker-{slot_index:02d}"
        slot_dir = worker_slots_root / f"worker_{slot_index:02d}"
        slot_dir.mkdir(parents=True, exist_ok=True)
        slot_summaries[slot_name] = load_worker_slot_summary(slot_dir, slot_name, slot_index)
        save_worker_slot_summary(slot_dir, slot_summaries[slot_name])

    stop_requested = False
    exit_code = 0
    last_progress_ts = 0.0

    while True:
        if not stop_requested:
            for slot_index in range(1, worker_count + 1):
                slot_name = f"worker-{slot_index:02d}"
                if slot_name in active_processes:
                    continue
                batch_item = next_pending_batch(state)
                if batch_item is None:
                    break

                batch_number = int(batch_item["batch_number"])
                batch_dir = shards_root / f"batch_{batch_number:04d}"
                batch_dir.mkdir(parents=True, exist_ok=True)
                source_json_path = Path(batch_item["source_json"])
                worker_name, batch_config = build_batch_worker_config(
                    master_config,
                    batch_dir,
                    source_json_path,
                    batch_number,
                    len(selected_batches),
                    slot_index,
                    worker_count,
                )
                batch_config_path = batch_dir / "config.json"
                write_json(batch_config_path, batch_config)

                logger.info(
                    "Starting %s on batch %04d (%s)",
                    worker_name,
                    batch_number,
                    source_json_path,
                )
                process = subprocess.Popen(
                    [python_bin, str(worker_script), "--config", str(batch_config_path)],
                    cwd=str(PROJECT_ROOT),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )
                active_processes[slot_name] = {
                    "worker_name": worker_name,
                    "worker_index": slot_index,
                    "batch_number": batch_number,
                    "batch_dir": batch_dir,
                    "source_json": str(source_json_path),
                    "config_path": str(batch_config_path),
                    "process": process,
                }
                batch_item["status"] = "running"
                batch_item["assigned_worker"] = worker_name
                batch_item["started_at"] = now_string()
                batch_item["batch_dir"] = str(batch_dir)
                slot_summaries[slot_name]["active_batch_number"] = batch_number
                save_worker_slot_summary(worker_slots_root / f"worker_{slot_index:02d}", slot_summaries[slot_name])
                save_assignment_state(state_path, state)

        if not active_processes:
            pending_left = next_pending_batch(state)
            if pending_left is None:
                break
            if stop_requested:
                break

        time.sleep(5)

        for slot_name, item in list(active_processes.items()):
            process = item["process"]
            return_code = process.poll()
            if return_code is None:
                continue

            batch_number = int(item["batch_number"])
            batch_key = str(batch_number)
            batch_dir = Path(item["batch_dir"])
            batch_summary_path = batch_dir / "summary.json"
            batch_results_path = batch_dir / "results_manifest.json"
            slot_dir = worker_slots_root / f"worker_{item['worker_index']:02d}"

            logger.info(
                "%s finished batch %04d with exit code %s",
                slot_name,
                batch_number,
                return_code,
            )

            state_item = state["items"][batch_key]
            state_item["finished_at"] = now_string()
            state_item["exit_code"] = return_code
            slot_summaries[slot_name]["active_batch_number"] = None

            if return_code == 0 and batch_summary_path.exists() and batch_results_path.exists():
                batch_summary = read_json(batch_summary_path)
                batch_results = read_json(batch_results_path)
                merge_shard_results(global_results, batch_results, batch_number, slot_name)
                save_assignment_results(results_path, global_results)
                exported_lists = write_result_url_lists(global_results.get("items", []), workers_root)

                slot_summaries[slot_name]["completed_batches"] = sorted(
                    set(slot_summaries[slot_name].get("completed_batches", [])) | {batch_number}
                )
                slot_summaries[slot_name]["downloaded"] += int(batch_summary.get("downloaded", 0))
                slot_summaries[slot_name]["skipped"] += int(batch_summary.get("skipped", 0))
                slot_summaries[slot_name]["failed"] += int(batch_summary.get("failed", 0))
                slot_summaries[slot_name]["total_processed"] += int(
                    batch_summary.get("total_processed", 0)
                )
                save_worker_slot_summary(slot_dir, slot_summaries[slot_name])

                state_item["status"] = "completed"
                logger.info(
                    "Merged batch %04d results: downloaded=%d skipped=%d failed=%d",
                    batch_number,
                    int(batch_summary.get("downloaded", 0)),
                    int(batch_summary.get("skipped", 0)),
                    int(batch_summary.get("failed", 0)),
                )
            else:
                state_item["status"] = "failed"
                logger.error(
                    "Batch %04d failed or is missing output files (exit_code=%s, summary=%s, results=%s)",
                    batch_number,
                    return_code,
                    batch_summary_path.exists(),
                    batch_results_path.exists(),
                )
                exit_code = 1
                slot_summaries[slot_name]["failed"] += 1
                save_worker_slot_summary(slot_dir, slot_summaries[slot_name])
                if master_config.get("batches", {}).get("stop_on_batch_error", True):
                    stop_requested = True

            save_assignment_state(state_path, state)
            aggregate_summary = build_assignment_summary(
                assignment,
                selected_batches,
                state,
                global_results,
                worker_count,
            )
            aggregate_summary["url_list_exports"] = exported_lists
            aggregate_summary["exit_code"] = exit_code
            write_json(summary_path, aggregate_summary)

            del active_processes[slot_name]

            if stop_requested:
                for other_slot_name, other_item in list(active_processes.items()):
                    logger.warning(
                        "Stopping active %s batch %04d because stop_on_batch_error is enabled",
                        other_slot_name,
                        int(other_item["batch_number"]),
                    )
                    terminate_process(other_item["process"], logger, other_slot_name)
                    pending_state_item = state["items"][str(other_item["batch_number"])]
                    pending_state_item["status"] = "pending"
                    pending_state_item["assigned_worker"] = None
                    slot_summaries[other_slot_name]["active_batch_number"] = None
                    save_worker_slot_summary(
                        worker_slots_root / f"worker_{other_item['worker_index']:02d}",
                        slot_summaries[other_slot_name],
                    )
                    del active_processes[other_slot_name]
                save_assignment_state(state_path, state)
                break

        progress_every_seconds = telegram.notify_coordinator_progress_every_seconds
        now = time.time()
        if progress_every_seconds > 0 and now - last_progress_ts >= progress_every_seconds:
            telegram.notify_custom(
                "Assignment queue progress",
                build_progress_lines(state, global_results, active_processes),
            )
            last_progress_ts = now

    aggregate_summary = build_assignment_summary(
        assignment,
        selected_batches,
        state,
        global_results,
        worker_count,
    )
    exported_lists = write_result_url_lists(global_results.get("items", []), workers_root)
    aggregate_summary["url_list_exports"] = exported_lists
    aggregate_summary["exit_code"] = exit_code
    write_json(summary_path, aggregate_summary)
    save_assignment_state(state_path, state)
    save_assignment_results(results_path, global_results)

    logger.info("Assignment summary saved to %s", summary_path)
    for bucket, path in exported_lists.items():
        logger.info("Exported %s URLs to %s", bucket, path)

    telegram.notify_custom(
        "Assignment queue finish",
        [
            f"server: <code>{assignment['server_name']}</code>",
            f"completed_batches: <code>{aggregate_summary['completed_batches']}/{aggregate_summary['total_batches']}</code>",
            f"running_batches: <code>{aggregate_summary['running_batches']}</code>",
            f"pending_batches: <code>{aggregate_summary['pending_batches']}</code>",
            f"downloaded: <code>{aggregate_summary['downloaded']}</code>",
            f"skipped: <code>{aggregate_summary['skipped']}</code>",
            f"failed_logged: <code>{aggregate_summary['failed_logged']}</code>",
            f"summary: <code>{summary_path}</code>",
            f"results: <code>{results_path}</code>",
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

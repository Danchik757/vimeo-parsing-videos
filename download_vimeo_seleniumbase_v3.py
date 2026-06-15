"""Vimeo downloader with configurable browser mode and worker-aware logging."""

import argparse
import hashlib
import json
import logging
import math
import os
import random
import re
import signal
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from seleniumbase import SB
from selenium.webdriver.common.action_chains import ActionChains

from config_utils import load_json_config_with_optional_secrets, resolve_path
from platform_runtime import (
    find_browser_command,
    is_windows,
    load_socket_usage,
    terminate_child_process_trees_for_restart,
    supports_interface_bound_downloads,
    supports_xvfb,
)
from telegram_notifier import TelegramNotifier
from vimeo_cdp_helpers import (
    _extract_download_options_from_scope,
    choose_best_download_option,
    click_download_button,
    click_download_button_in_player_iframe,
    extract_best_api_download,
    extract_best_player_iframe_download,
    modal_looks_like_transcript,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"

MIN_DELAY_BETWEEN_VIDEOS = 3
MAX_DELAY_BETWEEN_VIDEOS = 8
PAGE_LOAD_WAIT_MIN = 2
PAGE_LOAD_WAIT_MAX = 5
READING_TIME_MIN = 1
READING_TIME_MAX = 3
CLOUDFLARE_TIMEOUT_MIN = 40
CLOUDFLARE_TIMEOUT_MAX = 60
CONTROLLED_RESTART_EXIT_CODE = 75
LOW_DISK_EXIT_CODE = 86
VIMEO_API_ROOT = "https://api.vimeo.com"
VIMEO_API_ACCEPT_HEADER = "application/vnd.vimeo.*;version=3.4"
VIMEO_API_USER_AGENT = "codex_vimeo_fix/1.0"
_PARTIAL_CLEANUP_LAST_RUN_TS = {}


def load_config(config_path):
    config, config_path, config_dir, secrets_path = load_json_config_with_optional_secrets(config_path)
    config["_meta"] = {
        "config_path": str(config_path),
        "config_dir": str(config_dir),
        "secrets_path": str(secrets_path) if secrets_path else None,
    }

    files_cfg = config.setdefault("files", {})
    for key in (
        "source_json",
        "videos_dir",
        "jsons_dir",
        "logs_dir",
        "failed_downloads",
        "log_file",
        "results_file",
    ):
        if key in files_cfg:
            files_cfg[key] = str(resolve_path(config_dir, files_cfg[key]))

    if "summary_file" in files_cfg:
        files_cfg["summary_file"] = str(resolve_path(config_dir, files_cfg["summary_file"]))
    else:
        files_cfg["summary_file"] = str(
            resolve_path(config_dir, Path(files_cfg["logs_dir"]) / "summary.json")
        )
    if "results_file" in files_cfg:
        files_cfg["results_file"] = str(resolve_path(config_dir, files_cfg["results_file"]))
    else:
        files_cfg["results_file"] = str(
            resolve_path(config_dir, Path(files_cfg["logs_dir"]) / "results_manifest.json")
        )

    settings = config.setdefault("settings", {})
    settings.setdefault("test_mode", True)
    settings.setdefault("test_limit", 50)
    settings.setdefault("retry_attempts", 3)
    settings.setdefault("retry_delay", 5)
    settings.setdefault("retry_backoff_multiplier", 2.0)
    settings.setdefault("retry_backoff_max_delay_seconds", 60)
    settings.setdefault("retry_jitter_seconds", 2)
    settings.setdefault("javascript_wait_time", 15)
    settings.setdefault("download_timeout", 600)
    settings.setdefault("connect_timeout", 30)
    settings.setdefault("download_chunk_size_kb", 1024)
    settings.setdefault("download_progress_log_seconds", 15)
    settings.setdefault("download_only_original", False)
    settings.setdefault("store_full_api_payload_for_downloaded", True)
    settings.setdefault("download_interface", "")
    settings.setdefault("download_retry_interface", "")
    settings.setdefault(
        "direct_download_timeout_seconds",
        int(settings.get("download_timeout", 600)),
    )
    settings.setdefault(
        "curl_stall_timeout_seconds",
        int(settings.get("download_timeout", 600)),
    )
    settings.setdefault("login_completion_timeout_seconds", 30)
    settings.setdefault("login_retry_attempts", 3)
    settings.setdefault("login_retry_delay_seconds", 10)
    settings.setdefault("page_load_timeout_seconds", 120)
    settings.setdefault(
        "page_open_stage_timeout_seconds",
        max(180, int(settings.get("page_load_timeout_seconds", 120) or 120) + 60),
    )
    settings.setdefault("cloudflare_stage_timeout_seconds", 180)
    settings.setdefault("video_processing_timeout_seconds", 1200)
    settings.setdefault("api_connect_timeout_seconds", min(10, int(settings.get("connect_timeout", 30) or 30)))
    settings.setdefault("api_read_timeout_seconds", 30)
    settings.setdefault("api_http_pool_connections", 1)
    settings.setdefault("api_http_pool_maxsize", 1)
    settings.setdefault("download_http_pool_connections", 2)
    settings.setdefault("download_http_pool_maxsize", 4)
    settings.setdefault("min_free_disk_gb", 0)
    settings.setdefault("min_free_disk_action", "stop")
    settings.setdefault("resume_free_disk_gb", float(settings.get("min_free_disk_gb", 0) or 0))
    settings.setdefault("disk_space_check_path", "")
    settings.setdefault("disk_space_wait_poll_seconds", 60)
    settings.setdefault("disk_space_wait_timeout_seconds", 0)
    settings.setdefault("disk_space_pause_notification_every_seconds", 900)
    settings.setdefault("partial_cleanup_enabled", True)
    settings.setdefault("part_cleanup_max_total_gb", 40)
    settings.setdefault("part_cleanup_stale_after_seconds", 21600)
    settings.setdefault("part_cleanup_check_interval_seconds", 300)

    login_cfg = config.setdefault("vimeo_login", {})
    login_cfg.setdefault("email", "")
    login_cfg.setdefault("password", "")

    browser = config.setdefault("browser", {})
    browser.setdefault("uc", True)
    browser.setdefault("headless", False)
    browser.setdefault("xvfb", False)
    browser.setdefault("disable_csp", True)
    browser.setdefault("block_images", False)
    browser.setdefault("incognito", False)
    browser.setdefault(
        "chromium_arg",
        "--disable-blink-features=AutomationControlled",
    )

    runtime = config.setdefault("runtime", {})
    runtime.setdefault("job_name", "Vimeo Downloader")
    runtime.setdefault("worker_name", "worker-01")
    runtime.setdefault("worker_index", 1)
    runtime.setdefault("worker_count", 1)
    runtime.setdefault("batch_number", 0)
    runtime.setdefault("batch_count", 0)
    runtime.setdefault("vimeo_authenticated_session", False)

    watchdog = config.setdefault("watchdog", {})
    watchdog.setdefault("enabled", True)
    watchdog.setdefault("stall_alert_after_seconds", 1800)
    watchdog.setdefault("repeat_alert_every_seconds", 1800)
    watchdog.setdefault("heartbeat_every_seconds", 900)
    watchdog.setdefault("exit_on_stall", True)
    watchdog.setdefault(
        "stall_exit_after_seconds",
        max(
            int(watchdog.get("stall_alert_after_seconds", 1800) or 1800) * 2,
            3600,
        ),
    )

    resume = config.setdefault("resume", {})
    resume.setdefault("enabled", True)
    resume.setdefault("skip_completed_files", True)
    resume.setdefault("resume_partial_downloads", True)
    if "state_file" in resume:
        resume["state_file"] = str(resolve_path(config_dir, resume["state_file"]))
    else:
        resume["state_file"] = str(Path(files_cfg["logs_dir"]) / "resume_state.json")

    workers = config.setdefault("workers", {})
    workers.setdefault("count", 1)
    workers.setdefault("stagger_start_seconds", 3)
    workers.setdefault("shared_media_dirs", False)
    workers.setdefault("socket_pressure_gate_enabled", True)
    workers.setdefault("max_tcp_inuse_to_start_worker", 320)
    workers.setdefault("max_tcp_timewait_to_start_worker", 500)
    workers.setdefault("max_tcp_orphan_to_start_worker", 130)
    workers.setdefault("socket_pressure_cooldown_seconds", 60)
    workers.setdefault("wait_for_socket_budget_before_network", True)
    workers.setdefault("socket_pressure_wait_timeout_seconds", 180)
    workers.setdefault("socket_pressure_poll_seconds", 5)
    workers.setdefault("socket_pressure_wait_timeout_action", "restart")
    workers.setdefault("restart_after_processed", 100)
    workers.setdefault("consecutive_timeout_failures_before_restart", 3)

    batches = config.setdefault("batches", {})
    batches.setdefault("enabled", False)
    batches.setdefault("batch_size", 10000)
    batches.setdefault("auto_advance", True)
    batches.setdefault("reuse_existing_shards", True)
    batches.setdefault("stop_on_batch_error", True)
    batches.setdefault("max_batch_retries", 0)
    batches.setdefault("max_controlled_restarts", 0)
    batches.setdefault("max_batches", 0)
    batches.setdefault("shards_dir", "data_shards/generated_batches")
    batches.setdefault("runs_dir", "output/batches/runs")
    batches.setdefault("state_file", "output/batches/batch_state.json")
    batches.setdefault("manifest_file", "output/batches/batches_manifest.json")
    batches.setdefault("global_results_file", "output/batches/global_results_manifest.json")
    batches.setdefault("global_downloaded_file", "output/batches/global_downloaded_videos.json")
    batches.setdefault("global_summary_file", "output/batches/global_summary.json")

    for key in (
        "shards_dir",
        "runs_dir",
        "state_file",
        "manifest_file",
        "global_results_file",
        "global_downloaded_file",
        "global_summary_file",
    ):
        if key in batches:
            batches[key] = str(resolve_path(config_dir, batches[key]))

    offload = config.setdefault("offload", {})
    offload.setdefault("enabled", False)
    offload.setdefault("storage_root", "")
    offload.setdefault("registry_file", str(Path(files_cfg["logs_dir"]) / "offload_registry.json"))
    offload.setdefault("log_file", str(Path(files_cfg["logs_dir"]) / "offload.log"))
    offload.setdefault("scan_interval_seconds", 600)
    offload.setdefault("min_file_age_seconds", 30)
    offload.setdefault("delete_local_video_after_upload", False)
    offload.setdefault("validate_with_ffprobe", True)
    offload.setdefault("ffprobe_bin", "ffprobe")

    for key in ("storage_root", "registry_file", "log_file"):
        if offload.get(key):
            offload[key] = str(resolve_path(config_dir, offload[key]))

    return config


def setup_logger(log_file):
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("vimeo_downloader")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


class RuntimeState:
    """Shared worker state for watchdog and logging."""

    def __init__(self, worker_name, total_videos):
        self.worker_name = worker_name
        self.total_videos = total_videos
        self.current_video_id = None
        self.current_stage = "initializing"
        self.processed = 0
        self.downloaded = 0
        self.skipped = 0
        self.failed = 0
        self.last_activity_ts = time.time()
        self.last_heartbeat_ts = 0.0
        self.last_stall_alert_ts = 0.0
        self.lock = threading.Lock()

    def touch(self, stage, video_id=None):
        with self.lock:
            self.current_stage = stage
            if video_id is not None:
                self.current_video_id = video_id
            self.last_activity_ts = time.time()
            self.last_stall_alert_ts = 0.0

    def update_counts(self, processed, downloaded, skipped, failed):
        with self.lock:
            self.processed = processed
            self.downloaded = downloaded
            self.skipped = skipped
            self.failed = failed
            self.last_activity_ts = time.time()
            self.last_stall_alert_ts = 0.0

    def mark_heartbeat(self):
        with self.lock:
            self.last_heartbeat_ts = time.time()

    def mark_stall_alert(self):
        with self.lock:
            self.last_stall_alert_ts = time.time()

    def snapshot(self):
        with self.lock:
            return {
                "worker_name": self.worker_name,
                "total_videos": self.total_videos,
                "current_video_id": self.current_video_id,
                "current_stage": self.current_stage,
                "processed": self.processed,
                "downloaded": self.downloaded,
                "skipped": self.skipped,
                "failed": self.failed,
                "last_activity_ts": self.last_activity_ts,
                "last_heartbeat_ts": self.last_heartbeat_ts,
                "last_stall_alert_ts": self.last_stall_alert_ts,
            }


class ActivityWatchdog(threading.Thread):
    """Send heartbeat and stall alerts when the worker is quiet for too long."""

    def __init__(self, runtime_state, telegram, logger, config):
        super().__init__(daemon=True)
        watchdog_cfg = config["watchdog"]
        self.runtime_state = runtime_state
        self.telegram = telegram
        self.logger = logger
        self.config = config
        self.enabled = bool(watchdog_cfg.get("enabled", True))
        self.stall_after = int(watchdog_cfg.get("stall_alert_after_seconds", 1800))
        self.repeat_every = int(watchdog_cfg.get("repeat_alert_every_seconds", 1800))
        self.heartbeat_every = int(watchdog_cfg.get("heartbeat_every_seconds", 900))
        self.exit_on_stall = bool(watchdog_cfg.get("exit_on_stall", True))
        self.stall_exit_after = int(watchdog_cfg.get("stall_exit_after_seconds", 3600))
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        if not self.enabled:
            return

        while not self.stop_event.wait(5):
            snapshot = self.runtime_state.snapshot()
            now = time.time()

            if self.heartbeat_every > 0 and (
                now - snapshot["last_heartbeat_ts"] >= self.heartbeat_every
            ):
                self.telegram.notify_heartbeat(
                    snapshot["processed"],
                    snapshot["total_videos"],
                    snapshot["downloaded"],
                    snapshot["skipped"],
                    snapshot["failed"],
                    snapshot["current_stage"],
                    snapshot["current_video_id"],
                )
                self.runtime_state.mark_heartbeat()

            if self.stall_after <= 0:
                continue

            idle_for = now - snapshot["last_activity_ts"]
            if idle_for < self.stall_after:
                continue

            if snapshot["last_stall_alert_ts"] and (
                now - snapshot["last_stall_alert_ts"] < self.repeat_every
            ):
                continue

            self.logger.warning(
                "Watchdog detected inactivity for %.0fs at stage '%s' (video=%s)",
                idle_for,
                snapshot["current_stage"],
                snapshot["current_video_id"],
            )
            self.telegram.notify_stall(
                snapshot["current_video_id"],
                snapshot["current_stage"],
                idle_for,
                snapshot["processed"],
                snapshot["total_videos"],
                snapshot["downloaded"],
                snapshot["skipped"],
                snapshot["failed"],
            )
            self.runtime_state.mark_stall_alert()

            if self.exit_on_stall and self.stall_exit_after > 0 and idle_for >= self.stall_exit_after:
                self.logger.error(
                    "Watchdog terminating worker after %.0fs idle at stage '%s' (video=%s)",
                    idle_for,
                    snapshot["current_stage"],
                    snapshot["current_video_id"],
                )
                self.telegram.notify_custom(
                    "stall recovery",
                    [
                        f"video_id: <code>{snapshot['current_video_id'] or 'unknown'}</code>",
                        f"stage: <code>{snapshot['current_stage'] or 'unknown'}</code>",
                        f"idle_seconds: <code>{int(idle_for)}</code>",
                        "action: <code>terminating worker for queue retry</code>",
                    ],
                )
                self.telegram.shutdown(timeout=5)
                try:
                    write_watchdog_restart_summary(self.config, snapshot, idle_for)
                except Exception as exc:
                    self.logger.warning("Failed to write watchdog restart summary: %s", exc)
                terminate_child_process_trees_for_restart(
                    logger=self.logger,
                    label=f"worker-stall-{snapshot['worker_name'] or 'unknown'}",
                )
                os._exit(CONTROLLED_RESTART_EXIT_CODE)


class StageTimeoutError(TimeoutError):
    """Raised when a browser interaction exceeds its hard timeout."""


class ControlledWorkerRestart(RuntimeError):
    """Raised when a worker should exit cleanly and resume in a fresh process."""


class LowDiskSpaceError(RuntimeError):
    """Raised when free disk space falls below the configured minimum."""

    def __init__(self, message, snapshot=None):
        super().__init__(message)
        self.snapshot = snapshot or {}


def get_disk_space_snapshot(path_value):
    candidate = Path(path_value or ".")
    if candidate.exists() and candidate.is_file():
        candidate = candidate.parent
    if not candidate.exists():
        candidate = candidate.parent if candidate.parent != candidate else Path.cwd()
    if not candidate.exists():
        candidate = Path.cwd()

    usage = shutil.disk_usage(candidate)
    return {
        "path": str(candidate),
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
        "total_gb": round(usage.total / (1024 ** 3), 2),
        "used_gb": round(usage.used / (1024 ** 3), 2),
        "free_gb": round(usage.free / (1024 ** 3), 2),
    }


def collect_partial_download_entries(videos_dir, current_video_id=None):
    downloaded_root = Path(videos_dir) / "downloaded"
    if not downloaded_root.exists():
        return []

    normalized_current_video_id = (
        str(current_video_id).strip() if current_video_id is not None else None
    )
    now_ts = time.time()
    entries = []
    for part_path in sorted(downloaded_root.glob("*/*.part")):
        try:
            stat = part_path.stat()
        except OSError:
            continue

        video_dir = part_path.parent
        video_id = video_dir.name
        entries.append(
            {
                "video_id": video_id,
                "path": str(part_path),
                "dir_path": str(video_dir),
                "size_bytes": int(stat.st_size),
                "mtime_ts": float(stat.st_mtime),
                "age_seconds": max(0, int(now_ts - stat.st_mtime)),
                "protected": bool(
                    normalized_current_video_id
                    and str(video_id).strip() == normalized_current_video_id
                ),
            }
        )
    return entries


def _try_remove_empty_parent_dir(path_value):
    try:
        path = Path(path_value)
        if path.exists() and path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        return


def cleanup_partial_download_cache(
    config,
    logger=None,
    telegram=None,
    runtime_state=None,
    current_video_id=None,
    reason="runtime",
    force=False,
):
    settings = config["settings"]
    if not bool(settings.get("partial_cleanup_enabled", True)):
        return None

    videos_dir = config["files"].get("videos_dir")
    if not videos_dir:
        return None

    check_interval_seconds = max(
        0,
        int(settings.get("part_cleanup_check_interval_seconds", 300) or 300),
    )
    cache_key = str(Path(videos_dir).resolve())
    now_ts = time.time()
    last_run_ts = float(_PARTIAL_CLEANUP_LAST_RUN_TS.get(cache_key, 0.0) or 0.0)
    if not force and check_interval_seconds > 0 and now_ts - last_run_ts < check_interval_seconds:
        return None
    _PARTIAL_CLEANUP_LAST_RUN_TS[cache_key] = now_ts

    stale_after_seconds = max(
        0,
        int(settings.get("part_cleanup_stale_after_seconds", 21600) or 21600),
    )
    max_total_gb = float(settings.get("part_cleanup_max_total_gb", 40) or 0)
    max_total_bytes = int(max_total_gb * (1024 ** 3)) if max_total_gb > 0 else 0

    entries = collect_partial_download_entries(videos_dir, current_video_id=current_video_id)
    initial_total_bytes = sum(int(entry["size_bytes"]) for entry in entries)
    remaining_total_bytes = initial_total_bytes
    deleted_bytes = 0
    deleted_entries = []

    def delete_entry(entry, delete_reason):
        nonlocal remaining_total_bytes, deleted_bytes
        path = Path(entry["path"])
        if not path.exists():
            return False
        size_bytes = int(entry["size_bytes"])
        try:
            path.unlink()
        except OSError:
            return False
        remaining_total_bytes = max(0, remaining_total_bytes - size_bytes)
        deleted_bytes += size_bytes
        deleted_entries.append(
            {
                "video_id": entry["video_id"],
                "path": entry["path"],
                "size_bytes": size_bytes,
                "reason": delete_reason,
                "age_seconds": entry["age_seconds"],
            }
        )
        _try_remove_empty_parent_dir(entry["dir_path"])
        return True

    if runtime_state is not None and entries:
        runtime_state.touch("cleaning stale partial downloads", current_video_id)

    for entry in sorted(entries, key=lambda item: item["mtime_ts"]):
        if entry["protected"]:
            continue
        if stale_after_seconds > 0 and entry["age_seconds"] >= stale_after_seconds:
            delete_entry(entry, "stale")

    if max_total_bytes > 0 and remaining_total_bytes > max_total_bytes:
        survivors = [
            entry
            for entry in sorted(entries, key=lambda item: item["mtime_ts"])
            if Path(entry["path"]).exists() and not entry["protected"]
        ]
        for entry in survivors:
            if remaining_total_bytes <= max_total_bytes:
                break
            delete_entry(entry, "over_limit")

    remaining_entries = collect_partial_download_entries(videos_dir, current_video_id=current_video_id)
    result = {
        "reason": reason,
        "initial_count": len(entries),
        "initial_total_bytes": initial_total_bytes,
        "initial_total_gb": round(initial_total_bytes / (1024 ** 3), 3),
        "remaining_count": len(remaining_entries),
        "remaining_total_bytes": sum(int(entry["size_bytes"]) for entry in remaining_entries),
        "remaining_total_gb": round(
            sum(int(entry["size_bytes"]) for entry in remaining_entries) / (1024 ** 3),
            3,
        ),
        "deleted_count": len(deleted_entries),
        "deleted_bytes": deleted_bytes,
        "deleted_gb": round(deleted_bytes / (1024 ** 3), 3),
        "deleted_stale_count": sum(1 for entry in deleted_entries if entry["reason"] == "stale"),
        "deleted_over_limit_count": sum(1 for entry in deleted_entries if entry["reason"] == "over_limit"),
        "max_total_gb": max_total_gb,
        "stale_after_seconds": stale_after_seconds,
        "protected_video_id": current_video_id,
    }

    if deleted_entries:
        if logger is not None:
            logger.warning(
                "Partial download cleanup during %s: deleted=%d files %.2f GB (stale=%d over_limit=%d), remaining=%.2f GB",
                reason,
                result["deleted_count"],
                result["deleted_gb"],
                result["deleted_stale_count"],
                result["deleted_over_limit_count"],
                result["remaining_total_gb"],
            )
        if telegram is not None:
            lines = [
                f"reason: <code>{reason}</code>",
                f"deleted_part_files: <code>{result['deleted_count']}</code>",
                f"deleted_part_gb: <code>{result['deleted_gb']}</code>",
                f"stale_deleted: <code>{result['deleted_stale_count']}</code>",
                f"over_limit_deleted: <code>{result['deleted_over_limit_count']}</code>",
                f"remaining_part_gb: <code>{result['remaining_total_gb']}</code>",
                f"part_limit_gb: <code>{result['max_total_gb']}</code>",
                f"protected_video_id: <code>{current_video_id or '-'}</code>",
            ]
            preview_entries = deleted_entries[:5]
            if preview_entries:
                preview_text = ", ".join(
                    f"{entry['video_id']}({round(entry['size_bytes'] / (1024 ** 3), 2)} GB/{entry['reason']})"
                    for entry in preview_entries
                )
                lines.append(f"deleted_preview: <code>{preview_text}</code>")
            telegram.notify_custom("Очистка partial downloads", lines)

    return result


def ensure_min_free_disk_space(
    config,
    stage="runtime",
    current_video_id=None,
    logger=None,
    telegram=None,
    runtime_state=None,
):
    threshold_gb = float(config["settings"].get("min_free_disk_gb", 0) or 0)
    if threshold_gb <= 0:
        return None

    action = str(config["settings"].get("min_free_disk_action", "stop") or "stop").strip().lower()
    resume_threshold_gb = float(
        config["settings"].get("resume_free_disk_gb", threshold_gb) or threshold_gb
    )
    resume_threshold_gb = max(threshold_gb, resume_threshold_gb)
    wait_poll_seconds = max(
        5,
        int(config["settings"].get("disk_space_wait_poll_seconds", 60) or 60),
    )
    wait_timeout_seconds = max(
        0,
        int(config["settings"].get("disk_space_wait_timeout_seconds", 0) or 0),
    )
    pause_notify_every_seconds = max(
        0,
        int(
            config["settings"].get(
                "disk_space_pause_notification_every_seconds",
                900,
            )
            or 900
        ),
    )

    check_path = (
        config["settings"].get("disk_space_check_path")
        or config["files"].get("videos_dir")
        or config["files"].get("logs_dir")
        or "."
    )
    snapshot = get_disk_space_snapshot(check_path)
    cleanup_result = cleanup_partial_download_cache(
        config,
        logger=logger,
        telegram=telegram,
        runtime_state=runtime_state,
        current_video_id=current_video_id,
        reason=stage,
    )
    if cleanup_result and cleanup_result.get("deleted_bytes", 0) > 0:
        snapshot = get_disk_space_snapshot(check_path)
    snapshot["threshold_gb"] = threshold_gb
    snapshot["stage"] = stage
    snapshot["video_id"] = current_video_id
    if float(snapshot["free_gb"]) >= threshold_gb:
        return snapshot

    if action == "wait":
        wait_started_ts = time.time()
        last_notify_ts = 0.0
        last_log_ts = 0.0
        recovery_notified = False
        while True:
            now = time.time()
            waited_seconds = int(now - wait_started_ts)
            if runtime_state is not None:
                runtime_state.touch("waiting for disk space", current_video_id)
            cleanup_result = cleanup_partial_download_cache(
                config,
                logger=logger,
                telegram=telegram,
                runtime_state=runtime_state,
                current_video_id=current_video_id,
                reason=f"{stage} wait",
            )
            if cleanup_result and cleanup_result.get("deleted_bytes", 0) > 0:
                snapshot = get_disk_space_snapshot(check_path)
                snapshot["threshold_gb"] = threshold_gb
                snapshot["resume_threshold_gb"] = resume_threshold_gb
                snapshot["stage"] = stage
                snapshot["video_id"] = current_video_id
                snapshot["waited_seconds"] = waited_seconds
            if (
                telegram is not None
                and (
                    last_notify_ts == 0.0
                    or pause_notify_every_seconds <= 0
                    or now - last_notify_ts >= pause_notify_every_seconds
                )
            ):
                telegram.notify_custom(
                    "Ожидание освобождения места",
                    [
                        f"stage: <code>{stage}</code>",
                        f"video_id: <code>{current_video_id or '-'}</code>",
                        f"free_gb: <code>{snapshot['free_gb']}</code>",
                        f"threshold_gb: <code>{threshold_gb}</code>",
                        f"resume_gb: <code>{resume_threshold_gb}</code>",
                        f"waited_seconds: <code>{waited_seconds}</code>",
                        f"check_path: <code>{snapshot['path']}</code>",
                        "action: <code>paused, waiting for offload/free space recovery</code>",
                    ],
                )
                last_notify_ts = now
            if logger is not None and (
                last_log_ts == 0.0
                or pause_notify_every_seconds <= 0
                or now - last_log_ts >= pause_notify_every_seconds
            ):
                logger.warning(
                    "Waiting for free disk space recovery during %s: free=%.2f GB threshold=%.2f GB resume=%.2f GB path=%s video=%s waited=%ss",
                    stage,
                    float(snapshot["free_gb"]),
                    threshold_gb,
                    resume_threshold_gb,
                    snapshot["path"],
                    current_video_id,
                    waited_seconds,
                )
                last_log_ts = now

            if wait_timeout_seconds > 0 and waited_seconds >= wait_timeout_seconds:
                snapshot["waited_seconds"] = waited_seconds
                break

            time.sleep(wait_poll_seconds)
            snapshot = get_disk_space_snapshot(check_path)
            snapshot["threshold_gb"] = threshold_gb
            snapshot["resume_threshold_gb"] = resume_threshold_gb
            snapshot["stage"] = stage
            snapshot["video_id"] = current_video_id
            snapshot["waited_seconds"] = waited_seconds
            if float(snapshot["free_gb"]) >= resume_threshold_gb:
                if telegram is not None and not recovery_notified:
                    telegram.notify_custom(
                        "Место освобождено",
                        [
                            f"stage: <code>{stage}</code>",
                            f"video_id: <code>{current_video_id or '-'}</code>",
                            f"free_gb: <code>{snapshot['free_gb']}</code>",
                            f"resume_gb: <code>{resume_threshold_gb}</code>",
                            f"waited_seconds: <code>{waited_seconds}</code>",
                            f"check_path: <code>{snapshot['path']}</code>",
                            "action: <code>resuming batch processing</code>",
                        ],
                    )
                    recovery_notified = True
                if logger is not None:
                    logger.info(
                        "Free disk space recovered during %s: free=%.2f GB resume=%.2f GB path=%s video=%s waited=%ss",
                        stage,
                        float(snapshot["free_gb"]),
                        resume_threshold_gb,
                        snapshot["path"],
                        current_video_id,
                        waited_seconds,
                    )
                return snapshot

    video_suffix = f", video_id={current_video_id}" if current_video_id else ""
    raise LowDiskSpaceError(
        (
            f"Free disk space dropped below threshold during {stage}{video_suffix}: "
            f"free={snapshot['free_gb']:.2f} GB threshold={threshold_gb:.2f} GB path={snapshot['path']}"
        ),
        snapshot=snapshot,
    )


def write_watchdog_restart_summary(config, snapshot, idle_for):
    processed = int(snapshot.get("processed", 0) or 0)
    summary = {
        "worker_name": snapshot.get("worker_name"),
        "config_path": config["_meta"]["config_path"],
        "browser_mode": browser_mode_label(config),
        "total": processed,
        "total_processed": processed,
        "downloaded": int(snapshot.get("downloaded", 0) or 0),
        "skipped": int(snapshot.get("skipped", 0) or 0),
        "failed": int(snapshot.get("failed", 0) or 0),
        "failed_urls": 0,
        "exit_code": CONTROLLED_RESTART_EXIT_CODE,
        "fatal_error": None,
        "restart_requested": True,
        "restart_reason": (
            f"watchdog stall restart after {int(idle_for)}s idle "
            f"at stage '{snapshot.get('current_stage') or 'unknown'}'"
        ),
        "log_file": config["files"]["log_file"],
        "videos_dir": config["files"]["videos_dir"],
        "jsons_dir": config["files"]["jsons_dir"],
        "failed_downloads": config["files"]["failed_downloads"],
        "results_file": config["files"]["results_file"],
        "storage_layout": {
            "downloaded": "per_video_directory",
            "not_downloaded": "flat_json",
            "no_links": "flat_json",
        },
        "resume_enabled": config["resume"]["enabled"],
        "resume_state_file": config["resume"]["state_file"],
        "resume_next_index": processed,
        "resume_completed": False,
        "resume_source_signature": None,
    }
    write_summary(config, summary)


def call_with_stage_timeout(timeout_seconds, description, func, *args, **kwargs):
    timeout_seconds = int(timeout_seconds or 0)
    if (
        timeout_seconds <= 0
        or threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "setitimer")
        or is_windows()
    ):
        return func(*args, **kwargs)

    def _handle_timeout(_signum, _frame):
        raise StageTimeoutError(f"{description} exceeded {timeout_seconds}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, _handle_timeout)
        signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))
        return func(*args, **kwargs)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def build_video_deadline(settings):
    timeout_seconds = int(settings.get("video_processing_timeout_seconds", 0) or 0)
    if timeout_seconds <= 0:
        return None
    return time.monotonic() + timeout_seconds


def remaining_deadline_seconds(deadline_ts):
    if deadline_ts is None:
        return None
    return deadline_ts - time.monotonic()


def ensure_deadline_not_exceeded(deadline_ts, description):
    remaining = remaining_deadline_seconds(deadline_ts)
    if remaining is None:
        return
    if remaining <= 0:
        raise StageTimeoutError(f"{description} exceeded overall video timeout")


def resolve_stage_timeout(timeout_seconds, deadline_ts):
    timeout_seconds = int(timeout_seconds or 0)
    if timeout_seconds <= 0 or deadline_ts is None:
        return timeout_seconds
    remaining = remaining_deadline_seconds(deadline_ts)
    if remaining is None:
        return timeout_seconds
    return max(1, min(timeout_seconds, int(math.ceil(remaining))))


def clamp_sleep_seconds(seconds, deadline_ts, description):
    seconds = float(seconds or 0)
    if deadline_ts is None:
        return seconds
    remaining = remaining_deadline_seconds(deadline_ts)
    if remaining is None or remaining <= 0:
        raise StageTimeoutError(f"{description} exceeded overall video timeout")
    return min(seconds, remaining)


def terminate_subprocess_quietly(process, timeout_seconds=5):
    try:
        process.terminate()
    except Exception:
        return

    deadline = time.time() + max(1, int(timeout_seconds))
    while time.time() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.2)

    try:
        process.kill()
    except Exception:
        pass


def is_timeout_like_message(message):
    text = str(message or "").lower()
    if not text:
        return False
    markers = (
        "timed out",
        "timeout",
        "time-out",
        "read timed out",
        "connecttimeout",
        "connection timed out",
        "stall timeout",
        "overall video timeout",
    )
    if any(marker in text for marker in markers):
        return True
    return re.search(r"\bexceeded\s+\d+s\b", text) is not None


def maybe_raise_controlled_restart(
    config,
    start_index,
    current_index,
    total_urls,
    consecutive_timeout_failures,
    logger,
    video_id,
):
    if current_index >= total_urls:
        return

    worker_cfg = config.get("workers", {})
    restart_after_processed = int(worker_cfg.get("restart_after_processed", 0) or 0)
    if restart_after_processed > 0:
        processed_in_this_worker = max(0, int(current_index) - int(start_index))
        if processed_in_this_worker >= restart_after_processed:
            reason = (
                f"processed {processed_in_this_worker} URLs in this worker process; "
                f"restarting to refresh browser/network state at video {video_id}"
            )
            logger.warning("Controlled worker restart requested: %s", reason)
            raise ControlledWorkerRestart(reason)

    timeout_restart_threshold = int(
        worker_cfg.get("consecutive_timeout_failures_before_restart", 0) or 0
    )
    if timeout_restart_threshold > 0 and consecutive_timeout_failures >= timeout_restart_threshold:
        reason = (
            f"{consecutive_timeout_failures} consecutive timeout-like failures; "
            f"restarting worker before retrying at video {video_id}"
        )
        logger.warning("Controlled worker restart requested: %s", reason)
        raise ControlledWorkerRestart(reason)


def simulate_mouse_movement(sb, logger):
    try:
        actions = ActionChains(sb.driver)
        num_movements = random.randint(2, 4)
        for _ in range(num_movements):
            x_offset = random.randint(-200, 200)
            y_offset = random.randint(-200, 200)
            actions.move_by_offset(x_offset, y_offset)
            actions.pause(random.uniform(0.1, 0.3))
        actions.perform()
        actions.reset_actions()
    except Exception as exc:
        logger.debug("Mouse movement error (not critical): %s", exc)


def simulate_page_scroll(sb, logger):
    try:
        scroll_amount = random.randint(300, 600)
        sb.execute_script(f"window.scrollBy(0, {scroll_amount});")
        sb.sleep(random.uniform(0.3, 0.7))
        scroll_back = random.randint(100, 300)
        sb.execute_script(f"window.scrollBy(0, -{scroll_back});")
        sb.sleep(random.uniform(0.2, 0.5))
    except Exception as exc:
        logger.debug("Page scroll error (not critical): %s", exc)


def check_if_cloudflare_blocked(sb, logger):
    try:
        page_source = sb.get_page_source().lower()
        page_title = sb.get_title().lower()

        if " on vimeo" in page_title:
            return False

        cloudflare_indicators = [
            "cloudflare turnstile",
            "verify you are human",
            "verify to continue",
            "challenge-platform",
            "just a moment",
            "cf-challenge",
        ]

        for indicator in cloudflare_indicators:
            if indicator in page_source:
                return True

        return False
    except Exception as exc:
        logger.debug("Error checking Cloudflare: %s", exc)
        return False


def require_login_credentials(config):
    login_cfg = config.get("vimeo_login", {})
    email = (os.environ.get("VIMEO_EMAIL") or login_cfg.get("email") or "").strip()
    password = os.environ.get("VIMEO_PASSWORD") or login_cfg.get("password") or ""
    if not email or not password:
        raise RuntimeError(
            "Vimeo login credentials are required when runtime.vimeo_authenticated_session=true. "
            "Set VIMEO_EMAIL/VIMEO_PASSWORD or fill vimeo_login.email/password in config/secrets."
        )
    return email, password


def is_login_page(current_url, title, page_context=None):
    haystack = f"{current_url} {title}".lower()
    if "log_in" in haystack or "login" in haystack:
        return True
    viewer_bootstrap = (page_context or {}).get("viewer_bootstrap") or {}
    if viewer_bootstrap.get("logged_in") is False:
        return True
    return False


def save_login_failure_artifact(sb, logger, config, attempt):
    logs_dir = Path(config["files"]["logs_dir"])
    logs_dir.mkdir(parents=True, exist_ok=True)
    html_path = logs_dir / f"login_failure_attempt_{attempt}.html"
    try:
        html_path.write_text(sb.get_page_source(), encoding="utf-8")
        logger.warning("Saved login failure HTML to %s", html_path)
    except Exception as exc:
        logger.warning("Failed to save login failure HTML: %s", exc)


def login_to_vimeo(sb, email, password, logger, config):
    settings = config["settings"]
    login_completion_timeout = int(settings.get("login_completion_timeout_seconds", 30))
    login_retry_attempts = int(settings.get("login_retry_attempts", 3))
    login_retry_delay_seconds = int(settings.get("login_retry_delay_seconds", 10))

    email_selectors = [
        "#email_login",
        "input[data-testid='site_login_email_input']",
        "input[name='email']",
        "input[type='email']",
        "#email",
    ]
    password_selectors = [
        "#password_login",
        "input[data-testid='site_login_password_input']",
        "input[name='password']",
        "input[type='password']",
        "#password",
    ]
    submit_selectors = [
        "button[data-testid='site_login_submit_button']",
        "button[type='submit']",
        "button[aria-label*='Log in']",
    ]

    for attempt in range(1, login_retry_attempts + 1):
        logger.info("Opening Vimeo login page (attempt %d/%d)", attempt, login_retry_attempts)
        sb.open("https://vimeo.com/log_in")
        sb.sleep(3)

        for selector in email_selectors:
            try:
                sb.wait_for_element_visible(selector, timeout=10)
                sb.clear(selector)
                sb.type(selector, email)
                break
            except Exception:
                continue
        else:
            raise RuntimeError("Email input not found on Vimeo login page")

        for selector in password_selectors:
            try:
                sb.clear(selector)
                sb.type(selector, password)
                break
            except Exception:
                continue
        else:
            raise RuntimeError("Password input not found on Vimeo login page")

        for selector in submit_selectors:
            try:
                sb.click(selector)
                break
            except Exception:
                continue
        else:
            raise RuntimeError("Login submit button not found on Vimeo login page")

        logger.info(
            "Waiting up to %ss for login to complete (attempt %d/%d)",
            login_completion_timeout,
            attempt,
            login_retry_attempts,
        )
        deadline = time.time() + login_completion_timeout
        while time.time() < deadline:
            current_url = ""
            page_title = ""
            try:
                current_url = sb.get_current_url()
            except Exception:
                pass
            try:
                page_title = sb.get_title()
            except Exception:
                pass

            if not is_login_page(current_url, page_title):
                logger.info("Logged in successfully")
                return
            time.sleep(2)

        save_login_failure_artifact(sb, logger, config, attempt)
        current_url = ""
        try:
            current_url = sb.get_current_url()
        except Exception:
            pass

        if attempt < login_retry_attempts:
            logger.warning(
                "Login attempt %d/%d incomplete, still on %s. Retrying in %ss",
                attempt,
                login_retry_attempts,
                current_url or "unknown url",
                login_retry_delay_seconds,
            )
            sb.sleep(login_retry_delay_seconds)
            continue

        raise RuntimeError(f"Login appears incomplete, still on {current_url}")


def parse_content_range_total(header_value):
    if not header_value or "/" not in header_value:
        return None
    total_part = header_value.rsplit("/", 1)[-1].strip()
    if not total_part.isdigit():
        return None
    return int(total_part)


def coerce_numeric_video_id(value):
    if value is None:
        return None

    candidate = str(value).strip()
    if not candidate:
        return None

    parsed = urlparse(candidate)
    path = parsed.path.rstrip("/")
    if path:
        segments = [segment.strip() for segment in path.split("/") if segment.strip()]
        for segment in reversed(segments):
            if segment.isdigit():
                return segment
        if segments:
            candidate = segments[-1]

    return candidate if candidate.isdigit() else None


def extract_video_identifier(value, field_name="video_id"):
    if value is None:
        raise ValueError(f"{field_name} is required")

    candidate = str(value).strip()
    if not candidate:
        raise ValueError(f"{field_name} is required")

    parsed = urlparse(candidate)
    path = parsed.path.rstrip("/")
    if path:
        segments = [segment.strip() for segment in path.split("/") if segment.strip()]
        if segments:
            return segments[-1]

    candidate = candidate.rstrip("/").split("/")[-1].strip()
    if not candidate:
        raise ValueError(f"{field_name} is required")
    return candidate


def normalize_video_storage_key(value, field_name="video_id"):
    normalized_numeric = coerce_numeric_video_id(value)
    if normalized_numeric is not None:
        return normalized_numeric

    identifier = extract_video_identifier(value, field_name=field_name)
    safe_value = re.sub(r"[^A-Za-z0-9._-]+", "_", identifier).strip("._")
    if not safe_value or safe_value in {".", ".."}:
        raise ValueError(f"{field_name} must resolve to a safe storage key: {value!r}")
    return safe_value


def extract_video_id(video_url):
    return extract_video_identifier(video_url, field_name="video_url")


def current_network_label(config):
    proxy_cfg = config.get("proxy", {})
    if proxy_cfg.get("enabled") and proxy_cfg.get("host"):
        port = proxy_cfg.get("port")
        if port:
            return f"proxy:{proxy_cfg['host']}:{port}"
        return f"proxy:{proxy_cfg['host']}"
    return "direct_connection"


def is_probable_ip_block(error_message):
    message = str(error_message or "").lower()
    indicators = [
        "cloudflare turnstile not bypassed",
        "api rate limit exceeded",
        "rate limit exceeded",
    ]
    return any(indicator in message for indicator in indicators)


def close_response_quietly(response):
    if response is None:
        return
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def close_session_quietly(session):
    if session is None:
        return
    close = getattr(session, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def build_download_http_session(config):
    settings = config["settings"]
    pool_connections = max(1, int(settings.get("download_http_pool_connections", 2) or 2))
    pool_maxsize = max(1, int(settings.get("download_http_pool_maxsize", 4) or 4))
    return build_bounded_http_session(
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
    )


def build_bounded_http_session(pool_connections, pool_maxsize, headers=None):
    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
        max_retries=0,
        pool_block=True,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if headers:
        session.headers.update(headers)
    return session


def build_vimeo_api_http_session(config):
    settings = config["settings"]
    vimeo_cfg = config["vimeo_api"]
    pool_connections = max(1, int(settings.get("api_http_pool_connections", 1) or 1))
    pool_maxsize = max(1, int(settings.get("api_http_pool_maxsize", 1) or 1))
    headers = {
        "Accept": VIMEO_API_ACCEPT_HEADER,
        "User-Agent": VIMEO_API_USER_AGENT,
        "Authorization": f"bearer {vimeo_cfg['token']}",
    }
    return build_bounded_http_session(
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
        headers=headers,
    )


def get_vimeo_api_video(api_session, video_id, config):
    settings = config["settings"]
    connect_timeout = max(
        1,
        int(settings.get("api_connect_timeout_seconds", settings.get("connect_timeout", 30)) or 30),
    )
    read_timeout = max(1, int(settings.get("api_read_timeout_seconds", 30) or 30))
    return api_session.get(
        f"{VIMEO_API_ROOT}/videos/{video_id}",
        timeout=(connect_timeout, read_timeout),
        allow_redirects=True,
    )


def compute_retry_delay(settings, attempt_number):
    base_delay = max(0.0, float(settings.get("retry_delay", 5) or 0))
    multiplier = max(1.0, float(settings.get("retry_backoff_multiplier", 2.0) or 1.0))
    max_delay = max(base_delay, float(settings.get("retry_backoff_max_delay_seconds", 60) or base_delay))
    jitter = max(0.0, float(settings.get("retry_jitter_seconds", 2) or 0))
    exponent = max(0, int(attempt_number) - 1)
    delay = min(max_delay, base_delay * (multiplier ** exponent))
    if jitter > 0:
        delay += random.uniform(0.0, jitter)
    return delay


def evaluate_socket_pressure(config, logger=None):
    workers_cfg = config.get("workers", {})
    if not bool(workers_cfg.get("socket_pressure_gate_enabled", True)):
        return None

    socket_usage = load_socket_usage(logger=logger)
    if socket_usage is None:
        return None

    checks = (
        ("tcp_inuse", "max_tcp_inuse_to_start_worker"),
        ("tcp_timewait", "max_tcp_timewait_to_start_worker"),
        ("tcp_orphan", "max_tcp_orphan_to_start_worker"),
    )
    reasons = []
    for metric_key, limit_key in checks:
        limit = int(workers_cfg.get(limit_key, 0) or 0)
        if limit > 0 and int(socket_usage.get(metric_key, 0)) >= limit:
            reasons.append(
                f"{metric_key}={int(socket_usage.get(metric_key, 0))} >= {limit_key}={limit}"
            )

    if not reasons:
        return None

    return {"usage": socket_usage, "reasons": reasons}


def wait_for_socket_budget(config, logger, stage, video_id=None, deadline_ts=None):
    workers_cfg = config.get("workers", {})
    if not bool(workers_cfg.get("wait_for_socket_budget_before_network", True)):
        return

    timeout_seconds = max(
        0,
        int(workers_cfg.get("socket_pressure_wait_timeout_seconds", 180) or 0),
    )
    poll_seconds = max(
        1.0,
        float(workers_cfg.get("socket_pressure_poll_seconds", 5) or 5),
    )
    timeout_action = str(
        workers_cfg.get("socket_pressure_wait_timeout_action", "restart")
    ).strip().lower()
    start_ts = time.time()
    last_log_ts = 0.0

    while True:
        pressure = evaluate_socket_pressure(config, logger=logger)
        if pressure is None:
            return

        now = time.time()
        if now - last_log_ts >= poll_seconds:
            logger.warning(
                "Waiting for socket budget before %s for %s: %s",
                stage,
                video_id or "-",
                "; ".join(pressure["reasons"]),
            )
            last_log_ts = now

        if timeout_seconds > 0 and now - start_ts >= timeout_seconds:
            detail = (
                f"socket pressure persisted before {stage} for {video_id or '-'}: "
                + "; ".join(pressure["reasons"])
            )
            if timeout_action == "restart":
                raise ControlledWorkerRestart(detail)
            logger.warning(
                "%s; continuing because socket_pressure_wait_timeout_action=%s",
                detail,
                timeout_action,
            )
            return

        sleep_seconds = poll_seconds
        if deadline_ts is not None:
            sleep_seconds = clamp_sleep_seconds(
                poll_seconds,
                deadline_ts,
                f"waiting for socket budget before {stage}",
            )
        time.sleep(max(0.2, sleep_seconds))


def download_file_via_curl(
    url,
    local_filename,
    runtime_state,
    logger,
    config,
    video_id,
    interface_name=None,
    deadline_ts=None,
):
    settings = config["settings"]
    resume = config["resume"]
    connect_timeout = int(settings.get("connect_timeout", 30))
    read_timeout = int(
        settings.get(
            "curl_stall_timeout_seconds",
            settings.get("download_timeout", 600),
        )
    )
    progress_log_every = int(settings.get("download_progress_log_seconds", 15))
    download_interface = (interface_name or settings.get("download_interface") or "").strip()

    if not download_interface:
        raise RuntimeError("settings.download_interface is required for curl-based downloads")
    if not supports_interface_bound_downloads():
        raise RuntimeError(
            "interface-bound downloads are currently supported only on Linux; "
            "clear settings.download_interface on Windows"
        )

    curl_binary = shutil.which("curl")
    if not curl_binary:
        raise RuntimeError("curl is required when settings.download_interface is set")

    local_path = Path(local_filename)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = local_path.with_suffix(local_path.suffix + ".part")

    allow_resume = bool(resume.get("resume_partial_downloads", True))
    existing_size = part_path.stat().st_size if part_path.exists() else 0
    if existing_size and not allow_resume:
        part_path.unlink()
        existing_size = 0

    cmd = [
        curl_binary,
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--connect-timeout",
        str(connect_timeout),
        "--speed-time",
        str(read_timeout),
        "--speed-limit",
        "1",
        "--interface",
        download_interface,
        "--output",
        str(part_path),
    ]
    if existing_size > 0:
        cmd.extend(["-C", "-"])
        logger.info(
            "Resuming partial download for %s via curl from %.1f MB on interface %s",
            video_id,
            existing_size / (1024 * 1024),
            download_interface,
        )

    cmd.append(url)
    logger.info("Downloading %s via curl on interface %s", video_id, download_interface)

    stderr_output = ""
    with subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        last_log_ts = time.time()
        last_size = existing_size
        while True:
            try:
                ensure_deadline_not_exceeded(
                    deadline_ts,
                    f"downloading file for {video_id}",
                )
            except StageTimeoutError:
                logger.warning(
                    "Overall video timeout reached while curl was downloading %s; terminating curl process",
                    video_id,
                )
                terminate_subprocess_quietly(process)
                raise
            return_code = process.poll()
            current_size = part_path.stat().st_size if part_path.exists() else existing_size
            runtime_state.touch("downloading file", video_id)

            if time.time() - last_log_ts >= progress_log_every:
                if current_size != last_size or current_size > 0:
                    logger.info(
                        "Download progress for %s: %d MB",
                        video_id,
                        int(current_size / (1024 * 1024)),
                    )
                    last_size = current_size
                last_log_ts = time.time()

            if return_code is not None:
                break
            time.sleep(1)

        if process.stderr is not None:
            stderr_output = process.stderr.read().strip()

    if return_code != 0:
        error_message = stderr_output.splitlines()[-1] if stderr_output else f"curl exited with {return_code}"
        raise RuntimeError(error_message)

    if not part_path.exists():
        raise RuntimeError("curl completed but part file is missing")

    os.replace(part_path, local_path)
    file_size_mb = os.path.getsize(local_path) / (1024 * 1024)
    logger.info("Downloaded: %s (%.1f MB) via curl", local_path, file_size_mb)
    return file_size_mb


def download_file(
    url,
    local_filename,
    runtime_state,
    logger,
    config,
    video_id,
    download_session=None,
    deadline_ts=None,
):
    settings = config["settings"]
    resume = config["resume"]
    download_interface = (settings.get("download_interface") or "").strip()
    download_retry_interface = (settings.get("download_retry_interface") or "").strip()

    if download_interface:
        return download_file_via_curl(
            url,
            local_filename,
            runtime_state,
            logger,
            config,
            video_id,
            interface_name=download_interface,
            deadline_ts=deadline_ts,
        )

    chunk_size = int(settings.get("download_chunk_size_kb", 1024)) * 1024
    connect_timeout = int(settings.get("connect_timeout", 30))
    read_timeout = int(
        settings.get(
            "direct_download_timeout_seconds",
            settings.get("download_timeout", 600),
        )
    )
    progress_log_every = int(settings.get("download_progress_log_seconds", 15))

    local_path = Path(local_filename)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = local_path.with_suffix(local_path.suffix + ".part")

    allow_resume = bool(resume.get("resume_partial_downloads", True))
    existing_size = part_path.stat().st_size if part_path.exists() else 0
    if existing_size and not allow_resume:
        part_path.unlink()
        existing_size = 0

    request_headers = {}
    write_mode = "wb"
    if existing_size > 0:
        request_headers["Range"] = f"bytes={existing_size}-"
        write_mode = "ab"
        logger.info(
            "Resuming partial download for %s from %.1f MB",
            video_id,
            existing_size / (1024 * 1024),
        )

    bytes_written = existing_size
    content_length = 0
    last_log_ts = time.time()
    temporary_download_session = None
    if download_session is None:
        temporary_download_session = build_bounded_http_session(
            pool_connections=1,
            pool_maxsize=1,
        )
        download_session = temporary_download_session

    try:
        while True:
            ensure_deadline_not_exceeded(deadline_ts, f"downloading file for {video_id}")
            with download_session.get(
                url,
                stream=True,
                timeout=(connect_timeout, read_timeout),
                allow_redirects=True,
                headers=request_headers,
            ) as response:
                if existing_size > 0 and response.status_code == 416:
                    total_size = parse_content_range_total(response.headers.get("Content-Range"))
                    if total_size and existing_size >= total_size:
                        logger.info(
                            "Partial file for %s is already complete, finalizing without re-download",
                            video_id,
                        )
                        os.replace(part_path, local_path)
                        return os.path.getsize(local_path) / (1024 * 1024)

                    logger.warning(
                        "Server rejected resume range for %s with 416, restarting download from scratch",
                        video_id,
                    )
                    if part_path.exists():
                        part_path.unlink()
                    existing_size = 0
                    bytes_written = 0
                    request_headers = {}
                    write_mode = "wb"
                    last_log_ts = time.time()
                    continue

                response.raise_for_status()

                if existing_size > 0 and response.status_code != 206:
                    logger.warning(
                        "Server ignored resume range for %s (status=%s), restarting download from scratch",
                        video_id,
                        response.status_code,
                    )
                    if part_path.exists():
                        part_path.unlink()
                    existing_size = 0
                    bytes_written = 0
                    request_headers = {}
                    write_mode = "wb"
                    last_log_ts = time.time()
                    continue

                response_length = int(response.headers.get("content-length", 0) or 0)
                content_length = existing_size + response_length if existing_size else response_length
                runtime_state.touch("downloading file", video_id)

                with open(part_path, write_mode) as f:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        ensure_deadline_not_exceeded(deadline_ts, f"downloading file for {video_id}")
                        if not chunk:
                            continue
                        f.write(chunk)
                        bytes_written += len(chunk)
                        runtime_state.touch("downloading file", video_id)

                        if time.time() - last_log_ts >= progress_log_every:
                            if content_length:
                                progress_pct = bytes_written / content_length * 100
                                logger.info(
                                    "Download progress for %s: %.1f%% (%d / %d MB)",
                                    video_id,
                                    progress_pct,
                                    int(bytes_written / (1024 * 1024)),
                                    int(content_length / (1024 * 1024)),
                                )
                            else:
                                logger.info(
                                    "Download progress for %s: %d MB",
                                    video_id,
                                    int(bytes_written / (1024 * 1024)),
                                )
                            last_log_ts = time.time()
                break
    except Exception as exc:
        if download_retry_interface:
            logger.warning(
                "Direct download failed for %s (%s). Retrying via interface %s",
                video_id,
                exc,
                download_retry_interface,
            )
            return download_file_via_curl(
                url,
                local_filename,
                runtime_state,
                logger,
                config,
                video_id,
                interface_name=download_retry_interface,
                deadline_ts=deadline_ts,
            )
        raise
    finally:
        close_session_quietly(temporary_download_session)

    os.replace(part_path, local_path)
    file_size_mb = os.path.getsize(local_path) / (1024 * 1024)
    logger.info("Downloaded: %s (%.1f MB)", local_path, file_size_mb)
    return file_size_mb


def detect_extension(download_link):
    ext = ".mp4"
    if ext in download_link.lower():
        return ext
    ext = ".mov"
    if ext in download_link.lower():
        return ext
    return ""


def is_original_quality(selected_quality, config=None):
    if not selected_quality:
        return None
    haystack = str(selected_quality).lower()
    return "original" in haystack or "source" in haystack


def load_existing_download_metadata(metadata_path, config=None, logger=None):
    if not metadata_path.exists():
        return {
            "selected_quality": None,
            "download_source": None,
            "filename": None,
            "file_path": None,
            "file_size_mb": None,
            "is_original": False if config is not None else None,
            "offloaded": False,
            "remote_video_file": None,
            "remote_metadata_json": None,
        }

    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        if logger:
            logger.warning("Failed to read existing metadata %s: %s", metadata_path, exc)
        return {}

    download = payload.setdefault("_download", {})
    offload = payload.get("_offload") or {}
    selected_quality = download.get("selected_quality")
    download_source = download.get("source")
    is_original = download.get("is_original")

    if is_original is None:
        is_original = is_original_quality(selected_quality, config=config)
        download["is_original"] = is_original
        try:
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            if logger:
                logger.warning(
                    "Failed to backfill is_original in metadata %s: %s",
                    metadata_path,
                    exc,
                )

    return {
        "selected_quality": selected_quality,
        "download_source": download_source,
        "filename": download.get("filename"),
        "file_path": download.get("file_path"),
        "file_size_mb": download.get("file_size_mb"),
        "is_original": is_original,
        "offloaded": offload.get("status") == "uploaded",
        "remote_video_file": offload.get("remote_video_file"),
        "remote_metadata_json": offload.get("remote_metadata_json"),
    }


def get_video_storage_dir(video_dir, video_id):
    normalized_video_id = normalize_video_storage_key(video_id)
    return Path(video_dir) / "downloaded" / normalized_video_id


def get_metadata_bucket(status, result=None):
    result = result or {}
    if status == "downloaded":
        return "downloaded"
    if result.get("downloadable") or result.get("download_link_found"):
        return "not_downloaded"
    return "no_links"


def get_download_status_root(video_dir, status, result=None):
    return Path(video_dir) / get_metadata_bucket(status, result=result)


def get_video_metadata_path(video_dir, video_id, status="downloaded", result=None):
    normalized_video_id = normalize_video_storage_key(video_id)
    bucket = get_metadata_bucket(status, result=result)
    if bucket == "downloaded":
        storage_dir = Path(video_dir) / bucket / normalized_video_id
        return storage_dir / f"{normalized_video_id}.json"
    return Path(video_dir) / bucket / f"{normalized_video_id}.json"


def extract_canonical_video_id(video_id, json_data=None, result=None):
    candidates = []

    if isinstance(json_data, dict):
        candidates.extend(
            [
                json_data.get("id"),
                json_data.get("clip_id"),
            ]
        )
        uri = json_data.get("uri")
        if isinstance(uri, str):
            candidates.append(uri.rstrip("/").split("/")[-1])
        link = json_data.get("link")
        if isinstance(link, str):
            candidates.append(link.rstrip("/").split("/")[-1])

    if isinstance(result, dict):
        page_context = result.get("page_context") or {}
        next_data = page_context.get("next_data") or {}
        candidates.extend(
            [
                next_data.get("clip_id"),
                ((next_data.get("clip") or {}).get("id")),
            ]
        )

    for candidate in candidates:
        normalized = coerce_numeric_video_id(candidate)
        if normalized is not None:
            return normalized

    return normalize_video_storage_key(video_id)


def normalize_download_option(option):
    if not isinstance(option, dict):
        return None
    return {
        "text": option.get("text"),
        "href": option.get("href"),
        "quality": option.get("quality"),
        "rendition": option.get("rendition"),
        "width": option.get("width"),
        "height": option.get("height"),
    }


def normalize_download_options(options):
    normalized = []
    for option in options or []:
        item = normalize_download_option(option)
        if item is not None:
            normalized.append(item)
    return normalized


def summarize_vimeo_video(json_data):
    if not isinstance(json_data, dict):
        return None

    user = json_data.get("user") or {}
    summary = {
        "id": json_data.get("id"),
        "uri": json_data.get("uri"),
        "name": json_data.get("name"),
        "link": json_data.get("link"),
        "created_time": json_data.get("created_time"),
        "duration": json_data.get("duration"),
        "width": json_data.get("width"),
        "height": json_data.get("height"),
        "privacy": json_data.get("privacy"),
        "license": json_data.get("license"),
        "status": json_data.get("status"),
        "user": {
            "uri": user.get("uri"),
            "name": user.get("name"),
            "link": user.get("link"),
        } if user else None,
    }
    return summary


def collect_modal_download_options(sb, timeout=12):
    deadline = time.time() + timeout
    modal_seen = False
    last_options = []

    while time.time() < deadline:
        scope_element = None
        for selector in ("section[aria-modal='true']", "[role='dialog'][aria-modal='true']"):
            try:
                scope_element = sb.wait_for_query_selector(selector, timeout=1)
                modal_seen = True
                break
            except Exception:
                continue

        if scope_element is None and hasattr(sb, "cdp"):
            try:
                scope_element = sb.cdp.select("body", timeout=1)
            except Exception:
                scope_element = None

        if scope_element is not None:
            last_options = _extract_download_options_from_scope(scope_element)
            if last_options:
                return last_options
        time.sleep(0.5)

    if modal_seen:
        return last_options
    return []


def probe_player_iframe_download(sb, video_id, logger, timeout):
    click_download_button_in_player_iframe(sb, timeout=timeout, logger=logger)
    best_option = extract_best_player_iframe_download(
        sb, timeout=timeout, logger=logger
    )
    if logger and best_option:
        logger.info(
            "Recovered download link for %s from Vimeo player iframe fallback",
            video_id,
        )
    return best_option


def extract_script_json_by_id(page_source, script_id):
    pattern = re.compile(
        rf'<script[^>]*id=["\']{re.escape(script_id)}["\'][^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(page_source or "")
    if not match:
        return None
    raw_payload = match.group(1).strip()
    if not raw_payload:
        return None
    try:
        return json.loads(raw_payload)
    except Exception:
        return None


def extract_meta_tags(page_source):
    tags = {}
    patterns = [
        re.compile(
            r'<meta[^>]+(?:property|name)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
            re.IGNORECASE,
        ),
        re.compile(
            r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']([^"\']+)["\']',
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:
        for match in pattern.finditer(page_source or ""):
            if len(match.groups()) != 2:
                continue
            if pattern is patterns[0]:
                key, value = match.group(1), match.group(2)
            else:
                value, key = match.group(1), match.group(2)
            tags.setdefault(key, value)
    return tags


def extract_page_context_summary(page_source):
    if not page_source:
        return None

    summary = {}

    next_data = extract_script_json_by_id(page_source, "__NEXT_DATA__")
    if isinstance(next_data, dict):
        page_props = next_data.get("props", {}).get("pageProps", {})
        clip = page_props.get("clip") or {}
        page_metadata = page_props.get("pageMetadata") or {}
        summary["next_data"] = {
            "clip_id": page_props.get("clipId"),
            "clip_hash": page_props.get("clipHash"),
            "page_metadata": {
                "clip_signature": page_metadata.get("clipSignature"),
                "clip_thumbnail_url": page_metadata.get("clipThumbnailUrl"),
                "should_have_robots_meta": page_metadata.get("shouldHaveRobotsMeta"),
            },
            "clip": {
                "uri": clip.get("uri"),
                "name": clip.get("name"),
                "link": clip.get("link"),
                "privacy": clip.get("privacy"),
                "content_rating_class": clip.get("contentRatingClass"),
                "created_time": clip.get("createdTime"),
                "duration": clip.get("duration"),
                "width": clip.get("width"),
                "height": clip.get("height"),
            },
        }

    viewer_bootstrap = extract_script_json_by_id(page_source, "viewer-bootstrap")
    if isinstance(viewer_bootstrap, dict):
        bootstrap_user = viewer_bootstrap.get("ablincolnConfig", {}).get("user", {})
        summary["viewer_bootstrap"] = {
            "logged_in": bootstrap_user.get("logged_in"),
            "location": viewer_bootstrap.get("location"),
            "api_url": viewer_bootstrap.get("apiUrl"),
            "vimeo_https_url": viewer_bootstrap.get("vimeoHttpsUrl"),
            "content_viewing_prefs": viewer_bootstrap.get("contentViewingPrefs"),
            "is_turnstile_enabled": viewer_bootstrap.get("isTurnstileEnabled"),
            "requires_age_verification": viewer_bootstrap.get("requiresAgeVerification"),
            "requires_age_self_certification": viewer_bootstrap.get("requires_age_self_certification"),
            "is_from_copyright_restricted_region": viewer_bootstrap.get(
                "isFromCopyrightRestrictedRegion"
            ),
            "jwt_present": bool(viewer_bootstrap.get("jwt")),
            "recaptcha_site_key_present": bool(viewer_bootstrap.get("recaptchaSiteKey")),
            "turnstile_site_key_present": bool(viewer_bootstrap.get("turnstileSiteKey")),
        }

    meta_tags = extract_meta_tags(page_source)
    if meta_tags:
        summary["meta"] = {
            "canonical_url": meta_tags.get("og:url"),
            "player_url": meta_tags.get("og:video:secure_url") or meta_tags.get("twitter:player"),
            "player_width": meta_tags.get("og:video:width") or meta_tags.get("twitter:player:width"),
            "player_height": meta_tags.get("og:video:height") or meta_tags.get("twitter:player:height"),
            "poster": meta_tags.get("og:image"),
        }

    return summary or None


def build_api_probe(response_status_code, response_payload):
    best_option = None
    download_options_count = 0
    privacy_download = None
    api_has_download_link = False
    if isinstance(response_payload, dict):
        download_options = response_payload.get("download") or []
        download_options_count = len(download_options)
        privacy_download = response_payload.get("privacy", {}).get("download")
        best_option = extract_best_api_download(response_payload)
        api_has_download_link = bool(best_option)

    return {
        "status_code": response_status_code,
        "privacy_download": privacy_download,
        "download_options_count": download_options_count,
        "has_download_link": api_has_download_link,
        "best_option": normalize_download_option(best_option),
    }


def build_page_probe(result, include_extended=False):
    probe = {
        "visited": result.get("page_visited", False),
        "page_url": result.get("page_url"),
        "page_title": result.get("page_title"),
        "authenticated_session": result.get("authenticated_session"),
        "session_relogin": result.get("session_relogin", False),
        "cloudflare_detected": result.get("cloudflare_detected", False),
        "download_button_found": result.get("button_found"),
        "download_link_found": result.get("download_link_found", False),
        "available_options_count": result.get("available_options_count", 0),
        "best_option": normalize_download_option(result.get("page_best_option")),
        "has_original_option": result.get("has_original_option"),
        "error": result.get("probe_error"),
    }
    if include_extended:
        probe["available_options"] = result.get("available_options") or []
        probe["context"] = result.get("page_context")
    return probe


def get_no_links_list_path(config):
    return Path(config["files"]["logs_dir"]) / "no_links_urls.txt"


def record_no_links_url(config, video_url):
    path = get_no_links_list_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if path.exists():
        existing = {
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    if video_url not in existing:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{video_url}\n")
    return path


def build_metadata_payload(
    config,
    video_dir,
    video_id,
    video_url,
    json_data,
    api_probe,
    result,
    status,
    reason,
):
    canonical_video_id = extract_canonical_video_id(video_id, json_data=json_data, result=result)
    bucket = get_metadata_bucket(status, result=result)
    skip_no_links_json = bucket == "no_links" and status == "skipped"
    metadata_path = get_video_metadata_path(video_dir, canonical_video_id, status=status, result=result)
    if not skip_no_links_json:
        metadata_path.parent.mkdir(parents=True, exist_ok=True)

    file_path = result.get("file_path")
    filename = result.get("filename")
    if not file_path and filename:
        file_path = str(get_video_storage_dir(video_dir, canonical_video_id) / filename)

    compact_video = summarize_vimeo_video(json_data)
    store_full_downloaded_payload = bool(
        config["settings"].get("store_full_api_payload_for_downloaded", True)
    )
    stored_video_payload = (
        json_data if status == "downloaded" and store_full_downloaded_payload else compact_video
    )

    include_extended_page_probe = bucket == "downloaded" or status == "failed"
    payload = {
        "_saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "_video_id": canonical_video_id,
        "_requested_video_id": str(video_id),
        "_video_url": video_url,
        "_download": {
            "status": status,
            "reason": reason,
            "worker_name": config["runtime"]["worker_name"],
            "browser_mode": browser_mode_label(config),
            "authenticated_session": bool(config["runtime"].get("vimeo_authenticated_session", False)),
            "download_only_original": bool(config["settings"].get("download_only_original", False)),
            "downloadable": result.get("downloadable"),
            "filename": filename,
            "file_path": file_path,
            "file_size_mb": round(result["file_size_mb"], 3) if result.get("file_size_mb") is not None else None,
            "source": result.get("download_source"),
            "selected_quality": result.get("selected_quality"),
            "is_original": result.get("is_original"),
            "download_link": result.get("download_link"),
        },
        "_api": api_probe,
        "_page_probe": build_page_probe(result, include_extended=include_extended_page_probe),
        "_storage": {
            "bucket": bucket,
            "video_dir": str(
                get_video_storage_dir(video_dir, canonical_video_id)
                if bucket == "downloaded"
                else metadata_path.parent
                if not skip_no_links_json
                else Path(config["files"]["logs_dir"])
            ),
            "metadata_path": str(metadata_path) if not skip_no_links_json else None,
            "per_video_directory": bucket == "downloaded",
        },
        "_offload": {
            "status": None,
            "remote_root": None,
            "remote_video_file": None,
            "remote_metadata_json": None,
            "uploaded_at": None,
            "verified_with_ffprobe": None,
            "local_video_deleted": False,
            "local_video_deleted_at": None,
        },
        "vimeo_video": stored_video_payload,
    }
    return payload, metadata_path


def save_video_metadata(
    config,
    video_dir,
    video_id,
    video_url,
    json_data,
    api_probe,
    result,
    status,
    reason,
):
    payload, metadata_path = build_metadata_payload(
        config,
        video_dir,
        video_id,
        video_url,
        json_data,
        api_probe,
        result,
        status,
        reason,
    )
    if payload["_storage"]["bucket"] == "no_links" and status == "skipped":
        return record_no_links_url(config, video_url)
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return metadata_path


def download_video(
    sb,
    video_url,
    video_id,
    json_data,
    video_dir,
    logger,
    config,
    runtime_state,
    download_session=None,
    login_email=None,
    login_password=None,
    cloudflare_retry_count=0,
    video_deadline_ts=None,
):
    result = {
        "success": False,
        "skipped_by_policy": False,
        "error": None,
        "file_size_mb": None,
        "filename": None,
        "downloadable": False,
        "download_link_found": False,
        "download_source": None,
        "selected_quality": None,
        "is_original": None,
        "download_link": None,
        "button_found": None,
        "available_options_count": 0,
        "available_options": [],
        "api_best_option": None,
        "page_best_option": None,
        "has_original_option": None,
        "probe_error": None,
        "policy_reason": None,
        "page_visited": False,
        "page_url": None,
        "page_title": None,
        "page_context": None,
        "cloudflare_detected": False,
        "session_relogin": False,
        "authenticated_session": bool(config["runtime"].get("vimeo_authenticated_session", False)),
        "canonical_video_id": None,
        "file_path": None,
        "timeout_like": False,
    }

    try:
        if video_deadline_ts is None:
            video_deadline_ts = build_video_deadline(config["settings"])
        ensure_deadline_not_exceeded(video_deadline_ts, f"processing video {video_id}")
        api_download = extract_best_api_download(json_data)
        result["api_best_option"] = normalize_download_option(api_download)
        runtime_state.touch("checking direct api download", video_id)
        page_open_stage_timeout = int(
            config["settings"].get(
                "page_open_stage_timeout_seconds",
                config["settings"].get("page_load_timeout_seconds", 120),
            )
        )
        cloudflare_stage_timeout = int(
            config["settings"].get("cloudflare_stage_timeout_seconds", 180)
        )
        download_only_original = bool(config["settings"].get("download_only_original", False))
        api_selected_quality = None
        api_is_original = None
        if api_download:
            api_selected_quality = (
                api_download.get("text")
                or api_download.get("quality")
                or api_download.get("rendition")
            )
            api_is_original = is_original_quality(api_selected_quality, config=config)

        api_non_original_fallback = bool(
            api_download and download_only_original and api_is_original is not True
        )
        if api_download:
            result["download_source"] = "api"
            result["selected_quality"] = api_selected_quality
            result["is_original"] = api_is_original
            result["download_link"] = api_download.get("href")
            result["download_link_found"] = bool(api_download.get("href"))
            result["downloadable"] = True
            if api_non_original_fallback:
                result["policy_reason"] = "downloadable via api but best available option is not original"

        use_api_download = bool(api_download)
        if download_only_original and not api_is_original:
            use_api_download = False

        if use_api_download:
            download_link = api_download["href"]
            logger.info(
                "Using direct API download link for %s (%s)",
                video_id,
                api_download.get("text")
                or api_download.get("quality")
                or "best available",
            )
        else:
            runtime_state.touch("opening video page", video_id)
            logger.info("Opening %s", video_url)
            call_with_stage_timeout(
                resolve_stage_timeout(page_open_stage_timeout, video_deadline_ts),
                f"opening video page for {video_id}",
                sb.open,
                video_url,
            )
            result["page_visited"] = True

            initial_wait = random.uniform(PAGE_LOAD_WAIT_MIN, PAGE_LOAD_WAIT_MAX)
            initial_wait = clamp_sleep_seconds(
                initial_wait,
                video_deadline_ts,
                f"processing video {video_id}",
            )
            logger.info("Waiting %.1fs for page load...", initial_wait)
            sb.sleep(initial_wait)

            try:
                result["page_url"] = sb.get_current_url()
            except Exception:
                result["page_url"] = video_url
            try:
                result["page_title"] = sb.get_title()
            except Exception:
                result["page_title"] = None
            try:
                result["page_context"] = extract_page_context_summary(sb.get_page_source())
            except Exception:
                result["page_context"] = None

            if bool(config["runtime"].get("vimeo_authenticated_session", False)) and is_login_page(
                result.get("page_url"),
                result.get("page_title"),
                result.get("page_context"),
            ):
                logger.warning(
                    "Session appears logged out while opening %s; re-authenticating",
                    video_url,
                )
                runtime_state.touch("re-authenticating session", video_id)
                login_to_vimeo(sb, login_email, login_password, logger, config)
                result["session_relogin"] = True
                runtime_state.touch("opening video page", video_id)
                call_with_stage_timeout(
                    resolve_stage_timeout(page_open_stage_timeout, video_deadline_ts),
                    f"re-opening video page for {video_id} after login",
                    sb.open,
                    video_url,
                )

                initial_wait = random.uniform(PAGE_LOAD_WAIT_MIN, PAGE_LOAD_WAIT_MAX)
                initial_wait = clamp_sleep_seconds(
                    initial_wait,
                    video_deadline_ts,
                    f"processing video {video_id}",
                )
                logger.info("Waiting %.1fs for page reload after login...", initial_wait)
                sb.sleep(initial_wait)
                try:
                    result["page_url"] = sb.get_current_url()
                except Exception:
                    result["page_url"] = video_url
                try:
                    result["page_title"] = sb.get_title()
                except Exception:
                    result["page_title"] = None
                try:
                    result["page_context"] = extract_page_context_summary(sb.get_page_source())
                except Exception:
                    result["page_context"] = None

            if check_if_cloudflare_blocked(sb, logger):
                result["cloudflare_detected"] = True
                cloudflare_timeout = random.uniform(
                    CLOUDFLARE_TIMEOUT_MIN,
                    CLOUDFLARE_TIMEOUT_MAX,
                )
                cloudflare_timeout += cloudflare_retry_count * 10
                cloudflare_timeout = clamp_sleep_seconds(
                    cloudflare_timeout,
                    video_deadline_ts,
                    f"processing video {video_id}",
                )

                runtime_state.touch("waiting cloudflare auto-bypass", video_id)
                logger.warning(
                    "Cloudflare detected, waiting %.0fs for auto-bypass...",
                    cloudflare_timeout,
                )

                def _wait_and_recheck_cloudflare():
                    sb.sleep(cloudflare_timeout)
                    return check_if_cloudflare_blocked(sb, logger)

                if call_with_stage_timeout(
                    resolve_stage_timeout(cloudflare_stage_timeout, video_deadline_ts),
                    f"cloudflare auto-bypass stage for {video_id}",
                    _wait_and_recheck_cloudflare,
                ):
                    result["error"] = (
                        f"Cloudflare Turnstile not bypassed after "
                        f"{cloudflare_timeout:.0f}s"
                    )
                    logger.error(result["error"])
                    try:
                        page_source = sb.get_page_source()
                        debug_video_id = normalize_video_storage_key(video_id)
                        debug_file = Path(config["files"]["logs_dir"]) / (
                            f"cloudflare_fail_{debug_video_id}.html"
                        )
                        with open(debug_file, "w", encoding="utf-8") as f:
                            f.write(page_source)
                        logger.info("Saved failed HTML to %s", debug_file)
                    except Exception:
                        pass
                    return result

                logger.info("Cloudflare bypassed successfully")

            runtime_state.touch("simulating mouse movement", video_id)
            ensure_deadline_not_exceeded(video_deadline_ts, f"processing video {video_id}")
            simulate_mouse_movement(sb, logger)

            runtime_state.touch("simulating page scroll", video_id)
            ensure_deadline_not_exceeded(video_deadline_ts, f"processing video {video_id}")
            simulate_page_scroll(sb, logger)

            reading_time = random.uniform(READING_TIME_MIN, READING_TIME_MAX)
            reading_time = clamp_sleep_seconds(
                reading_time,
                video_deadline_ts,
                f"processing video {video_id}",
            )
            runtime_state.touch("simulating reading", video_id)
            logger.info("Simulating reading (%.1fs)...", reading_time)
            sb.sleep(reading_time)

            js_timeout = max(10, int(config["settings"]["javascript_wait_time"]))
            runtime_state.touch("clicking download button", video_id)
            try:
                click_download_button(sb, timeout=js_timeout, logger=logger)
                result["button_found"] = True
            except Exception as exc:
                iframe_best_option = None
                try:
                    logger.info(
                        "Trying Vimeo player iframe fallback for %s after page-level button miss",
                        video_id,
                    )
                    iframe_best_option = probe_player_iframe_download(
                        sb, video_id, logger, js_timeout
                    )
                except Exception as iframe_exc:
                    logger.info(
                        "Player iframe fallback did not recover %s: %s",
                        video_id,
                        iframe_exc,
                    )

                if iframe_best_option:
                    result["button_found"] = True
                    result["available_options"] = normalize_download_options(
                        [iframe_best_option]
                    )
                    result["available_options_count"] = len(
                        result["available_options"]
                    )
                    result["page_best_option"] = normalize_download_option(
                        iframe_best_option
                    )
                    result["has_original_option"] = is_original_quality(
                        iframe_best_option.get("text"), config=config
                    )
                    download_link = iframe_best_option["href"]
                    result["download_source"] = "page"
                    result["selected_quality"] = iframe_best_option.get("text")
                    result["is_original"] = is_original_quality(
                        result["selected_quality"],
                        config=config,
                    )
                    result["download_link"] = download_link
                    result["download_link_found"] = True
                    result["downloadable"] = True
                    logger.info("Got download link from player iframe fallback")
                else:
                    result["button_found"] = False
                    result["probe_error"] = "Download button not found"
                    if api_non_original_fallback:
                        result["skipped_by_policy"] = True
                        result["policy_reason"] = (
                            "downloadable via api but not original; page probe failed: "
                            "Download button not found"
                        )
                        logger.info(
                            "Page probe for %s did not find button, but API already confirmed non-original downloadability (link=%s)",
                            video_id,
                            result.get("download_link"),
                        )
                        return result
                    result["error"] = "Download button not found"
                    logger.error("Download button not found for %s: %s", video_id, exc)
                    try:
                        page_source = sb.get_page_source()
                        debug_video_id = normalize_video_storage_key(video_id)
                        debug_file = Path(config["files"]["logs_dir"]) / (
                            f"no_button_{debug_video_id}.html"
                        )
                        with open(debug_file, "w", encoding="utf-8") as f:
                            f.write(page_source)
                        logger.info("Saved HTML (no button) to %s", debug_file)
                    except Exception:
                        pass
                    return result

            if not result.get("download_link_found"):
                runtime_state.touch("extracting modal download option", video_id)
                options = collect_modal_download_options(sb, timeout=js_timeout)
                result["available_options"] = normalize_download_options(options)
                result["available_options_count"] = len(result["available_options"])
                best_option = choose_best_download_option(options)
                result["page_best_option"] = normalize_download_option(best_option)
                result["has_original_option"] = any(
                    is_original_quality(option.get("text"), config=config)
                    for option in result["available_options"]
                )

                if not best_option:
                    transcript_modal = False
                    with suppress(Exception):
                        transcript_modal = modal_looks_like_transcript(
                            sb.get_page_source()
                        )

                    iframe_best_option = None
                    try:
                        logger.info(
                            "Trying Vimeo player iframe fallback for %s after page-level modal miss",
                            video_id,
                        )
                        iframe_best_option = probe_player_iframe_download(
                            sb, video_id, logger, js_timeout
                        )
                    except Exception as iframe_exc:
                        logger.info(
                            "Player iframe fallback did not recover %s: %s",
                            video_id,
                            iframe_exc,
                        )

                    if iframe_best_option:
                        best_option = iframe_best_option
                        result["available_options"] = normalize_download_options(
                            [iframe_best_option]
                        )
                        result["available_options_count"] = len(
                            result["available_options"]
                        )
                        result["page_best_option"] = normalize_download_option(
                            iframe_best_option
                        )
                        result["has_original_option"] = is_original_quality(
                            iframe_best_option.get("text"), config=config
                        )
                    else:
                        result["probe_error"] = (
                            "Transcript download modal opened instead of video download"
                            if transcript_modal
                            else (
                                "No download option found in modal"
                                if result["available_options_count"] > 0
                                else "Download modal not found"
                            )
                        )
                        if api_non_original_fallback:
                            result["skipped_by_policy"] = True
                            result["policy_reason"] = (
                                "downloadable via api but not original; page probe did not expose original "
                                f"({result['probe_error']})"
                            )
                            logger.info(
                                "Page probe for %s did not expose original; keeping API non-original metadata only (link=%s)",
                                video_id,
                                result.get("download_link"),
                            )
                            return result
                        result["error"] = result["probe_error"]
                        logger.error(result["error"])
                        return result

                download_link = best_option["href"]
                result["download_source"] = "page"
                result["selected_quality"] = best_option.get("text")
                result["is_original"] = is_original_quality(
                    result["selected_quality"],
                    config=config,
                )
                result["download_link"] = download_link
                result["download_link_found"] = True
                result["downloadable"] = True
                logger.info("Got download link")

        if download_only_original and result["is_original"] is not True:
            result["skipped_by_policy"] = True
            if not result.get("policy_reason"):
                result["policy_reason"] = "downloadable but best available option is not original"
            logger.info(
                "Skipping download for %s because best available option is not original (%s, link=%s)",
                video_id,
                result["selected_quality"] or "unknown quality",
                result.get("download_link"),
            )
            return result

        canonical_video_id = extract_canonical_video_id(video_id, json_data=json_data, result=result)
        result["canonical_video_id"] = canonical_video_id
        ext = detect_extension(download_link)
        filename = f"{canonical_video_id}{ext}"
        local_path = get_video_storage_dir(video_dir, canonical_video_id) / filename

        wait_for_socket_budget(
            config,
            logger,
            stage="download",
            video_id=video_id,
            deadline_ts=video_deadline_ts,
        )
        runtime_state.touch("downloading file", video_id)
        logger.info("Starting download to %s", local_path)
        file_size_mb = download_file(
            download_link,
            local_path,
            runtime_state,
            logger,
            config,
            video_id,
            download_session=download_session,
            deadline_ts=video_deadline_ts,
        )

        result["success"] = True
        result["file_size_mb"] = file_size_mb
        result["filename"] = filename
        result["file_path"] = str(local_path)
        logger.info("Successfully downloaded %s (%.1f MB)", video_id, file_size_mb)

    except Exception as exc:
        result["error"] = str(exc)
        result["timeout_like"] = is_timeout_like_message(exc)
        logger.error("Error downloading %s: %s", video_id, exc)

    return result


def browser_mode_label(config):
    browser = config["browser"]
    flags = []
    if browser.get("headless"):
        flags.append("headless")
    else:
        flags.append("headed")
    if browser.get("xvfb"):
        flags.append("xvfb")
    if browser.get("uc"):
        flags.append("uc")
    return ", ".join(flags)


def build_sb_kwargs(config, logger):
    browser = config["browser"]
    xvfb = bool(browser.get("xvfb", False))
    if xvfb and not supports_xvfb():
        logger.warning("xvfb requested on unsupported platform; disabling xvfb")
        xvfb = False

    browser_name = str(browser.get("browser_name", "") or "").strip().lower()
    binary_location = str(browser.get("binary_location", "") or "").strip()
    if not binary_location:
        detected_browser = find_browser_command()
        if detected_browser:
            binary_location = str(detected_browser)
    if not browser_name and binary_location:
        lowered_binary = binary_location.lower()
        if "msedge" in lowered_binary or lowered_binary.endswith("edge"):
            browser_name = "edge"
        else:
            browser_name = "chrome"
    if not browser_name:
        browser_name = "chrome"

    logger.info(
        "Selected SeleniumBase browser: browser=%s binary=%s",
        browser_name,
        binary_location or "<default>",
    )

    return {
        "browser": browser_name,
        "uc": bool(browser.get("uc", True)),
        "headless": bool(browser.get("headless", False)),
        "xvfb": xvfb,
        "disable_csp": bool(browser.get("disable_csp", True)),
        "block_images": bool(browser.get("block_images", False)),
        "incognito": bool(browser.get("incognito", False)),
        "binary_location": binary_location or None,
        "chromium_arg": browser.get(
            "chromium_arg",
            "--disable-blink-features=AutomationControlled",
        ),
    }


def write_summary(config, summary):
    summary_path = Path(config["files"]["summary_file"])
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = summary_path.with_suffix(summary_path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    os.replace(temp_path, summary_path)


def _now_string():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def compute_urls_signature(urls):
    digest = hashlib.sha256()
    for url in urls:
        digest.update(str(url).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def default_results_manifest(config, urls, source_signature):
    items = []
    for index, url in enumerate(urls, start=1):
        items.append(
            {
                "index": index,
                "video_id": extract_video_id(url),
                "url": url,
                "status": "pending",
                "reason": None,
                "filename": None,
                "file_size_mb": None,
                "metadata_json": None,
                "video_file": None,
                "download_source": None,
                "download_link": None,
                "selected_quality": None,
                "is_original": None,
                "downloadable": None,
                "button_found": None,
                "download_link_found": None,
                "available_options_count": 0,
                "video_folder": None,
                "storage_bucket": None,
                "requested_video_id": extract_video_id(url),
                "updated_at": None,
            }
        )

    return {
        "source_json": config["files"]["source_json"],
        "source_signature": source_signature,
        "total_urls": len(urls),
        "updated_at": None,
        "items": items,
    }


def load_results_manifest(config, urls, source_signature, logger):
    manifest_path = Path(config["files"]["results_file"])
    default_manifest = default_results_manifest(config, urls, source_signature)

    if not manifest_path.exists():
        return default_manifest

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception as exc:
        logger.warning("Failed to read results manifest %s: %s. Rebuilding.", manifest_path, exc)
        return default_manifest

    if loaded.get("source_json") != config["files"]["source_json"]:
        logger.warning("Results manifest source_json mismatch. Rebuilding %s", manifest_path)
        return default_manifest
    if loaded.get("source_signature") != source_signature:
        logger.warning("Results manifest source_signature mismatch. Rebuilding %s", manifest_path)
        return default_manifest
    if loaded.get("total_urls") != len(urls):
        logger.warning("Results manifest total_urls mismatch. Rebuilding %s", manifest_path)
        return default_manifest

    items = loaded.get("items")
    if not isinstance(items, list) or len(items) != len(urls):
        logger.warning("Results manifest items mismatch. Rebuilding %s", manifest_path)
        return default_manifest

    for item in items:
        item.setdefault("is_original", None)
        item.setdefault("downloadable", None)
        item.setdefault("button_found", None)
        item.setdefault("download_link_found", None)
        item.setdefault("available_options_count", 0)
        item.setdefault("video_folder", None)
        item.setdefault("download_link", None)
        item.setdefault("storage_bucket", None)
        item.setdefault("requested_video_id", item.get("video_id"))

    return loaded


def save_results_manifest(config, manifest):
    manifest_path = Path(config["files"]["results_file"])
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = _now_string()
    temp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(temp_path, manifest_path)


def update_results_manifest(manifest, index, **fields):
    item = manifest["items"][index - 1]
    item.update(fields)
    item["updated_at"] = _now_string()


def default_resume_state(config, total_urls, source_signature):
    return {
        "enabled": bool(config["resume"]["enabled"]),
        "source_json": config["files"]["source_json"],
        "source_signature": source_signature,
        "total_urls": total_urls,
        "next_index": 0,
        "successful_downloads": 0,
        "skipped_videos": 0,
        "failed_videos": 0,
        "last_video_id": None,
        "last_status": None,
        "updated_at": None,
        "completed": False,
    }


def load_resume_state(config, total_urls, source_signature, logger):
    state = default_resume_state(config, total_urls, source_signature)
    if not config["resume"]["enabled"]:
        return state

    state_path = Path(config["resume"]["state_file"])
    if not state_path.exists():
        return state

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception as exc:
        logger.warning("Failed to read resume state %s: %s. Starting fresh.", state_path, exc)
        return state

    if loaded.get("source_json") != config["files"]["source_json"]:
        logger.warning("Resume source_json mismatch. Ignoring old state from %s", state_path)
        return state

    if loaded.get("source_signature") != source_signature:
        logger.warning("Resume source_signature mismatch. Ignoring old state from %s", state_path)
        return state

    if loaded.get("total_urls") != total_urls:
        logger.warning(
            "Resume total_urls mismatch (%s vs %s). Ignoring old state.",
            loaded.get("total_urls"),
            total_urls,
        )
        return state

    state.update(loaded)
    state["next_index"] = max(0, min(int(state.get("next_index", 0)), total_urls))
    state["successful_downloads"] = int(state.get("successful_downloads", 0))
    state["skipped_videos"] = int(state.get("skipped_videos", 0))
    state["failed_videos"] = int(state.get("failed_videos", 0))
    state["completed"] = bool(state.get("completed", False))
    logger.info(
        "Loaded resume state: next_index=%s success=%s skipped=%s failed=%s completed=%s",
        state["next_index"],
        state["successful_downloads"],
        state["skipped_videos"],
        state["failed_videos"],
        state["completed"],
    )
    return state


def save_resume_state(config, state):
    if not config["resume"]["enabled"]:
        return

    state_path = Path(config["resume"]["state_file"])
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["updated_at"] = _now_string()
    temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(temp_path, state_path)


def checkpoint_resume_state(
    config,
    state,
    processed_index,
    successful_downloads,
    skipped_videos,
    failed_videos,
    video_id,
    last_status,
    total_urls,
    source_signature,
):
    if not config["resume"]["enabled"]:
        return

    state.update(
        {
            "source_json": config["files"]["source_json"],
            "source_signature": source_signature,
            "total_urls": total_urls,
            "next_index": processed_index,
            "successful_downloads": successful_downloads,
            "skipped_videos": skipped_videos,
            "failed_videos": failed_videos,
            "last_video_id": video_id,
            "last_status": last_status,
            "completed": processed_index >= total_urls,
        }
    )
    save_resume_state(config, state)


def find_existing_completed_file(video_dir, video_id):
    video_dir = Path(video_dir)
    normalized_video_id = normalize_video_storage_key(video_id)
    per_video_dir = get_video_storage_dir(video_dir, normalized_video_id)
    search_roots = []
    if per_video_dir.exists():
        search_roots.append(per_video_dir)
    search_roots.append(video_dir)

    seen = set()
    for root in search_roots:
        pattern = f"{normalized_video_id}*"
        for candidate in sorted(root.glob(pattern)):
            candidate = candidate.resolve()
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_dir():
                continue
            if candidate.suffix in {".part", ".json"}:
                continue
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
    return None


def should_skip_existing_file(existing_file, existing_metadata, config):
    if existing_file is None:
        return False
    if not bool(config["settings"].get("download_only_original", False)):
        return True
    return existing_metadata.get("is_original") is True


def should_skip_offloaded_metadata(existing_metadata, config):
    if not existing_metadata.get("offloaded"):
        return False
    if not bool(config["settings"].get("download_only_original", False)):
        return True
    return existing_metadata.get("is_original") is True


def resolve_existing_metadata_path(video_dir, json_dir, video_id):
    normalized_video_id = normalize_video_storage_key(video_id)
    for status in ("downloaded", "skipped", "failed"):
        preferred = get_video_metadata_path(video_dir, normalized_video_id, status=status)
        if preferred.exists():
            return preferred
    for bucket in ("not_downloaded", "no_links"):
        preferred = Path(video_dir) / bucket / f"{normalized_video_id}.json"
        if preferred.exists():
            return preferred
    legacy = Path(json_dir) / f"{normalized_video_id}.json"
    if legacy.exists():
        return legacy
    return get_video_metadata_path(video_dir, normalized_video_id, status="downloaded")


def api_error_payload(response):
    try:
        return response.json()
    except Exception:
        return None


def create_placeholder_result():
    return {
        "success": False,
        "skipped_by_policy": False,
        "error": None,
        "file_size_mb": None,
        "filename": None,
        "downloadable": False,
        "download_link_found": False,
        "download_source": None,
        "selected_quality": None,
        "is_original": None,
        "download_link": None,
        "button_found": None,
        "available_options_count": 0,
        "available_options": [],
        "api_best_option": None,
        "page_best_option": None,
        "has_original_option": None,
        "page_visited": False,
        "page_url": None,
        "page_title": None,
        "page_context": None,
        "cloudflare_detected": False,
        "session_relogin": False,
        "authenticated_session": False,
    }


def load_existing_failed_urls(config, logger, should_resume):
    if not should_resume:
        return []

    failed_file = Path(config["files"]["failed_downloads"])
    if not failed_file.exists():
        return []

    try:
        with open(failed_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            logger.info("Loaded %d previous failed URLs from %s", len(data), failed_file)
            return data
    except Exception as exc:
        logger.warning("Failed to read previous failed URLs from %s: %s", failed_file, exc)
    return []


def should_recycle_after_fatal_exception(config, start_index, successful_downloads, skipped_videos, failed_videos):
    if not bool(config["resume"].get("enabled", False)):
        return False

    processed_total = successful_downloads + skipped_videos + failed_videos
    new_progress = processed_total - int(start_index or 0)
    return new_progress > 0


def parse_args():
    parser = argparse.ArgumentParser(description="Vimeo downloader worker")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to config JSON",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config(args.config)
    logger = setup_logger(config["files"]["log_file"])

    worker_name = config["runtime"]["worker_name"]
    browser_mode = browser_mode_label(config)
    logger.info("=" * 80)
    logger.info("VIMEO DOWNLOADER V3")
    logger.info("Worker: %s", worker_name)
    logger.info("Config: %s", config["_meta"]["config_path"])
    logger.info("Browser mode: %s", browser_mode)
    logger.info("=" * 80)

    telegram = TelegramNotifier(
        config,
        worker_name=worker_name,
        job_name=config["runtime"]["job_name"],
    )

    api_session = build_vimeo_api_http_session(config)
    logger.info("Vimeo API session initialized")

    source_file = Path(config["files"]["source_json"])
    logger.info("Loading URLs from %s", source_file)
    with open(source_file, "r", encoding="utf-8") as f:
        urls = json.load(f)

    if config["settings"]["test_mode"]:
        urls = urls[: int(config["settings"]["test_limit"])]
        logger.info("TEST MODE: Processing only first %d videos", len(urls))
    else:
        logger.info("Processing %d videos", len(urls))

    video_dir = Path(config["files"]["videos_dir"])
    json_dir = Path(config["files"]["jsons_dir"])
    video_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Directories ready: %s, %s", video_dir, json_dir)

    runtime_state = RuntimeState(worker_name, len(urls))
    source_signature = compute_urls_signature(urls)
    resume_state = load_resume_state(config, len(urls), source_signature, logger)
    start_index = resume_state["next_index"] if config["resume"]["enabled"] else 0
    successful_downloads = resume_state["successful_downloads"]
    skipped_videos = resume_state["skipped_videos"]
    failed_videos = resume_state["failed_videos"]
    failed_urls = load_existing_failed_urls(
        config,
        logger,
        should_resume=config["resume"]["enabled"] and start_index > 0,
    )
    results_manifest = load_results_manifest(config, urls, source_signature, logger)
    save_results_manifest(config, results_manifest)
    runtime_state.update_counts(
        start_index,
        successful_downloads,
        skipped_videos,
        failed_videos,
    )

    if config["resume"]["enabled"]:
        logger.info(
            "Resume enabled: state=%s next_index=%d/%d",
            config["resume"]["state_file"],
            start_index,
            len(urls),
        )

    watchdog = ActivityWatchdog(runtime_state, telegram, logger, config)
    watchdog.start()

    telegram.notify_start(
        len(urls),
        test_mode=config["settings"]["test_mode"],
        browser_mode=browser_mode,
    )

    fatal_error = None
    fatal_category = None
    fatal_disk_snapshot = None
    exit_code = 0
    controlled_restart_requested = False
    controlled_restart_reason = None
    login_email = None
    login_password = None
    api_401_fallback_enabled = bool(
        config["settings"].get("api_401_fallback_to_page", False)
    )
    api_disabled_after_auth_error = False
    api_disabled_banner_logged = False
    download_session = build_download_http_session(config)
    consecutive_timeout_failures = 0

    sb_kwargs = build_sb_kwargs(config, logger)
    logger.info("SeleniumBase args: %s", sb_kwargs)

    try:
        ensure_min_free_disk_space(
            config,
            stage="worker startup",
            logger=logger,
            telegram=telegram,
            runtime_state=runtime_state,
        )
        if start_index >= len(urls):
            logger.info(
                "Resume state indicates all %d URLs are already processed. Exiting without browser launch.",
                len(urls),
            )
        else:
            if bool(config["runtime"].get("vimeo_authenticated_session", False)):
                login_email, login_password = require_login_credentials(config)
                logger.info("Authenticated Vimeo session requested for this worker")
            with SB(**sb_kwargs) as sb:
                logger.info("SeleniumBase browser started")
                page_load_timeout = int(config["settings"].get("page_load_timeout_seconds", 120))
                if page_load_timeout > 0:
                    try:
                        sb.driver.set_page_load_timeout(page_load_timeout)
                        logger.info("Browser page load timeout set to %ds", page_load_timeout)
                    except ControlledWorkerRestart:
                        raise
                    except Exception as exc:
                        logger.warning("Failed to set page load timeout: %s", exc)
                if bool(config["runtime"].get("vimeo_authenticated_session", False)):
                    runtime_state.touch("logging into vimeo")
                    login_to_vimeo(sb, login_email, login_password, logger, config)

                for i, video_url in enumerate(urls[start_index:], start=start_index + 1):
                    video_id = extract_video_id(video_url)
                    ensure_min_free_disk_space(
                        config,
                        stage="before processing next video",
                        current_video_id=video_id,
                        logger=logger,
                        telegram=telegram,
                        runtime_state=runtime_state,
                    )
                    if config["resume"]["skip_completed_files"]:
                        existing_file = find_existing_completed_file(video_dir, video_id)
                        metadata_path = resolve_existing_metadata_path(video_dir, json_dir, video_id)
                        existing_metadata = load_existing_download_metadata(
                            metadata_path,
                            config=config,
                            logger=logger,
                        )
                        skip_reason = None
                        skipped_video_file = None
                        skipped_file_size_mb = existing_metadata.get("file_size_mb")
                        skipped_video_folder = None

                        if existing_file is not None:
                            if not should_skip_existing_file(existing_file, existing_metadata, config):
                                logger.info(
                                    "Existing file for %s is not considered complete under original-only policy; reprocessing",
                                    video_id,
                                )
                            else:
                                skip_reason = "file already existed before resume"
                                skipped_video_file = str(existing_file)
                                skipped_file_size_mb = round(existing_file.stat().st_size / (1024 * 1024), 3)
                                skipped_video_folder = str(existing_file.parent)
                                logger.info(
                                    "Skipping %s because completed file already exists: %s",
                                    video_id,
                                    existing_file,
                                )
                        elif should_skip_offloaded_metadata(existing_metadata, config):
                            skip_reason = "file already offloaded before resume"
                            skipped_video_file = (
                                existing_metadata.get("remote_video_file")
                                or existing_metadata.get("file_path")
                            )
                            skipped_video_folder = (
                                str(Path(skipped_video_file).parent) if skipped_video_file else None
                            )
                            logger.info(
                                "Skipping %s because metadata says the downloaded file was already offloaded",
                                video_id,
                            )

                        if skip_reason:
                            successful_downloads += 1
                            runtime_state.touch("already downloaded", video_id)
                            runtime_state.update_counts(
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                            )
                            checkpoint_resume_state(
                                config,
                                resume_state,
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                video_id,
                                "already_downloaded",
                                len(urls),
                                source_signature,
                            )
                            update_results_manifest(
                                results_manifest,
                                i,
                                status="downloaded",
                                reason=skip_reason,
                                filename=existing_metadata.get("filename") or (Path(skipped_video_file).name if skipped_video_file else None),
                                file_size_mb=skipped_file_size_mb,
                                metadata_json=str(metadata_path),
                                video_file=skipped_video_file,
                                download_source=existing_metadata.get("download_source"),
                                selected_quality=existing_metadata.get("selected_quality"),
                                is_original=existing_metadata.get("is_original"),
                                downloadable=True,
                                download_link_found=True,
                                video_folder=skipped_video_folder,
                                storage_bucket="downloaded",
                            )
                            save_results_manifest(config, results_manifest)
                            telegram.notify_progress(
                                i,
                                len(urls),
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                current_video_id=video_id,
                                current_stage="already downloaded",
                            )
                            consecutive_timeout_failures = 0
                            maybe_raise_controlled_restart(
                                config,
                                start_index,
                                i,
                                len(urls),
                                consecutive_timeout_failures,
                                logger,
                                video_id,
                            )
                            if i < len(urls):
                                delay = random.uniform(
                                    MIN_DELAY_BETWEEN_VIDEOS,
                                    MAX_DELAY_BETWEEN_VIDEOS,
                                )
                                logger.info("Delay before next video: %.1fs", delay)
                                time.sleep(delay)
                            continue

                    runtime_state.touch("api check", video_id)
                    logger.info("\n%s", "=" * 80)
                    logger.info("[%d/%d] Processing video %s", i, len(urls), video_id)
                    logger.info("URL: %s", video_url)
                    logger.info("%s", "=" * 80)

                    response = None
                    api_payload = {}
                    api_probe = build_api_probe(None, None)
                    result = create_placeholder_result()
                    try:
                        if api_disabled_after_auth_error:
                            if not api_disabled_banner_logged:
                                logger.warning(
                                    "Vimeo API disabled for this worker after prior 401; continuing in page-probe mode"
                                )
                                api_disabled_banner_logged = True
                        else:
                            wait_for_socket_budget(
                                config,
                                logger,
                                stage="api_request",
                                video_id=video_id,
                            )
                            logger.info("Checking video %s via API...", video_id)
                            response = get_vimeo_api_video(api_session, video_id, config)
                            api_payload = api_error_payload(response)
                            api_probe = build_api_probe(response.status_code, api_payload)

                        if response is not None and response.status_code == 401:
                            if api_401_fallback_enabled:
                                api_disabled_after_auth_error = True
                                api_disabled_banner_logged = True
                                api_payload = {}
                                logger.warning(
                                    "Vimeo API returned 401 for %s; disabling API for this worker and falling back to page probe",
                                    video_id,
                                )
                                telegram.notify_api_error(
                                    401,
                                    "Worker API token returned 401. Switching this worker to page-probe mode.",
                                )
                            else:
                                fatal_error = "API Authentication error (401) - invalid token"
                                logger.error(fatal_error)
                                metadata_path = save_video_metadata(
                                    config,
                                    video_dir,
                                    video_id,
                                    video_url,
                                    None,
                                    api_probe,
                                    result,
                                    "failed",
                                    "fatal_api_401 invalid token",
                                )
                                update_results_manifest(
                                    results_manifest,
                                    i,
                                    status="failed",
                                    reason="fatal_api_401 invalid token",
                                    metadata_json=str(metadata_path),
                                    storage_bucket=get_metadata_bucket("failed", result=result),
                                )
                                save_results_manifest(config, results_manifest)
                                telegram.notify_api_error(401, "Invalid API token")
                                exit_code = 2
                                break

                        if response is not None and response.status_code == 429:
                            fatal_error = "API rate limit exceeded (429)"
                            logger.error(fatal_error)
                            metadata_path = save_video_metadata(
                                config,
                                video_dir,
                                video_id,
                                video_url,
                                None,
                                api_probe,
                                result,
                                "failed",
                                "fatal_api_429 rate limit exceeded",
                            )
                            update_results_manifest(
                                results_manifest,
                                i,
                                status="failed",
                                reason="fatal_api_429 rate limit exceeded",
                                metadata_json=str(metadata_path),
                                storage_bucket=get_metadata_bucket("failed", result=result),
                            )
                            save_results_manifest(config, results_manifest)
                            telegram.notify_ip_blocked(
                                current_network_label(config),
                                fatal_error,
                            )
                            telegram.notify_api_error(429, "API rate limit exceeded. Wait 1 hour.")
                            exit_code = 3
                            break

                        if response is not None and response.status_code == 403:
                            reason = "403 API error"
                            try:
                                data = api_payload or {}
                                error_code = data.get("error_code")
                                error_msg = data.get("error", "Unknown error")
                                if error_code == 3410:
                                    reason = "On Demand (платное видео)"
                                else:
                                    reason = f"403 API error ({error_code}): {error_msg}"

                                logger.info("Video %s skipped: %s", video_id, reason)
                                skipped_videos += 1
                                runtime_state.update_counts(
                                    i,
                                    successful_downloads,
                                    skipped_videos,
                                    failed_videos,
                                )
                                telegram.notify_video_skipped(video_id, reason, i, len(urls))
                            except Exception as exc:
                                logger.error("Error parsing 403 response: %s", exc)
                                skipped_videos += 1
                                runtime_state.update_counts(
                                    i,
                                    successful_downloads,
                                    skipped_videos,
                                    failed_videos,
                                )
                            checkpoint_resume_state(
                                config,
                                resume_state,
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                video_id,
                                "skipped_403",
                                len(urls),
                                source_signature,
                            )
                            metadata_path = save_video_metadata(
                                config,
                                video_dir,
                                video_id,
                                video_url,
                                None,
                                api_probe,
                                result,
                                "skipped",
                                reason,
                            )
                            update_results_manifest(
                                results_manifest,
                                i,
                                status="skipped",
                                reason=reason,
                                metadata_json=str(metadata_path),
                                storage_bucket=get_metadata_bucket("skipped", result=result),
                            )
                            save_results_manifest(config, results_manifest)
                            telegram.notify_progress(
                                i,
                                len(urls),
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                current_video_id=video_id,
                                current_stage="skipped",
                            )
                            consecutive_timeout_failures = 0
                            maybe_raise_controlled_restart(
                                config,
                                start_index,
                                i,
                                len(urls),
                                consecutive_timeout_failures,
                                logger,
                                video_id,
                            )
                            if i < len(urls):
                                delay = random.uniform(
                                    MIN_DELAY_BETWEEN_VIDEOS,
                                    MAX_DELAY_BETWEEN_VIDEOS,
                                )
                                logger.info("Delay before next video: %.1fs", delay)
                                time.sleep(delay)
                            continue

                        if response is not None and response.status_code == 404:
                            logger.info("Video %s not found (404), skipping", video_id)
                            skipped_videos += 1
                            runtime_state.update_counts(
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                            )
                            checkpoint_resume_state(
                                config,
                                resume_state,
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                video_id,
                                "skipped_404",
                                len(urls),
                                source_signature,
                            )
                            metadata_path = save_video_metadata(
                                config,
                                video_dir,
                                video_id,
                                video_url,
                                None,
                                api_probe,
                                result,
                                "skipped",
                                "404 not found",
                            )
                            update_results_manifest(
                                results_manifest,
                                i,
                                status="skipped",
                                reason="404 not found",
                                metadata_json=str(metadata_path),
                                storage_bucket=get_metadata_bucket("skipped", result=result),
                            )
                            save_results_manifest(config, results_manifest)
                            telegram.notify_progress(
                                i,
                                len(urls),
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                current_video_id=video_id,
                                current_stage="404 skip",
                            )
                            consecutive_timeout_failures = 0
                            maybe_raise_controlled_restart(
                                config,
                                start_index,
                                i,
                                len(urls),
                                consecutive_timeout_failures,
                                logger,
                                video_id,
                            )
                            if i < len(urls):
                                delay = random.uniform(
                                    MIN_DELAY_BETWEEN_VIDEOS,
                                    MAX_DELAY_BETWEEN_VIDEOS,
                                )
                                logger.info("Delay before next video: %.1fs", delay)
                                time.sleep(delay)
                            continue

                        json_data = api_payload or {}
                        owner_allows_download = json_data.get("privacy", {}).get("download")
                        api_has_download_link = bool(extract_best_api_download(json_data))

                        if owner_allows_download is False and not api_has_download_link:
                            logger.info(
                                "Video %s is not downloadable (privacy.download = false, no API download links)",
                                video_id,
                            )
                            skipped_videos += 1
                            runtime_state.update_counts(
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                            )
                            checkpoint_resume_state(
                                config,
                                resume_state,
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                video_id,
                                "skipped_privacy",
                                len(urls),
                                source_signature,
                            )
                            metadata_path = save_video_metadata(
                                config,
                                video_dir,
                                video_id,
                                video_url,
                                json_data,
                                api_probe,
                                result,
                                "skipped",
                                "privacy.download=false and no API download links",
                            )
                            update_results_manifest(
                                results_manifest,
                                i,
                                status="skipped",
                                reason="privacy.download=false and no API download links",
                                metadata_json=str(metadata_path),
                                downloadable=False,
                                storage_bucket=get_metadata_bucket("skipped", result=result),
                            )
                            save_results_manifest(config, results_manifest)
                            telegram.notify_progress(
                                i,
                                len(urls),
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                current_video_id=video_id,
                                current_stage="privacy skip",
                            )
                            consecutive_timeout_failures = 0
                            maybe_raise_controlled_restart(
                                config,
                                start_index,
                                i,
                                len(urls),
                                consecutive_timeout_failures,
                                logger,
                                video_id,
                            )
                            if i < len(urls):
                                delay = random.uniform(
                                    MIN_DELAY_BETWEEN_VIDEOS,
                                    MAX_DELAY_BETWEEN_VIDEOS,
                                )
                                logger.info("Delay before next video: %.1fs", delay)
                                time.sleep(delay)
                            continue

                        if api_has_download_link:
                            logger.info(
                                "Video %s has direct API download links, proceeding without relying on page button",
                                video_id,
                            )
                        elif owner_allows_download:
                            logger.info(
                                "Video %s is downloadable (privacy.download = true), checking page for Download button...",
                                video_id,
                            )
                        else:
                            logger.info(
                                "Video %s has inconclusive API metadata, checking page for Download button...",
                                video_id,
                            )

                        retry_attempts = int(config["settings"]["retry_attempts"])
                        video_deadline_ts = build_video_deadline(config["settings"])
                        result = None
                        for attempt in range(1, retry_attempts + 1):
                            runtime_state.touch(f"download attempt {attempt}", video_id)
                            result = download_video(
                                sb,
                                video_url,
                                video_id,
                                json_data,
                                video_dir,
                                logger,
                                config,
                                runtime_state,
                                download_session=download_session,
                                login_email=login_email,
                                login_password=login_password,
                                cloudflare_retry_count=attempt - 1,
                                video_deadline_ts=video_deadline_ts,
                            )
                            if result["success"]:
                                break
                            if result.get("skipped_by_policy"):
                                break
                            if attempt < retry_attempts:
                                remaining_retry_budget = remaining_deadline_seconds(video_deadline_ts)
                                if remaining_retry_budget is not None and remaining_retry_budget <= 0:
                                    logger.warning(
                                        "Overall video timeout budget exhausted for %s after attempt %d/%d",
                                        video_id,
                                        attempt,
                                        retry_attempts,
                                    )
                                    break
                                retry_delay = compute_retry_delay(config["settings"], attempt)
                                if remaining_retry_budget is not None:
                                    retry_delay = min(retry_delay, max(0.0, remaining_retry_budget))
                                if retry_delay <= 0:
                                    logger.warning(
                                        "Skipping retry delay for %s because no overall video time budget remains",
                                        video_id,
                                    )
                                    break
                                logger.warning(
                                    "Attempt %d/%d failed for %s: %s. Retrying in %.1fs",
                                    attempt,
                                    retry_attempts,
                                    video_id,
                                    result["error"],
                                    retry_delay,
                                )
                                time.sleep(retry_delay)

                        ensure_min_free_disk_space(
                            config,
                            stage="before persisting video result",
                            current_video_id=video_id,
                            logger=logger,
                            telegram=telegram,
                            runtime_state=runtime_state,
                        )

                        if result and result["success"]:
                            successful_downloads += 1
                            metadata_path = save_video_metadata(
                                config,
                                video_dir,
                                video_id,
                                video_url,
                                json_data,
                                api_probe,
                                result,
                                "downloaded",
                                "downloaded successfully",
                            )
                            runtime_state.update_counts(
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                            )
                            checkpoint_resume_state(
                                config,
                                resume_state,
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                video_id,
                                "downloaded",
                                len(urls),
                                source_signature,
                            )
                            update_results_manifest(
                                results_manifest,
                                i,
                                status="downloaded",
                                reason="downloaded successfully",
                                filename=result["filename"],
                                file_size_mb=round(result["file_size_mb"], 3),
                                metadata_json=str(metadata_path),
                                video_id=result.get("canonical_video_id") or video_id,
                                video_file=result.get("file_path"),
                                download_source=result.get("download_source"),
                                selected_quality=result.get("selected_quality"),
                                is_original=result.get("is_original"),
                                downloadable=result.get("downloadable"),
                                button_found=result.get("button_found"),
                                download_link_found=result.get("download_link_found"),
                                download_link=result.get("download_link"),
                                available_options_count=result.get("available_options_count"),
                                video_folder=str(Path(metadata_path).parent),
                                storage_bucket="downloaded",
                            )
                            save_results_manifest(config, results_manifest)
                            telegram.notify_video_downloaded(
                                video_id,
                                result["filename"],
                                result["file_size_mb"],
                                i,
                                len(urls),
                                successful_downloads,
                            )
                            consecutive_timeout_failures = 0
                        elif result and result.get("skipped_by_policy"):
                            skipped_videos += 1
                            reason = (
                                result.get("policy_reason")
                                or "downloadable but not original (skipped by policy)"
                            )
                            metadata_path = save_video_metadata(
                                config,
                                video_dir,
                                video_id,
                                video_url,
                                json_data,
                                api_probe,
                                result,
                                "skipped",
                                reason,
                            )
                            runtime_state.update_counts(
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                            )
                            checkpoint_resume_state(
                                config,
                                resume_state,
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                video_id,
                                "skipped_non_original_policy",
                                len(urls),
                                source_signature,
                            )
                            update_results_manifest(
                                results_manifest,
                                i,
                                status="skipped",
                                reason=reason,
                                metadata_json=str(metadata_path),
                                video_id=extract_canonical_video_id(video_id, json_data=json_data, result=result),
                                download_source=result.get("download_source"),
                                selected_quality=result.get("selected_quality"),
                                is_original=result.get("is_original"),
                                downloadable=result.get("downloadable"),
                                button_found=result.get("button_found"),
                                download_link_found=result.get("download_link_found"),
                                download_link=result.get("download_link"),
                                available_options_count=result.get("available_options_count"),
                                video_folder=str(Path(metadata_path).parent),
                                storage_bucket=get_metadata_bucket("skipped", result=result),
                            )
                            save_results_manifest(config, results_manifest)
                            telegram.notify_progress(
                                i,
                                len(urls),
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                current_video_id=video_id,
                                current_stage="non-original skip",
                            )
                            consecutive_timeout_failures = 0
                        else:
                            failed_videos += 1
                            error_message = result["error"] if result else "Unknown error"
                            failed_urls.append(
                                {
                                    "url": video_url,
                                    "error": error_message,
                                    "stage": "download",
                                }
                            )
                            metadata_path = save_video_metadata(
                                config,
                                video_dir,
                                video_id,
                                video_url,
                                json_data,
                                api_probe,
                                result or create_placeholder_result(),
                                "failed",
                                error_message,
                            )
                            runtime_state.update_counts(
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                            )
                            checkpoint_resume_state(
                                config,
                                resume_state,
                                i,
                                successful_downloads,
                                skipped_videos,
                                failed_videos,
                                video_id,
                                "failed_download",
                                len(urls),
                                source_signature,
                            )
                            update_results_manifest(
                                results_manifest,
                                i,
                                status="failed",
                                reason=error_message,
                                metadata_json=str(metadata_path),
                                video_id=extract_canonical_video_id(video_id, json_data=json_data, result=result),
                                download_source=result.get("download_source") if result else None,
                                selected_quality=result.get("selected_quality") if result else None,
                                is_original=result.get("is_original") if result else None,
                                downloadable=result.get("downloadable") if result else None,
                                button_found=result.get("button_found") if result else None,
                                download_link_found=result.get("download_link_found") if result else None,
                                download_link=result.get("download_link") if result else None,
                                available_options_count=result.get("available_options_count") if result else 0,
                                video_folder=str(Path(metadata_path).parent),
                                storage_bucket=get_metadata_bucket("failed", result=result),
                            )
                            save_results_manifest(config, results_manifest)
                            logger.error("Failed to download %s: %s", video_id, error_message)
                            if is_probable_ip_block(error_message):
                                telegram.notify_ip_blocked(
                                    current_network_label(config),
                                    error_message,
                                )
                            telegram.notify_error(
                                video_id,
                                error_message,
                                i,
                                len(urls),
                                stage="download",
                            )
                            if result and result.get("timeout_like"):
                                consecutive_timeout_failures += 1
                            elif is_timeout_like_message(error_message):
                                consecutive_timeout_failures += 1
                            else:
                                consecutive_timeout_failures = 0

                        telegram.notify_progress(
                            i,
                            len(urls),
                            successful_downloads,
                            skipped_videos,
                            failed_videos,
                            current_video_id=video_id,
                            current_stage=runtime_state.snapshot()["current_stage"],
                        )
                        maybe_raise_controlled_restart(
                            config,
                            start_index,
                            i,
                            len(urls),
                            consecutive_timeout_failures,
                            logger,
                            video_id,
                        )

                        if i < len(urls):
                            delay = random.uniform(
                                MIN_DELAY_BETWEEN_VIDEOS,
                                MAX_DELAY_BETWEEN_VIDEOS,
                            )
                            logger.info("Delay before next video: %.1fs", delay)
                            time.sleep(delay)

                    except Exception as exc:
                        failed_videos += 1
                        failed_urls.append(
                            {
                                "url": video_url,
                                "error": str(exc),
                                "stage": "api_check",
                            }
                        )
                        result = create_placeholder_result()
                        metadata_path = save_video_metadata(
                            config,
                            video_dir,
                            video_id,
                            video_url,
                            None,
                            {
                                "status_code": None,
                                "privacy_download": None,
                                "download_options_count": None,
                                "has_download_link": None,
                                "best_option": None,
                            },
                            result,
                            "failed",
                            str(exc),
                        )
                        runtime_state.update_counts(
                            i,
                            successful_downloads,
                            skipped_videos,
                            failed_videos,
                        )
                        checkpoint_resume_state(
                            config,
                            resume_state,
                            i,
                            successful_downloads,
                            skipped_videos,
                            failed_videos,
                            video_id,
                            "failed_api_check",
                            len(urls),
                            source_signature,
                        )
                        update_results_manifest(
                            results_manifest,
                            i,
                            status="failed",
                            reason=str(exc),
                            metadata_json=str(metadata_path),
                            video_folder=str(Path(metadata_path).parent),
                            storage_bucket=get_metadata_bucket("failed", result=result),
                        )
                        save_results_manifest(config, results_manifest)
                        logger.error("Unexpected error processing %s: %s", video_id, exc)
                        telegram.notify_error(
                            video_id,
                            str(exc),
                            i,
                            len(urls),
                            stage="api_check",
                        )
                        if is_timeout_like_message(exc):
                            consecutive_timeout_failures += 1
                        else:
                            consecutive_timeout_failures = 0
                        maybe_raise_controlled_restart(
                            config,
                            start_index,
                            i,
                            len(urls),
                            consecutive_timeout_failures,
                            logger,
                            video_id,
                        )
                    finally:
                        close_response_quietly(response)

    except ControlledWorkerRestart as exc:
        controlled_restart_requested = True
        controlled_restart_reason = str(exc)
        exit_code = CONTROLLED_RESTART_EXIT_CODE
        logger.warning("Worker requested controlled restart: %s", exc)
    except LowDiskSpaceError as exc:
        fatal_error = str(exc)
        fatal_category = "low_disk_space"
        fatal_disk_snapshot = getattr(exc, "snapshot", {}) or {}
        exit_code = LOW_DISK_EXIT_CODE
        logger.exception("Low disk space: %s", exc)
        telegram.notify_custom(
            "Недостаточно свободного места",
            [
                f"error: <code>{str(exc)[:350]}</code>",
                f"free_gb: <code>{fatal_disk_snapshot.get('free_gb')}</code>",
                f"threshold_gb: <code>{fatal_disk_snapshot.get('threshold_gb')}</code>",
                f"check_path: <code>{fatal_disk_snapshot.get('path')}</code>",
                f"config: <code>{config['_meta']['config_path']}</code>",
                f"resume_next_index: <code>{resume_state.get('next_index')}</code>",
            ],
        )
    except Exception as exc:
        fatal_error = str(exc)
        if should_recycle_after_fatal_exception(
            config,
            start_index,
            successful_downloads,
            skipped_videos,
            failed_videos,
        ):
            controlled_restart_requested = True
            controlled_restart_reason = (
                f"fatal runtime after partial progress: {str(exc)[:300]}"
            )
            fatal_error = None
            exit_code = CONTROLLED_RESTART_EXIT_CODE
            logger.exception(
                "Fatal runtime error after partial progress; requesting controlled restart: %s",
                exc,
            )
            telegram.notify_custom(
                "Worker requested recycle after fatal runtime",
                [
                    f"error: <code>{str(exc)[:350]}</code>",
                    f"config: <code>{config['_meta']['config_path']}</code>",
                    f"resume_next_index: <code>{resume_state.get('next_index')}</code>",
                ],
            )
        else:
            exit_code = 1
            logger.exception("Fatal runtime error: %s", exc)
            telegram.notify_custom(
                "Процесс аварийно завершился",
                [
                    f"error: <code>{str(exc)[:350]}</code>",
                    f"config: <code>{config['_meta']['config_path']}</code>",
                ],
            )
    finally:
        watchdog.stop()
        watchdog.join(timeout=10)
        close_session_quietly(api_session)
        close_session_quietly(download_session)

    total_processed = successful_downloads + skipped_videos + failed_videos
    if config["resume"]["enabled"] and total_processed >= len(urls):
        checkpoint_resume_state(
            config,
            resume_state,
            len(urls),
            successful_downloads,
            skipped_videos,
            failed_videos,
            resume_state.get("last_video_id"),
            resume_state.get("last_status") or "completed",
            len(urls),
            source_signature,
        )

    logger.info("\n%s", "=" * 80)
    logger.info("DOWNLOAD PROCESS COMPLETED")
    logger.info("%s", "=" * 80)
    logger.info("Successfully downloaded: %d", successful_downloads)
    logger.info("Skipped / not downloaded by policy: %d", skipped_videos)
    logger.info("Failed: %d", failed_videos)
    logger.info("Total processed: %d", total_processed)
    logger.info("%s\n", "=" * 80)

    failed_file = Path(config["files"]["failed_downloads"])
    if failed_urls:
        failed_file.parent.mkdir(parents=True, exist_ok=True)
        with open(failed_file, "w", encoding="utf-8") as f:
            json.dump(failed_urls, f, indent=2, ensure_ascii=False)
        logger.info("Failed downloads saved to %s", failed_file)
    elif failed_file.exists():
        failed_file.unlink()
        logger.info("Removed stale failed downloads file %s", failed_file)

    summary = {
        "worker_name": worker_name,
        "config_path": config["_meta"]["config_path"],
        "browser_mode": browser_mode,
        "total": total_processed,
        "total_processed": total_processed,
        "downloaded": successful_downloads,
        "skipped": skipped_videos,
        "failed": failed_videos,
        "failed_urls": len(failed_urls),
        "exit_code": exit_code,
        "fatal_error": fatal_error,
        "fatal_category": fatal_category,
        "restart_requested": controlled_restart_requested,
        "restart_reason": controlled_restart_reason,
        "log_file": config["files"]["log_file"],
        "videos_dir": config["files"]["videos_dir"],
        "jsons_dir": config["files"]["jsons_dir"],
        "failed_downloads": config["files"]["failed_downloads"],
        "results_file": config["files"]["results_file"],
        "storage_layout": {
            "downloaded": "per_video_directory",
            "not_downloaded": "flat_json",
            "no_links": "flat_json",
        },
        "resume_enabled": config["resume"]["enabled"],
        "resume_state_file": config["resume"]["state_file"],
        "resume_next_index": resume_state["next_index"],
        "resume_completed": resume_state["completed"],
        "resume_source_signature": resume_state.get("source_signature"),
        "disk_check_path": fatal_disk_snapshot.get("path") if fatal_disk_snapshot else None,
        "disk_free_gb": fatal_disk_snapshot.get("free_gb") if fatal_disk_snapshot else None,
        "disk_threshold_gb": fatal_disk_snapshot.get("threshold_gb") if fatal_disk_snapshot else None,
    }
    write_summary(config, summary)
    telegram.notify_finish(summary, wait=True)
    telegram.shutdown(timeout=10)
    logger.info("Summary saved to %s", config["files"]["summary_file"])
    logger.info("Script finished with exit code %d", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

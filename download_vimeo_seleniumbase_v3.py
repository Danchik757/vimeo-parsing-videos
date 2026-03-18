"""Vimeo downloader with configurable browser mode and worker-aware logging."""

import argparse
import hashlib
import json
import logging
import os
import platform
import random
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import vimeo
from seleniumbase import SB
from selenium.webdriver.common.action_chains import ActionChains

from telegram_notifier import TelegramNotifier
from vimeo_cdp_helpers import (
    _extract_download_options_from_scope,
    choose_best_download_option,
    click_download_button,
    extract_best_api_download,
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


def resolve_path(config_dir, value):
    path = Path(value)
    if path.is_absolute():
        return path
    return (config_dir / path).resolve()


def load_config(config_path):
    config_path = Path(config_path).resolve()
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    config_dir = config_path.parent
    config["_meta"] = {
        "config_path": str(config_path),
        "config_dir": str(config_dir),
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
    settings.setdefault("javascript_wait_time", 15)
    settings.setdefault("download_timeout", 600)
    settings.setdefault("connect_timeout", 30)
    settings.setdefault("download_chunk_size_kb", 1024)
    settings.setdefault("download_progress_log_seconds", 15)
    settings.setdefault("download_only_original", False)

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

    batches = config.setdefault("batches", {})
    batches.setdefault("enabled", False)
    batches.setdefault("batch_size", 10000)
    batches.setdefault("auto_advance", True)
    batches.setdefault("reuse_existing_shards", True)
    batches.setdefault("stop_on_batch_error", True)
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

    return config


def setup_logger(log_file):
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("vimeo_downloader")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_path)
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
        self.enabled = bool(watchdog_cfg.get("enabled", True))
        self.stall_after = int(watchdog_cfg.get("stall_alert_after_seconds", 1800))
        self.repeat_every = int(watchdog_cfg.get("repeat_alert_every_seconds", 1800))
        self.heartbeat_every = int(watchdog_cfg.get("heartbeat_every_seconds", 900))
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


def parse_content_range_total(header_value):
    if not header_value or "/" not in header_value:
        return None
    total_part = header_value.rsplit("/", 1)[-1].strip()
    if not total_part.isdigit():
        return None
    return int(total_part)


def extract_video_id(video_url):
    parsed = urlparse(video_url)
    path = parsed.path.rstrip("/")
    if not path:
        return video_url.rstrip("/").split("/")[-1]
    return path.split("/")[-1]


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


def download_file(url, local_filename, runtime_state, logger, config, video_id):
    settings = config["settings"]
    resume = config["resume"]
    chunk_size = int(settings.get("download_chunk_size_kb", 1024)) * 1024
    connect_timeout = int(settings.get("connect_timeout", 30))
    read_timeout = int(settings.get("download_timeout", 600))
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

    while True:
        with requests.get(
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
            "is_original": False if config is not None else None,
        }

    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        if logger:
            logger.warning("Failed to read existing metadata %s: %s", metadata_path, exc)
        return {}

    download = payload.setdefault("_download", {})
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
        "is_original": is_original,
    }


def get_video_storage_dir(video_dir, video_id):
    return Path(video_dir) / "downloaded" / str(video_id)


def get_download_status_root(video_dir, status):
    bucket = "downloaded" if status == "downloaded" else "not_downloaded"
    return Path(video_dir) / bucket


def get_video_metadata_path(video_dir, video_id, status="downloaded"):
    storage_dir = get_download_status_root(video_dir, status) / str(video_id)
    return storage_dir / f"{video_id}.json"


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
        if candidate is None:
            continue
        candidate = str(candidate).strip()
        if candidate.isdigit():
            return candidate

    return str(video_id)


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


def build_page_probe(result):
    return {
        "visited": result.get("page_visited", False),
        "page_url": result.get("page_url"),
        "page_title": result.get("page_title"),
        "cloudflare_detected": result.get("cloudflare_detected", False),
        "download_button_found": result.get("button_found"),
        "download_link_found": result.get("download_link_found", False),
        "available_options_count": result.get("available_options_count", 0),
        "available_options": result.get("available_options") or [],
        "best_option": normalize_download_option(result.get("page_best_option")),
        "has_original_option": result.get("has_original_option"),
        "error": result.get("probe_error"),
        "context": result.get("page_context"),
    }


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
    metadata_path = get_video_metadata_path(video_dir, canonical_video_id, status=status)
    storage_dir = metadata_path.parent
    storage_dir.mkdir(parents=True, exist_ok=True)

    file_path = result.get("file_path")
    filename = result.get("filename")
    if not file_path and filename:
        file_path = str(storage_dir / filename)

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
        "_page_probe": build_page_probe(result),
        "_storage": {
            "bucket": "downloaded" if status == "downloaded" else "not_downloaded",
            "video_dir": str(storage_dir),
            "metadata_path": str(metadata_path),
            "per_video_directory": True,
        },
        "vimeo_video": json_data if isinstance(json_data, dict) else None,
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
    cloudflare_retry_count=0,
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
        "canonical_video_id": None,
        "file_path": None,
    }

    try:
        api_download = extract_best_api_download(json_data)
        result["api_best_option"] = normalize_download_option(api_download)
        runtime_state.touch("checking direct api download", video_id)
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
            sb.open(video_url)
            result["page_visited"] = True

            initial_wait = random.uniform(PAGE_LOAD_WAIT_MIN, PAGE_LOAD_WAIT_MAX)
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

            if check_if_cloudflare_blocked(sb, logger):
                result["cloudflare_detected"] = True
                cloudflare_timeout = random.uniform(
                    CLOUDFLARE_TIMEOUT_MIN,
                    CLOUDFLARE_TIMEOUT_MAX,
                )
                cloudflare_timeout += cloudflare_retry_count * 10

                runtime_state.touch("waiting cloudflare auto-bypass", video_id)
                logger.warning(
                    "Cloudflare detected, waiting %.0fs for auto-bypass...",
                    cloudflare_timeout,
                )
                sb.sleep(cloudflare_timeout)

                if check_if_cloudflare_blocked(sb, logger):
                    result["error"] = (
                        f"Cloudflare Turnstile not bypassed after "
                        f"{cloudflare_timeout:.0f}s"
                    )
                    logger.error(result["error"])
                    try:
                        page_source = sb.get_page_source()
                        debug_file = Path(config["files"]["logs_dir"]) / (
                            f"cloudflare_fail_{video_id}.html"
                        )
                        with open(debug_file, "w", encoding="utf-8") as f:
                            f.write(page_source)
                        logger.info("Saved failed HTML to %s", debug_file)
                    except Exception:
                        pass
                    return result

                logger.info("Cloudflare bypassed successfully")

            runtime_state.touch("simulating mouse movement", video_id)
            simulate_mouse_movement(sb, logger)

            runtime_state.touch("simulating page scroll", video_id)
            simulate_page_scroll(sb, logger)

            reading_time = random.uniform(READING_TIME_MIN, READING_TIME_MAX)
            runtime_state.touch("simulating reading", video_id)
            logger.info("Simulating reading (%.1fs)...", reading_time)
            sb.sleep(reading_time)

            js_timeout = max(10, int(config["settings"]["javascript_wait_time"]))
            runtime_state.touch("clicking download button", video_id)
            try:
                click_download_button(sb, timeout=js_timeout, logger=logger)
                result["button_found"] = True
            except Exception as exc:
                result["button_found"] = False
                result["probe_error"] = "Download button not found"
                if api_non_original_fallback:
                    result["skipped_by_policy"] = True
                    result["policy_reason"] = (
                        "downloadable via api but not original; page probe failed: "
                        "Download button not found"
                    )
                    logger.info(
                        "Page probe for %s did not find button, but API already confirmed non-original downloadability",
                        video_id,
                    )
                    return result
                result["error"] = "Download button not found"
                logger.error("Download button not found for %s: %s", video_id, exc)
                try:
                    page_source = sb.get_page_source()
                    debug_file = Path(config["files"]["logs_dir"]) / (
                        f"no_button_{video_id}.html"
                    )
                    with open(debug_file, "w", encoding="utf-8") as f:
                        f.write(page_source)
                    logger.info("Saved HTML (no button) to %s", debug_file)
                except Exception:
                    pass
                return result

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
                result["probe_error"] = (
                    "No download option found in modal"
                    if result["available_options_count"] > 0
                    else "Download modal not found"
                )
                if api_non_original_fallback:
                    result["skipped_by_policy"] = True
                    result["policy_reason"] = (
                        "downloadable via api but not original; page probe did not expose original "
                        f"({result['probe_error']})"
                    )
                    logger.info(
                        "Page probe for %s did not expose original; keeping API non-original metadata only",
                        video_id,
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
                "Skipping download for %s because best available option is not original (%s)",
                video_id,
                result["selected_quality"] or "unknown quality",
            )
            return result

        canonical_video_id = extract_canonical_video_id(video_id, json_data=json_data, result=result)
        result["canonical_video_id"] = canonical_video_id
        ext = detect_extension(download_link)
        filename = f"{canonical_video_id}{ext}"
        local_path = get_video_storage_dir(video_dir, canonical_video_id) / filename

        runtime_state.touch("downloading file", video_id)
        logger.info("Starting download to %s", local_path)
        file_size_mb = download_file(
            download_link,
            local_path,
            runtime_state,
            logger,
            config,
            video_id,
        )

        result["success"] = True
        result["file_size_mb"] = file_size_mb
        result["filename"] = filename
        result["file_path"] = str(local_path)
        logger.info("Successfully downloaded %s (%.1f MB)", video_id, file_size_mb)

    except Exception as exc:
        result["error"] = str(exc)
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
    if xvfb and platform.system().lower() != "linux":
        logger.warning("xvfb requested on non-Linux platform; disabling xvfb")
        xvfb = False

    return {
        "uc": bool(browser.get("uc", True)),
        "headless": bool(browser.get("headless", False)),
        "xvfb": xvfb,
        "disable_csp": bool(browser.get("disable_csp", True)),
        "block_images": bool(browser.get("block_images", False)),
        "incognito": bool(browser.get("incognito", False)),
        "chromium_arg": browser.get(
            "chromium_arg",
            "--disable-blink-features=AutomationControlled",
        ),
    }


def write_summary(config, summary):
    summary_path = Path(config["files"]["summary_file"])
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)


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
                "selected_quality": None,
                "is_original": None,
                "downloadable": None,
                "button_found": None,
                "download_link_found": None,
                "available_options_count": 0,
                "video_folder": None,
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
    per_video_dir = get_video_storage_dir(video_dir, video_id)
    search_roots = []
    if per_video_dir.exists():
        search_roots.append(per_video_dir)
    search_roots.append(video_dir)

    seen = set()
    for root in search_roots:
        pattern = f"{video_id}*"
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


def resolve_existing_metadata_path(video_dir, json_dir, video_id):
    for status in ("downloaded", "not_downloaded"):
        preferred = get_video_metadata_path(video_dir, video_id, status=status)
        if preferred.exists():
            return preferred
    legacy = Path(json_dir) / f"{video_id}.json"
    if legacy.exists():
        return legacy
    return get_video_metadata_path(video_dir, video_id, status="downloaded")


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

    client = vimeo.VimeoClient(
        token=config["vimeo_api"]["token"],
        key=config["vimeo_api"]["client_id"],
        secret=config["vimeo_api"]["secret"],
    )
    logger.info("Vimeo client initialized")

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
    exit_code = 0

    sb_kwargs = build_sb_kwargs(config, logger)
    logger.info("SeleniumBase args: %s", sb_kwargs)

    try:
        if start_index >= len(urls):
            logger.info(
                "Resume state indicates all %d URLs are already processed. Exiting without browser launch.",
                len(urls),
            )
        else:
            with SB(**sb_kwargs) as sb:
                logger.info("SeleniumBase browser started")

                for i, video_url in enumerate(urls[start_index:], start=start_index + 1):
                    video_id = extract_video_id(video_url)
                    if config["resume"]["skip_completed_files"]:
                        existing_file = find_existing_completed_file(video_dir, video_id)
                        if existing_file is not None:
                            metadata_path = resolve_existing_metadata_path(video_dir, json_dir, video_id)
                            existing_metadata = load_existing_download_metadata(
                                metadata_path,
                                config=config,
                                logger=logger,
                            )
                            if not should_skip_existing_file(existing_file, existing_metadata, config):
                                logger.info(
                                    "Existing file for %s is not considered complete under original-only policy; reprocessing",
                                    video_id,
                                )
                            else:
                                successful_downloads += 1
                                runtime_state.touch("already downloaded", video_id)
                                runtime_state.update_counts(
                                    i,
                                    successful_downloads,
                                    skipped_videos,
                                    failed_videos,
                                )
                                logger.info(
                                    "Skipping %s because completed file already exists: %s",
                                    video_id,
                                    existing_file,
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
                                    reason="file already existed before resume",
                                    filename=existing_file.name,
                                    file_size_mb=round(existing_file.stat().st_size / (1024 * 1024), 3),
                                    metadata_json=str(metadata_path),
                                    video_file=str(existing_file),
                                    download_source=existing_metadata.get("download_source"),
                                    selected_quality=existing_metadata.get("selected_quality"),
                                    is_original=existing_metadata.get("is_original"),
                                    downloadable=True,
                                    download_link_found=True,
                                    video_folder=str(existing_file.parent),
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

                    try:
                        logger.info("Checking video %s via API...", video_id)
                        response = client.get(f"https://api.vimeo.com/videos/{video_id}")
                        api_payload = api_error_payload(response)
                        api_probe = build_api_probe(response.status_code, api_payload)
                        result = create_placeholder_result()

                        if response.status_code == 401:
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
                            )
                            save_results_manifest(config, results_manifest)
                            telegram.notify_api_error(401, "Invalid API token")
                            exit_code = 2
                            break

                        if response.status_code == 429:
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
                            )
                            save_results_manifest(config, results_manifest)
                            telegram.notify_ip_blocked(
                                current_network_label(config),
                                fatal_error,
                            )
                            telegram.notify_api_error(429, "API rate limit exceeded. Wait 1 hour.")
                            exit_code = 3
                            break

                        if response.status_code == 403:
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
                            if i < len(urls):
                                delay = random.uniform(
                                    MIN_DELAY_BETWEEN_VIDEOS,
                                    MAX_DELAY_BETWEEN_VIDEOS,
                                )
                                logger.info("Delay before next video: %.1fs", delay)
                                time.sleep(delay)
                            continue

                        if response.status_code == 404:
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
                        retry_delay = int(config["settings"]["retry_delay"])
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
                                cloudflare_retry_count=attempt - 1,
                            )
                            if result["success"]:
                                break
                            if attempt < retry_attempts:
                                logger.warning(
                                    "Attempt %d/%d failed for %s: %s. Retrying in %ss",
                                    attempt,
                                    retry_attempts,
                                    video_id,
                                    result["error"],
                                    retry_delay,
                                )
                                time.sleep(retry_delay)

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
                                available_options_count=result.get("available_options_count"),
                                video_folder=str(Path(metadata_path).parent),
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
                                available_options_count=result.get("available_options_count"),
                                video_folder=str(Path(metadata_path).parent),
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
                                available_options_count=result.get("available_options_count") if result else 0,
                                video_folder=str(Path(metadata_path).parent),
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

                        telegram.notify_progress(
                            i,
                            len(urls),
                            successful_downloads,
                            skipped_videos,
                            failed_videos,
                            current_video_id=video_id,
                            current_stage=runtime_state.snapshot()["current_stage"],
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

    except Exception as exc:
        fatal_error = str(exc)
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
        "downloaded": successful_downloads,
        "skipped": skipped_videos,
        "failed": failed_videos,
        "failed_urls": len(failed_urls),
        "exit_code": exit_code,
        "fatal_error": fatal_error,
        "log_file": config["files"]["log_file"],
        "videos_dir": config["files"]["videos_dir"],
        "jsons_dir": config["files"]["jsons_dir"],
        "failed_downloads": config["files"]["failed_downloads"],
        "results_file": config["files"]["results_file"],
        "per_video_directory_storage": True,
        "resume_enabled": config["resume"]["enabled"],
        "resume_state_file": config["resume"]["state_file"],
        "resume_next_index": resume_state["next_index"],
        "resume_completed": resume_state["completed"],
        "resume_source_signature": resume_state.get("source_signature"),
    }
    write_summary(config, summary)
    telegram.notify_finish(summary, wait=True)
    telegram.shutdown(timeout=10)
    logger.info("Summary saved to %s", config["files"]["summary_file"])
    logger.info("Script finished with exit code %d", exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

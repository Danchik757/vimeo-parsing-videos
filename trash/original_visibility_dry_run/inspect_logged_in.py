"""Inspect Vimeo download visibility in a logged-in session without downloading files."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from seleniumbase import SB

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from download_vimeo_seleniumbase_v3 import (
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT as DOWNLOADER_PROJECT_ROOT,
    build_sb_kwargs,
    check_if_cloudflare_blocked,
    extract_video_id,
    load_config,
    setup_logger,
)
from vimeo_cdp_helpers import (
    _extract_download_options_from_scope,
    choose_best_download_option,
    click_download_button,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Inspect which Vimeo videos expose download/original options in a logged-in session"
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to config JSON",
    )
    return parser.parse_args()


def ensure_output_paths(config):
    files_cfg = config.setdefault("files", {})
    logs_dir = Path(files_cfg.get("logs_dir", DOWNLOADER_PROJECT_ROOT / "output" / "original_visibility"))
    logs_dir.mkdir(parents=True, exist_ok=True)

    files_cfg.setdefault("log_file", str(logs_dir / "download.log"))
    files_cfg.setdefault("summary_file", str(logs_dir / "summary.json"))
    files_cfg.setdefault("results_file", str(logs_dir / "results_manifest.json"))


def require_login_credentials():
    email = os.environ.get("VIMEO_EMAIL", "").strip()
    password = os.environ.get("VIMEO_PASSWORD", "")
    if not email or not password:
        raise SystemExit(
            "VIMEO_EMAIL and VIMEO_PASSWORD environment variables are required "
            "for logged-in original visibility inspection"
        )
    return email, password


def normalize_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for option in options or []:
        normalized.append(
            {
                "text": option.get("text"),
                "href": option.get("href"),
                "quality": option.get("quality"),
                "rendition": option.get("rendition"),
                "width": option.get("width"),
                "height": option.get("height"),
            }
        )
    return normalized


def has_original_option(options: list[dict[str, Any]]) -> bool:
    for option in options or []:
        haystack = " ".join(
            str(option.get(key, "")) for key in ("text", "quality", "rendition")
        ).lower()
        if "original" in haystack or "source" in haystack:
            return True
    return False


def collect_modal_options(sb, timeout=12):
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


def login_to_vimeo(sb, email, password, logger):
    logger.info("Opening Vimeo login page")
    sb.open("https://vimeo.com/log_in")
    sb.sleep(3)

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

    logger.info("Waiting for login to complete")
    sb.sleep(8)
    current_url = ""
    try:
        current_url = sb.get_current_url()
    except Exception:
        pass
    if "log_in" in current_url:
        raise RuntimeError(f"Login appears incomplete, still on {current_url}")
    logger.info("Logged in successfully")


def save_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def default_results_manifest(config, urls):
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    items = []
    for index, url in enumerate(urls):
        items.append(
            {
                "index": index,
                "url": url,
                "video_id": extract_video_id(url),
                "status": "pending",
                "downloadable": None,
                "has_original": None,
                "download_button_found": None,
                "options_count": 0,
                "best_option": None,
                "error": None,
                "cloudflare_blocked": False,
                "session_relogin": False,
                "processed_at": None,
            }
        )
    return {
        "generated_at": generated_at,
        "updated_at": generated_at,
        "worker_name": config.get("runtime", {}).get("worker_name"),
        "source_json": config["files"]["source_json"],
        "total_urls": len(urls),
        "items": items,
    }


def update_results_manifest(manifest, index, row):
    item = manifest["items"][index]
    item.update(
        {
            "status": row["status"],
            "downloadable": row["downloadable"],
            "has_original": row["has_original"],
            "download_button_found": row["download_button_found"],
            "options_count": row["options_count"],
            "best_option": row["best_option"],
            "error": row["error"],
            "cloudflare_blocked": row["cloudflare_blocked"],
            "session_relogin": row["session_relogin"],
            "processed_at": row["processed_at"],
        }
    )
    manifest["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def is_login_page(current_url, title):
    haystack = f"{current_url} {title}".lower()
    return "log_in" in haystack or "login" in haystack


def inspect_url(sb, url, logger, email, password):
    row = {
        "url": url,
        "video_id": extract_video_id(url),
        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "page_title": None,
        "page_url": None,
        "download_button_found": False,
        "options": [],
        "options_count": 0,
        "best_option": None,
        "downloadable": False,
        "has_original": False,
        "cloudflare_blocked": False,
        "session_relogin": False,
        "error": None,
        "status": "unknown",
    }

    try:
        sb.open(url)
        sb.sleep(random.uniform(2.0, 4.0))

        current_url = ""
        title = ""
        try:
            current_url = sb.get_current_url()
        except Exception:
            pass
        try:
            title = sb.get_title() or ""
        except Exception:
            pass
        row["page_url"] = current_url or url
        row["page_title"] = title or None

        if is_login_page(current_url, title):
            logger.warning("Session appears logged out while opening %s; re-authenticating", url)
            login_to_vimeo(sb, email, password, logger)
            row["session_relogin"] = True
            sb.open(url)
            sb.sleep(random.uniform(2.0, 4.0))
            try:
                row["page_url"] = sb.get_current_url()
            except Exception:
                row["page_url"] = url
            try:
                row["page_title"] = sb.get_title() or row["page_title"]
            except Exception:
                pass

        if check_if_cloudflare_blocked(sb, logger):
            wait_seconds = random.uniform(40.0, 60.0)
            logger.info("Cloudflare detected for %s, waiting %.0fs for auto-bypass", row["video_id"], wait_seconds)
            sb.sleep(wait_seconds)
            if check_if_cloudflare_blocked(sb, logger):
                row["cloudflare_blocked"] = True
                row["status"] = "cloudflare_blocked"
                row["error"] = "cloudflare_blocked"
                return row

        click_download_button(sb, timeout=12, logger=logger)
        row["download_button_found"] = True
        options = collect_modal_options(sb, timeout=12)
        row["options"] = normalize_options(options)
        row["options_count"] = len(options)
        best = choose_best_download_option(options)
        row["best_option"] = normalize_options([best])[0] if best else None
        row["downloadable"] = bool(best or options)
        row["has_original"] = has_original_option(options)

        if row["has_original"]:
            row["status"] = "downloadable_original"
        elif row["downloadable"]:
            row["status"] = "downloadable_non_original"
        else:
            row["status"] = "no_download_options"
            row["error"] = "no_download_options"
        return row
    except Exception as exc:
        row["error"] = str(exc)
        message = str(exc)
        if "No download option found in modal" in message:
            row["status"] = "no_download_options"
        elif "Download modal not found" in message:
            row["status"] = "download_modal_not_found"
        elif "No usable download links found" in message:
            row["status"] = "no_usable_download_links"
        else:
            row["status"] = "page_error"
        return row


def summarize_manifest(manifest):
    counter = Counter(item.get("status", "unknown") for item in manifest.get("items", []))
    total_urls = len(manifest.get("items", []))
    downloadable = counter.get("downloadable_original", 0) + counter.get("downloadable_non_original", 0)
    has_original = counter.get("downloadable_original", 0)
    processed = total_urls - counter.get("pending", 0)
    return {
        "worker_name": manifest.get("worker_name"),
        "total_urls": total_urls,
        "processed": processed,
        "downloadable": downloadable,
        "has_original": has_original,
        "without_original": counter.get("downloadable_non_original", 0),
        "cloudflare_blocked": counter.get("cloudflare_blocked", 0),
        "download_modal_not_found": counter.get("download_modal_not_found", 0),
        "no_download_options": counter.get("no_download_options", 0),
        "no_usable_download_links": counter.get("no_usable_download_links", 0),
        "page_error": counter.get("page_error", 0),
        "status_counts": dict(counter),
    }


def main():
    args = parse_args()
    config = load_config(args.config)
    ensure_output_paths(config)
    email, password = require_login_credentials()

    logger = setup_logger(config["files"]["log_file"])
    worker_name = config.get("runtime", {}).get("worker_name", "worker-01")

    with open(config["files"]["source_json"], "r", encoding="utf-8") as f:
        urls = json.load(f)
    if config["settings"]["test_mode"]:
        urls = urls[: int(config["settings"]["test_limit"])]

    logger.info("=" * 80)
    logger.info("VIMEO ORIGINAL VISIBILITY INSPECTOR")
    logger.info("Worker: %s", worker_name)
    logger.info("Config: %s", config["_meta"]["config_path"])
    logger.info("Authenticated session: %s", True)
    logger.info("Processing %d videos", len(urls))
    logger.info("=" * 80)

    results_manifest = default_results_manifest(config, urls)
    save_json(config["files"]["results_file"], results_manifest)

    sb_kwargs = build_sb_kwargs(config, logger)
    with SB(**sb_kwargs) as sb:
        login_to_vimeo(sb, email, password, logger)

        for index, url in enumerate(urls, start=1):
            video_id = extract_video_id(url)
            logger.info("[%d/%d] Inspecting video %s", index, len(urls), video_id)
            logger.info("URL: %s", url)
            row = inspect_url(sb, url, logger, email, password)
            logger.info(
                "Result for %s: status=%s downloadable=%s has_original=%s options=%d relogin=%s",
                video_id,
                row["status"],
                row["downloadable"],
                row["has_original"],
                row["options_count"],
                row["session_relogin"],
            )
            update_results_manifest(results_manifest, index - 1, row)
            save_json(config["files"]["results_file"], results_manifest)
            if index < len(urls):
                delay = random.uniform(2.0, 5.0)
                logger.info("Delay before next video: %.1fs", delay)
                sb.sleep(delay)

    summary = summarize_manifest(results_manifest)
    summary["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary["config_path"] = config["_meta"]["config_path"]
    summary["source_json"] = config["files"]["source_json"]
    summary["results_file"] = config["files"]["results_file"]
    save_json(config["files"]["summary_file"], summary)

    logger.info("=" * 80)
    logger.info("INSPECTION COMPLETED")
    logger.info("downloadable: %d", summary["downloadable"])
    logger.info("has_original: %d", summary["has_original"])
    logger.info("without_original: %d", summary["without_original"])
    logger.info("cloudflare_blocked: %d", summary["cloudflare_blocked"])
    logger.info("page_error: %d", summary["page_error"])
    logger.info("Summary saved to %s", config["files"]["summary_file"])
    logger.info("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

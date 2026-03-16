"""Compare Vimeo download options in anonymous and logged-in sessions.

This script does not download video files. It only inspects the available
download options visible on the Vimeo page and via the Vimeo API.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import vimeo
from seleniumbase import SB

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from download_vimeo_seleniumbase_v3 import (
    DEFAULT_CONFIG_PATH,
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
    extract_best_api_download,
)


OUTPUT_ROOT = PROJECT_ROOT / "output" / "original_visibility_tests"
REQUIRED_URL = "https://vimeo.com/390227235"


def parse_args():
    parser = argparse.ArgumentParser(description="Compare anonymous vs logged-in Vimeo download options.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config JSON")
    parser.add_argument(
        "--source-json",
        default=str(PROJECT_ROOT / "need_parse_unique.json"),
        help="Path to source JSON with Vimeo URLs",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=50,
        help="Number of videos to compare",
    )
    parser.add_argument(
        "--candidate-scan-limit",
        type=int,
        default=500,
        help="How many URLs from the source list to scan via API when building the sample",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Optional output directory. Defaults to output/original_visibility_tests/<timestamp>",
    )
    return parser.parse_args()


def has_original_option(options: list[dict[str, Any]]) -> bool:
    for option in options or []:
        haystack = " ".join(
            str(option.get(key, "")) for key in ("text", "quality", "rendition")
        ).lower()
        if "original" in haystack or "source" in haystack:
            return True
    return False


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


def collect_page_options(sb, url, logger, timeout=12):
    result = {
        "page_url": url,
        "page_title": None,
        "cloudflare_blocked": False,
        "download_button_found": False,
        "options": [],
        "best_option": None,
        "error": None,
    }

    try:
        sb.open(url)
        sb.sleep(3)
        try:
            result["page_title"] = sb.get_title()
        except Exception:
            pass

        if check_if_cloudflare_blocked(sb, logger):
            result["cloudflare_blocked"] = True
            result["error"] = "cloudflare_blocked"
            return result

        click_download_button(sb, timeout=timeout, logger=logger)
        result["download_button_found"] = True
        options = collect_modal_options(sb, timeout=timeout)
        result["options"] = normalize_options(options)
        best = choose_best_download_option(options)
        result["best_option"] = normalize_options([best])[0] if best else None
    except Exception as exc:
        result["error"] = str(exc)

    return result


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
            sb.type(selector, email)
            break
        except Exception:
            continue
    else:
        raise RuntimeError("Email input not found on Vimeo login page")

    for selector in password_selectors:
        try:
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
    current_url = sb.get_current_url()
    if "log_in" in current_url:
        raise RuntimeError(f"Login appears incomplete, still on {current_url}")

    logger.info("Logged in successfully")


def build_sample_urls(config, source_json, sample_count, candidate_scan_limit, logger):
    with open(source_json, "r", encoding="utf-8") as f:
        source_urls = json.load(f)

    selected = []
    seen = set()

    def add(url):
        if url and url not in seen:
            selected.append(url)
            seen.add(url)

    add(REQUIRED_URL)

    client = vimeo.VimeoClient(
        token=config["vimeo_api"]["token"],
        key=config["vimeo_api"]["client_id"],
        secret=config["vimeo_api"]["secret"],
    )

    scanned = 0
    for url in source_urls:
        if len(selected) >= sample_count:
            break
        if url == REQUIRED_URL:
            continue
        scanned += 1
        if scanned > candidate_scan_limit:
            break
        video_id = extract_video_id(url)
        try:
            response = client.get(f"https://api.vimeo.com/videos/{video_id}")
        except Exception as exc:
            logger.warning("API request failed for %s: %s", video_id, exc)
            continue
        if response.status_code != 200:
            continue
        try:
            data = response.json()
        except Exception:
            continue
        owner_allows_download = data.get("privacy", {}).get("download")
        api_best = extract_best_api_download(data)
        if owner_allows_download or api_best:
            add(url)

    if len(selected) < sample_count:
        for url in source_urls:
            if len(selected) >= sample_count:
                break
            add(url)

    return selected[:sample_count]


def apply_api_env_overrides(config):
    vimeo_cfg = config.setdefault("vimeo_api", {})
    token = os.environ.get("VIMEO_API_TOKEN")
    client_id = os.environ.get("VIMEO_API_CLIENT_ID")
    secret = os.environ.get("VIMEO_API_SECRET")
    if token:
        vimeo_cfg["token"] = token
    if client_id:
        vimeo_cfg["client_id"] = client_id
    if secret:
        vimeo_cfg["secret"] = secret


def collect_api_data(config, urls, logger):
    client = vimeo.VimeoClient(
        token=config["vimeo_api"]["token"],
        key=config["vimeo_api"]["client_id"],
        secret=config["vimeo_api"]["secret"],
    )

    result = {}
    for url in urls:
        video_id = extract_video_id(url)
        row = {
            "status_code": None,
            "privacy_download": None,
            "best_option": None,
            "has_original": False,
            "error": None,
        }
        try:
            response = client.get(f"https://api.vimeo.com/videos/{video_id}")
            row["status_code"] = response.status_code
            if response.status_code == 200:
                data = response.json()
                row["privacy_download"] = data.get("privacy", {}).get("download")
                best = extract_best_api_download(data)
                row["best_option"] = normalize_options([best])[0] if best else None
                row["has_original"] = has_original_option([best] if best else [])
            else:
                row["error"] = f"api_status_{response.status_code}"
        except Exception as exc:
            logger.warning("API collection failed for %s: %s", video_id, exc)
            row["error"] = str(exc)
        result[url] = row
    return result


def summarize_rows(rows):
    total = len(rows)
    anon_download_button = sum(1 for row in rows if row["anonymous"]["download_button_found"])
    anon_original = sum(1 for row in rows if row["anonymous"]["has_original"])
    logged_original = sum(1 for row in rows if row["logged_in"]["has_original"])
    original_only_logged = sum(
        1
        for row in rows
        if row["logged_in"]["has_original"] and not row["anonymous"]["has_original"]
    )
    api_original = sum(1 for row in rows if row["api"]["has_original"])
    return {
        "total_urls": total,
        "anonymous_download_button_found": anon_download_button,
        "anonymous_has_original": anon_original,
        "logged_in_has_original": logged_original,
        "original_only_when_logged_in": original_only_logged,
        "api_has_original": api_original,
    }


def main():
    args = parse_args()
    config = load_config(args.config)
    apply_api_env_overrides(config)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_ROOT / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(output_dir / "compare_original_visibility.log")

    email = os.environ.get("VIMEO_EMAIL", "")
    password = os.environ.get("VIMEO_PASSWORD", "")
    if not email:
        email = input("Vimeo email: ").strip()
    if not password:
        password = getpass.getpass("Vimeo password: ")
    if not email or not password:
        raise SystemExit("Vimeo credentials are required for this test")

    sample_urls = build_sample_urls(
        config,
        args.source_json,
        args.count,
        args.candidate_scan_limit,
        logger,
    )
    sample_path = output_dir / "sample_urls.json"
    with open(sample_path, "w", encoding="utf-8") as f:
        json.dump(sample_urls, f, indent=2, ensure_ascii=False)

    api_data = collect_api_data(config, sample_urls, logger)

    sb_kwargs = build_sb_kwargs(config, logger)
    sb_kwargs["headless"] = False
    sb_kwargs["xvfb"] = False

    anonymous_data = {}
    logger.info("Starting anonymous browser session")
    with SB(**sb_kwargs) as sb:
        for index, url in enumerate(sample_urls, start=1):
            logger.info("[anonymous %d/%d] %s", index, len(sample_urls), url)
            page_result = collect_page_options(sb, url, logger)
            page_result["has_original"] = has_original_option(page_result["options"])
            anonymous_data[url] = page_result

    logged_in_data = {}
    logger.info("Starting logged-in browser session")
    with SB(**sb_kwargs) as sb:
        login_to_vimeo(sb, email, password, logger)
        for index, url in enumerate(sample_urls, start=1):
            logger.info("[logged-in %d/%d] %s", index, len(sample_urls), url)
            page_result = collect_page_options(sb, url, logger)
            page_result["has_original"] = has_original_option(page_result["options"])
            logged_in_data[url] = page_result

    rows = []
    for url in sample_urls:
        rows.append(
            {
                "url": url,
                "video_id": extract_video_id(url),
                "api": api_data.get(url, {}),
                "anonymous": anonymous_data.get(url, {}),
                "logged_in": logged_in_data.get(url, {}),
            }
        )

    summary = summarize_rows(rows)
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_json": str(Path(args.source_json).resolve()),
        "sample_urls_file": str(sample_path),
        "summary": summary,
        "rows": rows,
    }

    report_path = output_dir / "comparison_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    original_only_urls = [
        row["url"]
        for row in rows
        if row["logged_in"].get("has_original") and not row["anonymous"].get("has_original")
    ]
    summary_text = [
        f"total_urls={summary['total_urls']}",
        f"anonymous_download_button_found={summary['anonymous_download_button_found']}",
        f"anonymous_has_original={summary['anonymous_has_original']}",
        f"logged_in_has_original={summary['logged_in_has_original']}",
        f"original_only_when_logged_in={summary['original_only_when_logged_in']}",
        f"api_has_original={summary['api_has_original']}",
        "original_only_when_logged_in_urls:",
        *original_only_urls,
    ]
    summary_path = output_dir / "summary.txt"
    summary_path.write_text("\n".join(summary_text) + "\n", encoding="utf-8")

    print(f"Report saved to: {report_path}")
    print(f"Summary saved to: {summary_path}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

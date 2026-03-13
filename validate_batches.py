"""Validation runner for Vimeo URLs without downloading full files."""

import argparse
import json
import time
from pathlib import Path

import requests
import vimeo
from seleniumbase import SB

from download_vimeo_seleniumbase_v3 import (
    PROJECT_ROOT,
    build_sb_kwargs,
    check_if_cloudflare_blocked,
    extract_video_id,
    load_config,
    setup_logger,
)
from vimeo_cdp_helpers import (
    click_download_button,
    extract_best_api_download,
    extract_best_modal_download,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Validate Vimeo downloadability in batches")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config.json"), help="Path to config JSON")
    parser.add_argument("--source-json", default="", help="Optional source JSON override")
    parser.add_argument("--limit", type=int, default=100, help="How many URLs to validate")
    parser.add_argument("--batches", type=int, default=5, help="How many batches to split the URLs into")
    parser.add_argument("--output-dir", default="", help="Directory for validation results")
    return parser.parse_args()


def chunk_urls(urls, batch_count):
    batch_count = max(1, batch_count)
    chunk_size = max(1, (len(urls) + batch_count - 1) // batch_count)
    return [urls[i : i + chunk_size] for i in range(0, len(urls), chunk_size)]


def validate_link(url):
    try:
        response = requests.get(
            url,
            headers={"Range": "bytes=0-0"},
            stream=True,
            timeout=(20, 30),
            allow_redirects=True,
        )
        ok = response.status_code in (200, 206)
        return {
            "ok": ok,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "content_type": "",
            "error": str(exc),
        }


def page_validation(sb, video_url, logger):
    sb.open(video_url)
    sb.sleep(3)
    if check_if_cloudflare_blocked(sb, logger):
        sb.sleep(10)
        if check_if_cloudflare_blocked(sb, logger):
            return {
                "status": "cloudflare_blocked",
                "reason": "Cloudflare challenge not bypassed during validation",
            }

    click_download_button(sb, timeout=10, logger=logger)
    best_option = extract_best_modal_download(sb, timeout=10, logger=logger)
    link_check = validate_link(best_option["href"])
    return {
        "status": "downloadable_page" if link_check["ok"] else "page_link_invalid",
        "quality": best_option.get("text"),
        "download_link_status": link_check,
        "source": "page",
    }


def validate_video(client, sb, url, logger):
    video_id = extract_video_id(url)
    result = {
        "video_id": video_id,
        "url": url,
        "status": "unknown",
    }

    response = client.get(f"https://api.vimeo.com/videos/{video_id}")
    if response.status_code == 401:
        result["status"] = "fatal_api_401"
        result["reason"] = "Invalid API token"
        return result

    if response.status_code == 429:
        result["status"] = "fatal_api_429"
        result["reason"] = "API rate limit exceeded"
        return result

    if response.status_code == 404:
        result["status"] = "not_found_404"
        return result

    if response.status_code == 403:
        try:
            data = response.json()
        except Exception:
            data = {}
        error_code = data.get("error_code")
        if error_code == 3410:
            result["status"] = "paid_ondemand_403"
        else:
            result["status"] = "forbidden_403"
            result["reason"] = data.get("error", "Unknown 403")
        return result

    metadata = response.json()
    owner_allows_download = metadata.get("privacy", {}).get("download")
    api_download = extract_best_api_download(metadata)

    if api_download:
        link_check = validate_link(api_download["href"])
        result.update(
            {
                "status": "downloadable_api" if link_check["ok"] else "api_link_invalid",
                "quality": api_download.get("text") or api_download.get("quality"),
                "download_link_status": link_check,
                "source": "api",
            }
        )
        return result

    if owner_allows_download is False:
        result["status"] = "skip_privacy_false"
        return result

    try:
        page_result = page_validation(sb, url, logger)
        result.update(page_result)
    except Exception as exc:
        result["status"] = "page_validation_failed"
        result["reason"] = str(exc)

    return result


def summarize_results(results):
    status_counts = {}
    source_counts = {}
    quality_counts = {}
    for item in results:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
        source = item.get("source")
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1
        quality = item.get("quality")
        if quality:
            quality_counts[quality] = quality_counts.get(quality, 0) + 1
    return {
        "total": len(results),
        "status_counts": status_counts,
        "source_counts": source_counts,
        "quality_counts": quality_counts,
    }


def main():
    args = parse_args()
    config = load_config(args.config)

    output_dir = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "output" / "validation_first100"
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(output_dir / "validation.log")

    source_json = Path(args.source_json) if args.source_json else Path(config["files"]["source_json"])
    with open(source_json, "r", encoding="utf-8") as f:
        urls = json.load(f)[: args.limit]

    batches = chunk_urls(urls, args.batches)
    client = vimeo.VimeoClient(
        token=config["vimeo_api"]["token"],
        key=config["vimeo_api"]["client_id"],
        secret=config["vimeo_api"]["secret"],
    )

    browser_config = json.loads(json.dumps(config))
    browser_config["browser"]["headless"] = True
    browser_config["browser"]["xvfb"] = False
    sb_kwargs = build_sb_kwargs(browser_config, logger)

    all_results = []
    batch_summaries = []
    fatal_statuses = {"fatal_api_401", "fatal_api_429"}

    for batch_index, batch_urls in enumerate(batches, start=1):
        batch_results = []
        logger.info("Starting validation batch %d/%d with %d URLs", batch_index, len(batches), len(batch_urls))
        with SB(**sb_kwargs) as sb:
            for index, url in enumerate(batch_urls, start=1):
                logger.info("Batch %d item %d/%d: %s", batch_index, index, len(batch_urls), url)
                result = validate_video(client, sb, url, logger)
                batch_results.append(result)
                all_results.append(result)
                if result["status"] in fatal_statuses:
                    logger.error("Fatal status encountered: %s", result)
                    break
                time.sleep(1)

        batch_summary = summarize_results(batch_results)
        batch_summary["batch_index"] = batch_index
        batch_summary["results_file"] = str(output_dir / f"batch_{batch_index:02d}_results.json")
        with open(output_dir / f"batch_{batch_index:02d}_results.json", "w", encoding="utf-8") as f:
            json.dump(batch_results, f, indent=2, ensure_ascii=False)
        batch_summaries.append(batch_summary)

        if any(item["status"] in fatal_statuses for item in batch_results):
            break

    overall = summarize_results(all_results)
    overall["batches"] = batch_summaries
    overall["source_json"] = str(source_json)
    overall["limit"] = args.limit
    overall["requested_batches"] = args.batches

    with open(output_dir / "overall_summary.json", "w", encoding="utf-8") as f:
        json.dump(overall, f, indent=2, ensure_ascii=False)
    with open(output_dir / "all_results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    logger.info("Validation complete. Summary saved to %s", output_dir / "overall_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

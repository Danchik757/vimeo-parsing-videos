import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "need_parse_unique.json"
DEFAULT_RESULTS = BASE_DIR / "vimeo_download_check_results.jsonl"
DEFAULT_DOWNLOADABLE = BASE_DIR / "vimeo_downloadable_urls.json"
DEFAULT_SUMMARY = BASE_DIR / "vimeo_download_check_summary.json"

VIMEO_API_BASE = "https://api.vimeo.com"
VIMEO_OEMBED_URL = "https://vimeo.com/api/oembed.json"


def normalize_url(url: str) -> str:
    return url.strip().rstrip("/")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(path: Path, payload: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def http_get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 4,
    sleep_seconds: float = 1.0,
) -> dict[str, Any]:
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        req = request.Request(url, headers=headers or {})
        try:
            with request.urlopen(req, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(charset, errors="replace")
                return json.loads(body)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")

            if exc.code == 429 and attempt < retries:
                retry_after = exc.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else sleep_seconds * attempt
                time.sleep(delay)
                continue

            message = f"HTTP {exc.code}"
            if body:
                message = f"{message}: {body[:400]}"
            raise RuntimeError(message) from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(sleep_seconds * attempt)
                continue
            break

    raise RuntimeError(f"Request failed: {last_error}") from last_error


def resolve_video_id_from_oembed(url: str, timeout: float) -> str | None:
    query_url = f"{VIMEO_OEMBED_URL}?{parse.urlencode({'url': url})}"
    payload = http_get_json(query_url, timeout=timeout, retries=2, sleep_seconds=1.0)

    candidates = []
    for key in ("video_id", "videoId", "id"):
        value = payload.get(key)
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            candidates.append(value)

    uri = payload.get("uri")
    if isinstance(uri, str):
        candidates.append(uri)

    html = payload.get("html")
    if isinstance(html, str):
        candidates.append(html)

    for candidate in candidates:
        match = re.search(r"(?<!\d)(\d{6,12})(?!\d)", candidate)
        if match:
            return match.group(1)

    return None


def resolve_video_id(url: str, timeout: float) -> tuple[str | None, str]:
    path = parse.urlparse(url).path.strip("/")
    parts = [part for part in path.split("/") if part]

    if parts and parts[-1].isdigit():
        return parts[-1], "path_numeric_tail"

    video_id = resolve_video_id_from_oembed(url, timeout=timeout)
    if video_id is not None:
        return video_id, "oembed"

    return None, "unresolved"


def fetch_video_metadata(video_id: str, token: str, timeout: float) -> dict[str, Any]:
    headers = {
        "Authorization": f"bearer {token}",
        "Accept": "application/vnd.vimeo.*+json;version=3.4",
    }
    return http_get_json(
        f"{VIMEO_API_BASE}/videos/{video_id}",
        headers=headers,
        timeout=timeout,
        retries=4,
        sleep_seconds=2.0,
    )


def extract_download_variants(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    variants = []
    download_items = metadata.get("download")
    if not isinstance(download_items, list):
        return variants

    for item in download_items:
        if not isinstance(item, dict):
            continue
        variants.append(
            {
                "quality": item.get("quality") or item.get("public_name"),
                "type": item.get("type"),
                "width": item.get("width"),
                "height": item.get("height"),
                "size": item.get("size"),
                "expires": item.get("expires"),
                "link": item.get("link"),
            }
        )
    return variants


def build_result_record(url: str, metadata: dict[str, Any], video_id: str, id_source: str) -> dict[str, Any]:
    privacy = metadata.get("privacy")
    if not isinstance(privacy, dict):
        privacy = {}

    variants = extract_download_variants(metadata)
    owner_allows_download = privacy.get("download")
    has_download_links = len(variants) > 0

    return {
        "url": url,
        "video_id": video_id,
        "id_source": id_source,
        "status": "ok",
        "name": metadata.get("name"),
        "api_uri": metadata.get("uri"),
        "link": metadata.get("link"),
        "owner_allows_download": owner_allows_download,
        "has_download_links": has_download_links,
        "downloadable": bool(owner_allows_download or has_download_links),
        "download_variant_count": len(variants),
        "download_variants": variants,
    }


def load_existing_results(results_path: Path) -> tuple[set[str], list[str]]:
    processed_urls: set[str] = set()
    downloadable_urls: list[str] = []

    if not results_path.exists():
        return processed_urls, downloadable_urls

    with results_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            url = record.get("url")
            if isinstance(url, str):
                processed_urls.add(url)
            if record.get("downloadable") and isinstance(url, str):
                downloadable_urls.append(url)

    return processed_urls, downloadable_urls


def append_result(results_path: Path, record: dict[str, Any]) -> None:
    with results_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check which Vimeo URLs from need_parse_unique.json are downloadable via the Vimeo API."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--downloadable", type=Path, default=DEFAULT_DOWNLOADABLE)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N URLs.")
    parser.add_argument("--start-index", type=int, default=0, help="Skip the first N URLs from the input file.")
    parser.add_argument("--sleep", type=float, default=0.2, help="Pause between successful API calls.")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    token = os.environ.get("VIMEO_TOKEN")
    if not token:
        raise SystemExit("Set VIMEO_TOKEN in the environment before running this script.")

    raw_urls = load_json(args.input)
    if not isinstance(raw_urls, list):
        raise SystemExit(f"Input file must contain a JSON array: {args.input}")

    urls = [normalize_url(url) for url in raw_urls if isinstance(url, str)]
    urls = urls[args.start_index :]
    if args.limit is not None:
        urls = urls[: args.limit]

    processed_urls, downloadable_urls = load_existing_results(args.results)
    downloadable_seen = set(downloadable_urls)

    counters = {
        "total_selected": len(urls),
        "already_processed": 0,
        "processed_now": 0,
        "downloadable_now": 0,
        "unresolved": 0,
        "api_errors": 0,
    }

    for index, url in enumerate(urls, start=1):
        if url in processed_urls:
            counters["already_processed"] += 1
            continue

        video_id, id_source = resolve_video_id(url, timeout=args.timeout)
        if video_id is None:
            record = {
                "url": url,
                "video_id": None,
                "id_source": id_source,
                "status": "unresolved",
                "downloadable": False,
                "error": "Could not resolve numeric Vimeo video id.",
            }
            append_result(args.results, record)
            processed_urls.add(url)
            counters["processed_now"] += 1
            counters["unresolved"] += 1
            continue

        try:
            metadata = fetch_video_metadata(video_id, token=token, timeout=args.timeout)
            record = build_result_record(url, metadata, video_id, id_source)
        except Exception as exc:
            record = {
                "url": url,
                "video_id": video_id,
                "id_source": id_source,
                "status": "api_error",
                "downloadable": False,
                "error": str(exc),
            }
            counters["api_errors"] += 1

        append_result(args.results, record)
        processed_urls.add(url)
        counters["processed_now"] += 1

        if record.get("downloadable") and url not in downloadable_seen:
            downloadable_urls.append(url)
            downloadable_seen.add(url)
            counters["downloadable_now"] += 1

        if index % 100 == 0:
            print(f"Processed {index}/{len(urls)} URLs")

        time.sleep(args.sleep)

    dump_json(args.downloadable, downloadable_urls)
    dump_json(args.summary, counters)

    print(json.dumps(counters, ensure_ascii=False, indent=2))
    print(f"Results: {args.results}")
    print(f"Downloadable URLs: {args.downloadable}")
    print(f"Summary: {args.summary}")


if __name__ == "__main__":
    main()

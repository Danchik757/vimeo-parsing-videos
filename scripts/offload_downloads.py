#!/usr/bin/env python3
"""Copy completed downloads to mounted storage and optionally free local disk."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_utils import load_json_config_with_optional_secrets, resolve_path


def parse_args():
    parser = argparse.ArgumentParser(description="Offload completed downloads to storage")
    parser.add_argument("--config", required=True, help="Path to run config JSON")
    parser.add_argument(
        "--storage-root",
        default="",
        help="Override offload.storage_root from config",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Keep scanning forever using offload.scan_interval_seconds",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Override scan interval in watch mode",
    )
    parser.add_argument(
        "--delete-local-video",
        action="store_true",
        help="Delete local media file after successful verified upload",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max videos to upload per cycle (0 = no limit)",
    )
    return parser.parse_args()


def load_config(config_path):
    config, config_path, config_dir, secrets_path = load_json_config_with_optional_secrets(config_path)
    config["_meta"] = {
        "config_path": str(config_path),
        "config_dir": str(config_dir),
        "secrets_path": str(secrets_path) if secrets_path else None,
    }

    files_cfg = config.setdefault("files", {})
    for key in ("videos_dir", "logs_dir"):
        if key in files_cfg:
            files_cfg[key] = str(resolve_path(config_dir, files_cfg[key]))

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

    logger = logging.getLogger("offload_downloads")
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


def now_string():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def normalize_video_storage_key(value, field_name="video_id"):
    candidate = str(value).strip() if value is not None else ""
    if not candidate:
        raise ValueError(f"{field_name} is required")
    if candidate.isdigit():
        return candidate
    safe_value = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._")
    if not safe_value or safe_value in {".", ".."}:
        raise ValueError(f"{field_name} must resolve to a safe storage key: {value!r}")
    return safe_value


def load_registry(path):
    path = Path(path)
    if not path.exists():
        return {"updated_at": None, "items": {}}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"updated_at": None, "items": {}}
    data.setdefault("items", {})
    return data


def save_registry(path, registry):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    registry["updated_at"] = now_string()
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    os.replace(temp_path, path)


def load_metadata(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_metadata(path, payload):
    path = Path(path)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(temp_path, path)


def ffprobe_media(media_path, ffprobe_bin):
    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,codec_name",
        "-show_entries",
        "format=size,duration",
        "-of",
        "json",
        str(media_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": (completed.stderr or completed.stdout or "ffprobe failed").strip(),
        }
    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"ffprobe returned invalid json: {exc}"}
    return {"ok": True, "data": data}


def find_completed_downloads(videos_root, min_file_age_seconds):
    videos_root = Path(videos_root)
    downloaded_root = videos_root / "downloaded"
    if not downloaded_root.exists():
        return []

    candidates = []
    now_ts = time.time()
    for metadata_path in sorted(downloaded_root.glob("*/*.json")):
        video_dir = metadata_path.parent
        media_files = [
            path
            for path in video_dir.iterdir()
            if path.is_file() and path.suffix not in {".json", ".part"}
        ]
        part_files = list(video_dir.glob("*.part"))
        payload = load_metadata(metadata_path)
        if part_files:
            continue
        if len(media_files) != 1:
            if payload.get("_offload", {}).get("status") == "uploaded":
                candidates.append((payload, metadata_path, None))
            continue
        media_path = media_files[0]
        if now_ts - media_path.stat().st_mtime < min_file_age_seconds:
            continue
        candidates.append((payload, metadata_path, media_path))
    return candidates


def build_remote_paths(storage_root, video_id, media_path):
    normalized_video_id = normalize_video_storage_key(video_id)
    remote_dir = Path(storage_root) / "downloaded" / normalized_video_id
    remote_video = remote_dir / media_path.name
    remote_metadata = remote_dir / f"{normalized_video_id}.json"
    return remote_dir, remote_video, remote_metadata


def copy_file_atomic(source_path, target_path):
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(target_path.name + ".uploading")
    shutil.copy2(source_path, temp_path)
    os.replace(temp_path, target_path)


def remote_file_matches(local_path, remote_path):
    return Path(remote_path).exists() and Path(remote_path).stat().st_size == Path(local_path).stat().st_size


def update_offload_payload(payload, storage_root, remote_video, remote_metadata, verified_with_ffprobe):
    payload.setdefault("_offload", {})
    payload["_offload"].update(
        {
            "status": "uploaded",
            "remote_root": str(storage_root),
            "remote_video_file": str(remote_video),
            "remote_metadata_json": str(remote_metadata),
            "uploaded_at": now_string(),
            "verified_with_ffprobe": verified_with_ffprobe,
        }
    )


def mark_local_video_deleted(payload):
    payload.setdefault("_offload", {})
    payload["_offload"]["local_video_deleted"] = True
    payload["_offload"]["local_video_deleted_at"] = now_string()


def process_candidate(
    payload,
    metadata_path,
    media_path,
    storage_root,
    registry,
    config,
    logger,
    delete_local_video,
):
    video_id = normalize_video_storage_key(
        payload.get("_video_id") or metadata_path.stem,
        field_name="_video_id",
    )
    offload_cfg = config["offload"]
    registry_items = registry.setdefault("items", {})
    existing_record = registry_items.get(video_id) or {}

    remote_dir = None
    remote_video = None
    remote_metadata = None
    if media_path is not None:
        remote_dir, remote_video, remote_metadata = build_remote_paths(storage_root, video_id, media_path)
    elif existing_record.get("remote_video_file"):
        remote_video = Path(existing_record["remote_video_file"])
        remote_metadata = Path(existing_record.get("remote_metadata_json") or "")

    if media_path is None:
        logger.info("Skipping %s because no local media file is present", video_id)
        return False

    if existing_record.get("status") == "uploaded" and remote_video and remote_video.exists():
        if delete_local_video and media_path.exists():
            media_path.unlink()
            mark_local_video_deleted(payload)
            save_metadata(metadata_path, payload)
            logger.info("Deleted local media for %s after confirming existing remote copy", video_id)
        return False

    validate_with_ffprobe = bool(offload_cfg.get("validate_with_ffprobe", True))
    ffprobe_bin = offload_cfg.get("ffprobe_bin", "ffprobe")
    local_probe = None
    if validate_with_ffprobe:
        local_probe = ffprobe_media(media_path, ffprobe_bin)
        if not local_probe["ok"]:
            logger.warning("Local ffprobe failed for %s: %s", video_id, local_probe["error"])
            return False

    copy_file_atomic(media_path, remote_video)
    update_offload_payload(payload, storage_root, remote_video, remote_metadata, validate_with_ffprobe)
    copy_file_atomic(metadata_path, remote_metadata)

    if not remote_file_matches(media_path, remote_video):
        if remote_video.exists():
            remote_video.unlink()
        raise RuntimeError(f"Remote size mismatch after copy for {video_id}")

    if validate_with_ffprobe:
        remote_probe = ffprobe_media(remote_video, ffprobe_bin)
        if not remote_probe["ok"]:
            if remote_video.exists():
                remote_video.unlink()
            if remote_metadata.exists():
                remote_metadata.unlink()
            raise RuntimeError(f"Remote ffprobe failed for {video_id}: {remote_probe['error']}")

    if delete_local_video:
        media_path.unlink()
        mark_local_video_deleted(payload)

    save_metadata(metadata_path, payload)
    copy_file_atomic(metadata_path, remote_metadata)

    registry_items[video_id] = {
        "status": "uploaded",
        "video_id": video_id,
        "uploaded_at": payload["_offload"]["uploaded_at"],
        "local_metadata_json": str(metadata_path),
        "local_video_file": str(media_path),
        "remote_video_file": str(remote_video),
        "remote_metadata_json": str(remote_metadata),
        "file_size_bytes": remote_video.stat().st_size,
        "verified_with_ffprobe": validate_with_ffprobe,
        "local_video_deleted": bool(payload["_offload"].get("local_video_deleted")),
        "source_url": payload.get("_video_url"),
        "selected_quality": (payload.get("_download") or {}).get("selected_quality"),
        "is_original": (payload.get("_download") or {}).get("is_original"),
    }
    logger.info("Offloaded %s to %s", video_id, remote_video)
    return True


def run_cycle(config, storage_root, delete_local_video, limit, logger):
    videos_root = Path(config["files"]["videos_dir"])
    registry_path = Path(config["offload"]["registry_file"])
    registry = load_registry(registry_path)
    candidates = find_completed_downloads(
        videos_root,
        int(config["offload"].get("min_file_age_seconds", 30)),
    )
    processed = 0
    uploaded = 0
    errors = 0

    for payload, metadata_path, media_path in candidates:
        if limit > 0 and processed >= limit:
            break
        processed += 1
        try:
            changed = process_candidate(
                payload,
                metadata_path,
                media_path,
                storage_root,
                registry,
                config,
                logger,
                delete_local_video,
            )
            if changed:
                uploaded += 1
                save_registry(registry_path, registry)
        except Exception as exc:
            errors += 1
            logger.error("Offload failed for %s: %s", metadata_path, exc)

    if uploaded == 0 and not registry_path.exists():
        save_registry(registry_path, registry)

    logger.info(
        "Offload cycle finished: scanned=%d uploaded=%d errors=%d registry=%s",
        processed,
        uploaded,
        errors,
        registry_path,
    )
    return {"scanned": processed, "uploaded": uploaded, "errors": errors}


def main():
    args = parse_args()
    config = load_config(args.config)
    logger = setup_logger(config["offload"]["log_file"])

    storage_root_value = args.storage_root or config["offload"].get("storage_root") or ""
    if not storage_root_value:
        raise SystemExit("offload.storage_root or --storage-root is required")

    storage_root = Path(storage_root_value).resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    delete_local_video = bool(
        args.delete_local_video or config["offload"].get("delete_local_video_after_upload", False)
    )
    interval = int(
        args.interval
        if args.interval is not None
        else config["offload"].get("scan_interval_seconds", 600)
    )

    logger.info("Config: %s", config["_meta"]["config_path"])
    logger.info("Storage root: %s", storage_root)
    logger.info("Delete local video after upload: %s", delete_local_video)

    if not args.watch:
        run_cycle(config, storage_root, delete_local_video, args.limit, logger)
        return 0

    logger.info("Starting watch mode with interval=%ss", interval)
    while True:
        run_cycle(config, storage_root, delete_local_video, args.limit, logger)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

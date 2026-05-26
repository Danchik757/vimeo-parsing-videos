#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Analyze remaining files in a Windows parser run videos directory"
    )
    parser.add_argument(
        "--repo-root",
        default=str(Path(__file__).resolve().parents[1]),
        help="Repository root",
    )
    parser.add_argument(
        "--videos-dir",
        default="output/runs/pc10/windows-2workers-500-email/videos",
        help="Videos directory relative to repo root unless absolute",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="Number of largest downloaded dirs to print",
    )
    parser.add_argument(
        "--delete-safe-offloaded-nonjson",
        action="store_true",
        help="Delete local non-json files for dirs whose metadata says uploaded and remote copy exists",
    )
    return parser.parse_args()


def resolve_under_root(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (root / candidate)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def format_gb(num_bytes: int) -> float:
    return round((num_bytes or 0) / (1024 ** 3), 3)


def dir_size_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return total


def classify_downloaded_dir(video_dir: Path):
    files = [p for p in video_dir.iterdir() if p.is_file()]
    json_files = [p for p in files if p.suffix.lower() == ".json"]
    part_files = [p for p in files if p.suffix.lower() == ".part"]
    media_files = [p for p in files if p.suffix.lower() not in {".json", ".part"}]
    non_json_files = [p for p in files if p.suffix.lower() != ".json"]
    metadata_path = video_dir / f"{video_dir.name}.json"
    if not metadata_path.exists() and json_files:
        metadata_path = json_files[0]

    payload = {}
    if metadata_path.exists():
        try:
            payload = load_json(metadata_path)
        except Exception:
            payload = {}

    offload = payload.get("_offload") or {}
    remote_video_file = str(offload.get("remote_video_file") or "").strip()
    remote_exists = Path(remote_video_file).exists() if remote_video_file else False
    offloaded = offload.get("status") == "uploaded"
    local_video_deleted = bool(offload.get("local_video_deleted"))
    total_bytes = sum(p.stat().st_size for p in files)
    non_json_bytes = sum(p.stat().st_size for p in non_json_files)
    media_bytes = sum(p.stat().st_size for p in media_files)
    part_bytes = sum(p.stat().st_size for p in part_files)

    if offloaded and remote_exists and non_json_bytes > 0:
        category = "offloaded_remote_exists_local_files_present"
    elif offloaded and remote_exists:
        category = "offloaded_remote_exists_metadata_only"
    elif part_files and media_files:
        category = "part_and_media_present"
    elif part_files:
        category = "part_only"
    elif len(media_files) > 1:
        category = "multi_media"
    elif len(media_files) == 1:
        category = "single_media_remaining"
    elif json_files:
        category = "metadata_only"
    else:
        category = "empty_or_unknown"

    return {
        "video_id": video_dir.name,
        "path": str(video_dir),
        "category": category,
        "total_bytes": total_bytes,
        "total_gb": format_gb(total_bytes),
        "non_json_bytes": non_json_bytes,
        "non_json_gb": format_gb(non_json_bytes),
        "media_bytes": media_bytes,
        "media_gb": format_gb(media_bytes),
        "part_bytes": part_bytes,
        "part_gb": format_gb(part_bytes),
        "json_count": len(json_files),
        "media_count": len(media_files),
        "part_count": len(part_files),
        "offloaded": offloaded,
        "remote_exists": remote_exists,
        "local_video_deleted": local_video_deleted,
        "remote_video_file": remote_video_file or None,
        "metadata_path": str(metadata_path) if metadata_path.exists() else None,
        "non_json_files": [str(p) for p in non_json_files],
    }


def maybe_delete_safe_nonjson(entry):
    if entry["category"] != "offloaded_remote_exists_local_files_present":
        return 0
    removed = 0
    for file_name in entry["non_json_files"]:
        path = Path(file_name)
        if path.exists() and path.is_file():
            removed += path.stat().st_size
            path.unlink()
    return removed


def analyze_flat_bucket(path: Path):
    if not path.exists():
        return {"exists": False, "file_count": 0, "size_bytes": 0, "size_gb": 0.0}
    files = [p for p in path.rglob("*") if p.is_file()]
    size_bytes = sum(p.stat().st_size for p in files)
    return {
        "exists": True,
        "file_count": len(files),
        "size_bytes": size_bytes,
        "size_gb": format_gb(size_bytes),
    }


def main():
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    videos_dir = resolve_under_root(repo_root, args.videos_dir).resolve()
    downloaded_root = videos_dir / "downloaded"

    report = {
        "repo_root": str(repo_root),
        "videos_dir": str(videos_dir),
        "downloaded_root": str(downloaded_root),
        "videos_dir_exists": videos_dir.exists(),
        "downloaded_root_exists": downloaded_root.exists(),
    }

    if not downloaded_root.exists():
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    downloaded_entries = []
    for child in sorted(downloaded_root.iterdir()):
        if child.is_dir():
            downloaded_entries.append(classify_downloaded_dir(child))

    deleted_bytes = 0
    if args.delete_safe_offloaded_nonjson:
        for entry in downloaded_entries:
            deleted_bytes += maybe_delete_safe_nonjson(entry)
        downloaded_entries = []
        for child in sorted(downloaded_root.iterdir()):
            if child.is_dir():
                downloaded_entries.append(classify_downloaded_dir(child))

    category_totals = {}
    safe_reclaim_bytes = 0
    for entry in downloaded_entries:
        bucket = category_totals.setdefault(
            entry["category"],
            {"dir_count": 0, "size_bytes": 0},
        )
        bucket["dir_count"] += 1
        bucket["size_bytes"] += entry["total_bytes"]
        if entry["category"] == "offloaded_remote_exists_local_files_present":
            safe_reclaim_bytes += entry["non_json_bytes"]

    for bucket in category_totals.values():
        bucket["size_gb"] = format_gb(bucket["size_bytes"])

    top_dirs = sorted(downloaded_entries, key=lambda item: item["total_bytes"], reverse=True)[: args.top]

    report.update(
        {
            "downloaded_dir_count": len(downloaded_entries),
            "downloaded_total_bytes": sum(item["total_bytes"] for item in downloaded_entries),
            "downloaded_total_gb": format_gb(sum(item["total_bytes"] for item in downloaded_entries)),
            "not_downloaded": analyze_flat_bucket(videos_dir / "not_downloaded"),
            "no_links": analyze_flat_bucket(videos_dir / "no_links"),
            "category_totals": category_totals,
            "safe_reclaim_bytes": safe_reclaim_bytes,
            "safe_reclaim_gb": format_gb(safe_reclaim_bytes),
            "deleted_safe_nonjson_bytes": deleted_bytes,
            "deleted_safe_nonjson_gb": format_gb(deleted_bytes),
            "top_downloaded_dirs": top_dirs,
        }
    )

    report_path = videos_dir.parent / "video_footprint_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"videos_dir: {videos_dir}")
    print(f"downloaded_total_gb: {report['downloaded_total_gb']}")
    print(f"not_downloaded_gb: {report['not_downloaded']['size_gb']}")
    print(f"no_links_gb: {report['no_links']['size_gb']}")
    print(f"safe_reclaim_gb: {report['safe_reclaim_gb']}")
    print(f"deleted_safe_nonjson_gb: {report['deleted_safe_nonjson_gb']}")
    print("category_totals:")
    for key in sorted(category_totals):
        item = category_totals[key]
        print(f"  {key}: dirs={item['dir_count']} size_gb={item['size_gb']}")
    print("top_downloaded_dirs:")
    for item in top_dirs:
        print(
            f"  {item['video_id']}: category={item['category']} size_gb={item['total_gb']} "
            f"media_count={item['media_count']} part_count={item['part_count']} offloaded={item['offloaded']} remote_exists={item['remote_exists']}"
        )
    print(f"report_json: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

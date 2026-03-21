"""Helpers shared by launcher/coordinator scripts."""

from __future__ import annotations

import subprocess
from pathlib import Path


def configure_launched_worker_notifications(config: dict) -> None:
    """Keep worker Telegram alerts high-signal when launched by a coordinator."""
    telegram_cfg = config.setdefault("telegram", {})
    if not isinstance(telegram_cfg, dict):
        return

    telegram_cfg["notify_on_start"] = False
    telegram_cfg["notify_on_finish"] = False
    telegram_cfg["notify_on_error"] = False
    telegram_cfg["notify_download_every_n_successes"] = 0
    telegram_cfg["notify_skip_every_n_processed"] = 0
    telegram_cfg["notify_progress_every_n_processed"] = 0
    telegram_cfg["notify_on_heartbeat"] = False


def format_bytes(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "missing"

    value = float(size_bytes)
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(size_bytes)} B"


def _du_size_bytes(path: Path) -> int | None:
    try:
        result = subprocess.run(
            ["du", "-sk", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None

    parts = result.stdout.strip().split()
    if not parts:
        return None
    try:
        return int(parts[0]) * 1024
    except ValueError:
        return None


def _walk_size_bytes(path: Path) -> int | None:
    total = 0
    try:
        for candidate in path.rglob("*"):
            if candidate.is_file():
                total += candidate.stat().st_size
    except OSError:
        return None
    return total


def get_storage_snapshot(storage_root: str | None) -> dict | None:
    root = str(storage_root or "").strip()
    if not root:
        return None

    path = Path(root)
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "bytes": None,
            "human": "missing",
        }

    size_bytes = _du_size_bytes(path)
    if size_bytes is None:
        size_bytes = _walk_size_bytes(path)

    return {
        "path": str(path),
        "exists": True,
        "bytes": size_bytes,
        "human": format_bytes(size_bytes),
    }

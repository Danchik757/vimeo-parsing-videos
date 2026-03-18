"""Shared helpers for loading layered JSON configs."""

from __future__ import annotations

import json
from pathlib import Path


def resolve_path(config_dir, value):
    path = Path(value)
    if path.is_absolute():
        return path
    return (config_dir / path).resolve()


def deep_merge_dict(base, overrides):
    for key, value in (overrides or {}).items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            deep_merge_dict(base[key], value)
        else:
            base[key] = value
    return base


def load_json_config_with_optional_secrets(config_path):
    config_path = Path(config_path).resolve()
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    config_dir = config_path.parent
    secrets_file = str(config.get("secrets_file", "")).strip()
    secrets_path = None
    if secrets_file:
        secrets_path = resolve_path(config_dir, secrets_file)
        with open(secrets_path, "r", encoding="utf-8") as f:
            secrets = json.load(f)
        config = deep_merge_dict(config, secrets)

    return config, config_path, config_dir, secrets_path

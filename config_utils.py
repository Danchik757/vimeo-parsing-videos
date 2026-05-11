"""Shared helpers for loading layered JSON configs."""

from __future__ import annotations

import json
import os
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


def load_json_with_base_config(config_path, visited=None):
    config_path = Path(config_path).resolve()
    visited = set(visited or ())
    config_key = str(config_path)
    if config_key in visited:
        raise ValueError(f"base_config cycle detected: {config_path}")
    visited.add(config_key)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    base_config_value = str(config.get("base_config", "")).strip()
    if not base_config_value:
        return config, config_path.parent

    base_config_path = resolve_path(config_path.parent, base_config_value)
    base_config, _ = load_json_with_base_config(base_config_path, visited=visited)
    merged = deep_merge_dict(base_config, {k: v for k, v in config.items() if k != "base_config"})
    return merged, config_path.parent


def apply_env_overrides(config):
    vimeo_api = config.setdefault("vimeo_api", {})
    telegram = config.setdefault("telegram", {})
    vimeo_login = config.setdefault("vimeo_login", {})
    proxy = config.setdefault("proxy", {})

    env_map = (
        ("VIMEO_API_TOKEN", vimeo_api, "token"),
        ("VIMEO_API_SECRET", vimeo_api, "secret"),
        ("VIMEO_API_CLIENT_ID", vimeo_api, "client_id"),
        ("TELEGRAM_BOT_TOKEN", telegram, "bot_token"),
        ("TELEGRAM_CHAT_ID", telegram, "chat_id"),
        ("VIMEO_EMAIL", vimeo_login, "email"),
        ("VIMEO_PASSWORD", vimeo_login, "password"),
        ("PROXY_HOST", proxy, "host"),
        ("PROXY_PORT", proxy, "port"),
        ("PROXY_USERNAME", proxy, "username"),
        ("PROXY_PASSWORD", proxy, "password"),
    )

    for env_name, target, key in env_map:
        value = os.environ.get(env_name)
        if value is not None and str(value).strip():
            target[key] = value

    return config


def load_json_config_with_optional_secrets(config_path):
    config_path = Path(config_path).resolve()
    config, config_dir = load_json_with_base_config(config_path)
    secrets_file = str(config.get("secrets_file", "")).strip()
    secrets_path = None
    if secrets_file:
        secrets_path = resolve_path(config_dir, secrets_file)
        with open(secrets_path, "r", encoding="utf-8") as f:
            secrets = json.load(f)
        config = deep_merge_dict(config, secrets)

    config = apply_env_overrides(config)
    return config, config_path, config_dir, secrets_path

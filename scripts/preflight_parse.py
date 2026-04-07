#!/usr/bin/env python3
"""Preflight checks for parse runs before starting parser/offload workers."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_utils import load_json_config_with_optional_secrets, resolve_path


REQUIRED_PYTHON_MODULES = ("requests", "vimeo", "seleniumbase")
TCP_TARGETS = (
    ("github.com", 443, "GitHub HTTPS"),
    ("vimeo.com", 443, "Vimeo Web"),
    ("api.vimeo.com", 443, "Vimeo API"),
    ("api.telegram.org", 443, "Telegram API"),
)
PROCESS_PATTERNS = (
    "run_assigned_shards.py",
    "download_vimeo_seleniumbase_v3.py",
    "offload_downloads.py",
    "chromedriver",
    "/opt/google/chrome/chrome",
)


class Reporter:
    def __init__(self):
        self.failures = 0
        self.warnings = 0
        self.passes = 0

    def section(self, title):
        print()
        print(f"== {title} ==")

    def pass_(self, label, detail=""):
        self.passes += 1
        print(f"PASS  {label}{_fmt_detail(detail)}")

    def warn(self, label, detail=""):
        self.warnings += 1
        print(f"WARN  {label}{_fmt_detail(detail)}")

    def fail(self, label, detail=""):
        self.failures += 1
        print(f"FAIL  {label}{_fmt_detail(detail)}")

    def summary(self):
        print()
        print(
            "Summary: "
            f"passes={self.passes} warnings={self.warnings} failures={self.failures}"
        )


def _fmt_detail(detail):
    return f" | {detail}" if detail else ""


def parse_args():
    parser = argparse.ArgumentParser(description="Preflight checks before starting a parse run")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to profile config JSON",
    )
    parser.add_argument(
        "--manifest",
        default="",
        help="Optional assignment manifest.json for queue-run validation",
    )
    parser.add_argument(
        "--min-local-free-gb",
        type=float,
        default=20.0,
        help="Warn if local free disk is below this threshold",
    )
    parser.add_argument(
        "--min-storage-free-gb",
        type=float,
        default=50.0,
        help="Warn if storage free disk is below this threshold",
    )
    parser.add_argument(
        "--min-mem-free-gb",
        type=float,
        default=2.0,
        help="Warn if available RAM is below this threshold",
    )
    return parser.parse_args()


def load_profile_config(config_path):
    config, config_path, config_dir, secrets_path = load_json_config_with_optional_secrets(config_path)
    config["_meta"] = {
        "config_path": str(config_path),
        "config_dir": str(config_dir),
        "secrets_path": str(secrets_path) if secrets_path else None,
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
        "summary_file",
    ):
        if key in files_cfg and files_cfg[key]:
            files_cfg[key] = str(resolve_path(config_dir, files_cfg[key]))

    offload = config.setdefault("offload", {})
    for key in ("storage_root", "registry_file", "log_file"):
        if offload.get(key):
            offload[key] = str(resolve_path(config_dir, offload[key]))

    return config


def path_write_probe(path_value):
    path = Path(path_value)
    target = path if path.exists() else path.parent
    if not target.exists():
        return False, f"parent missing: {target}"
    if not os.access(target, os.W_OK):
        return False, f"not writable: {target}"
    probe = target / f".preflight_write_test_{os.getpid()}_{int(time.time())}"
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok\n")
        probe.unlink()
        return True, str(target)
    except Exception as exc:
        return False, f"{target}: {exc}"


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_manifest_source_json(manifest_path, source_json, filename=None):
    manifest_path = Path(manifest_path).resolve()
    manifest_dir = manifest_path.parent
    source_path = Path(source_json)

    candidates = []
    if not source_path.is_absolute():
        candidates.append((manifest_dir / source_path).resolve())
    if filename:
        candidates.append((manifest_dir / filename).resolve())
    candidates.append((manifest_dir / source_path.name).resolve())
    if source_path.is_absolute():
        candidates.append(source_path)

    seen = set()
    for candidate in candidates:
        candidate_str = str(candidate)
        if candidate_str in seen:
            continue
        seen.add(candidate_str)
        if candidate.exists():
            return candidate

    return candidates[0]


def normalize_manifest(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    payload = read_json(manifest_path)
    batches = []
    for item in payload.get("batches", []):
        batch_number = int(item["batch_number"])
        source_json = item.get("path") or item.get("source_json")
        filename = item.get("filename") or (Path(source_json).name if source_json else None)
        if not source_json:
            raise ValueError(f"Manifest batch {batch_number} is missing path/source_json")
        batches.append(
            {
                "batch_number": batch_number,
                "source_json": resolve_manifest_source_json(manifest_path, source_json, filename=filename),
            }
        )
    if not batches:
        raise ValueError(f"No batches found in manifest: {manifest_path}")
    return manifest_path, batches


def run_command(args, timeout=10):
    completed = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def get_interface_ipv4_addresses():
    ip_binary = shutil.which("ip")
    if not ip_binary:
        return {}
    rc, stdout, stderr = run_command([ip_binary, "-o", "-4", "addr", "show"], timeout=10)
    if rc != 0:
        raise RuntimeError(stderr or stdout or "ip addr show failed")
    mapping = {}
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1]
        cidr = parts[3]
        address = cidr.split("/", 1)[0]
        mapping.setdefault(iface, set()).add(address)
    return mapping


def get_interface_for_source_ip(source_ip):
    try:
        mapping = get_interface_ipv4_addresses()
    except Exception:
        return None
    for iface, addresses in mapping.items():
        if source_ip in addresses:
            return iface
    return None


def check_interface_state(interface_name):
    ip_binary = shutil.which("ip")
    if not ip_binary:
        return False, "ip command not found"
    rc, stdout, stderr = run_command([ip_binary, "link", "show", "dev", interface_name], timeout=10)
    if rc != 0:
        return False, stderr or stdout or f"interface {interface_name} not found"
    line = stdout.splitlines()[0] if stdout else ""
    return ("UP" in line and "LOWER_UP" in line), line


def find_rule_table_for_source_ip(source_ip):
    ip_binary = shutil.which("ip")
    if not ip_binary:
        return False, "ip command not found"
    rc, stdout, stderr = run_command([ip_binary, "rule", "show"], timeout=10)
    if rc != 0:
        return False, stderr or stdout or "ip rule show failed"

    for line in stdout.splitlines():
        if f"from {source_ip}" not in line:
            continue
        parts = line.split()
        if "lookup" in parts:
            idx = parts.index("lookup")
            if idx + 1 < len(parts):
                return True, parts[idx + 1]
    return False, f"no ip rule for source {source_ip}"


def route_table_has_default_dev(table_name, interface_name):
    ip_binary = shutil.which("ip")
    if not ip_binary:
        return False, "ip command not found"
    rc, stdout, stderr = run_command(
        [ip_binary, "route", "show", "table", str(table_name)],
        timeout=10,
    )
    if rc != 0:
        return False, stderr or stdout or f"ip route show table {table_name} failed"

    for line in stdout.splitlines():
        if line.startswith("default ") and f"dev {interface_name}" in line:
            return True, line
    return False, stdout or f"no default route via {interface_name} in table {table_name}"


def check_dns(hostname):
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except Exception as exc:
        return False, str(exc)
    addresses = []
    for item in infos:
        address = item[4][0]
        if address not in addresses:
            addresses.append(address)
    return True, ", ".join(addresses[:4])


def tcp_connect(hostname, port, timeout=5, source_address=None):
    try:
        with socket.create_connection(
            (hostname, port),
            timeout=timeout,
            source_address=(source_address, 0) if source_address else None,
        ):
            return True, "connected"
    except Exception as exc:
        return False, str(exc)


def curl_interface_head(interface_name, url, timeout=10):
    curl_binary = shutil.which("curl")
    if not curl_binary:
        return False, "curl not found"
    rc, stdout, stderr = run_command(
        [
            curl_binary,
            "--interface",
            interface_name,
            "-4",
            "-m",
            str(timeout),
            "-I",
            "-sS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            url,
        ],
        timeout=timeout + 2,
    )
    if rc != 0:
        return False, stderr or stdout or f"curl exited with {rc}"
    if stdout and stdout != "000":
        return True, f"http={stdout}"
    return False, "no HTTP response"


def read_sockstat():
    counters = {}
    for path in ("/proc/net/sockstat", "/proc/net/sockstat6"):
        file_path = Path(path)
        if not file_path.exists():
            continue
        with open(file_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                proto, payload = line.split(":", 1)
                values = payload.split()
                for index in range(0, len(values) - 1, 2):
                    key = values[index]
                    try:
                        value = int(values[index + 1])
                    except ValueError:
                        continue
                    counters[f"{proto.strip().lower()}_{key.lower()}"] = (
                        counters.get(f"{proto.strip().lower()}_{key.lower()}", 0) + value
                    )
    return counters


def available_memory_gb():
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    values = {}
    with open(meminfo, "r", encoding="utf-8") as handle:
        for line in handle:
            if ":" not in line:
                continue
            key, payload = line.split(":", 1)
            values[key.strip()] = payload.strip()
    available = values.get("MemAvailable")
    if not available:
        return None
    parts = available.split()
    if not parts:
        return None
    try:
        kb = int(parts[0])
    except ValueError:
        return None
    return kb / 1024 / 1024


def format_gb(value):
    return f"{value:.1f} GB"


def browser_command():
    for command in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(command):
            return command
    return None


def import_python_modules(reporter):
    reporter.section("Python")
    reporter.pass_("python", sys.version.split()[0])
    for module_name in REQUIRED_PYTHON_MODULES:
        try:
            importlib.import_module(module_name)
            reporter.pass_(f"python module {module_name}")
        except Exception as exc:
            reporter.fail(f"python module {module_name}", str(exc))


def check_git(reporter):
    reporter.section("Git")
    git_binary = shutil.which("git")
    if not git_binary:
        reporter.fail("git", "not found")
        return
    rc, stdout, stderr = run_command([git_binary, "-C", str(PROJECT_ROOT), "status", "--short", "--branch"], timeout=10)
    if rc != 0:
        reporter.fail("git status", stderr or stdout)
        return
    lines = stdout.splitlines()
    reporter.pass_("git repo", lines[0] if lines else str(PROJECT_ROOT))
    dirty = [line for line in lines[1:] if line.strip()]
    if dirty:
        reporter.warn("git working tree", f"{len(dirty)} pending changes")
    else:
        reporter.pass_("git working tree", "clean")


def check_tools(config, reporter):
    reporter.section("Tools")
    for tool in ("curl", "ip", "tmux"):
        if shutil.which(tool):
            reporter.pass_(f"tool {tool}")
        else:
            reporter.fail(f"tool {tool}", "not found")

    if browser_command():
        reporter.pass_("browser", browser_command())
    else:
        reporter.fail("browser", "google-chrome/chromium not found")

    if config.get("browser", {}).get("xvfb"):
        if shutil.which("xvfb-run"):
            reporter.pass_("xvfb-run")
        else:
            reporter.fail("xvfb-run", "required because browser.xvfb=true")

    if config.get("settings", {}).get("download_interface"):
        if shutil.which("curl"):
            reporter.pass_("curl for interface-bound downloads")
        else:
            reporter.fail("curl for interface-bound downloads", "settings.download_interface is set")

    if config.get("offload", {}).get("enabled") and config.get("offload", {}).get("validate_with_ffprobe", True):
        ffprobe_bin = config["offload"].get("ffprobe_bin", "ffprobe")
        if shutil.which(ffprobe_bin):
            reporter.pass_("ffprobe", ffprobe_bin)
        else:
            reporter.fail("ffprobe", f"not found: {ffprobe_bin}")


def check_config_and_paths(config, args, reporter):
    reporter.section("Config")
    meta = config["_meta"]
    reporter.pass_("config", meta["config_path"])
    if meta.get("secrets_path"):
        reporter.pass_("secrets", meta["secrets_path"])
    else:
        reporter.warn("secrets", "no layered secrets_file configured")

    vimeo_cfg = config.get("vimeo_api", {})
    for key in ("token", "client_id", "secret"):
        if str(vimeo_cfg.get(key, "")).strip():
            reporter.pass_(f"vimeo_api.{key}")
        else:
            reporter.fail(f"vimeo_api.{key}", "missing")

    telegram_cfg = config.get("telegram", {})
    if telegram_cfg.get("enabled"):
        for key in ("bot_token", "chat_id"):
            if str(telegram_cfg.get(key, "")).strip():
                reporter.pass_(f"telegram.{key}")
            else:
                reporter.fail(f"telegram.{key}", "missing")

    files_cfg = config.get("files", {})
    source_json = files_cfg.get("source_json")
    if source_json and Path(source_json).exists():
        reporter.pass_("source_json", source_json)
    else:
        reporter.fail("source_json", source_json or "missing")

    for key in ("videos_dir", "logs_dir"):
        value = files_cfg.get(key)
        if not value:
            reporter.fail(key, "missing")
            continue
        ok, detail = path_write_probe(value)
        if ok:
            reporter.pass_(key, detail)
        else:
            reporter.fail(key, detail)

    offload_cfg = config.get("offload", {})
    if offload_cfg.get("enabled"):
        storage_root = offload_cfg.get("storage_root", "")
        if not storage_root:
            reporter.fail("offload.storage_root", "missing")
        elif not Path(storage_root).exists():
            reporter.fail("offload.storage_root", f"missing: {storage_root}")
        else:
            ok, detail = path_write_probe(storage_root)
            if ok:
                reporter.pass_("offload.storage_root", detail)
            else:
                reporter.fail("offload.storage_root", detail)

    if args.manifest:
        manifest_path = Path(args.manifest).resolve()
        if not manifest_path.exists():
            reporter.fail("manifest", str(manifest_path))
            return
        try:
            _, batches = normalize_manifest(manifest_path)
        except Exception as exc:
            reporter.fail("manifest parse", str(exc))
            return
        missing = [str(item["source_json"]) for item in batches if not Path(item["source_json"]).exists()]
        if missing:
            reporter.fail("manifest batches", f"missing shard files: {missing[:3]}")
        else:
            reporter.pass_("manifest", f"{manifest_path} ({len(batches)} batches)")


def check_interfaces_and_network(config, reporter):
    reporter.section("Network")

    telegram_cfg = config.get("telegram", {})
    source_address = str(telegram_cfg.get("source_address", "")).strip()

    for hostname, _, label in TCP_TARGETS:
        ok, detail = check_dns(hostname)
        if ok:
            reporter.pass_(f"DNS {label}", detail)
        else:
            reporter.fail(f"DNS {label}", detail)

    for hostname, port, label in TCP_TARGETS:
        ok, detail = tcp_connect(hostname, port, timeout=5)
        if ok:
            reporter.pass_(f"TCP {label}", detail)
        elif hostname == "api.telegram.org" and source_address:
            reporter.warn(
                f"TCP {label}",
                (
                    f"default-route check failed ({detail}); "
                    f"source-bound Telegram path is used instead via {source_address}"
                ),
            )
        else:
            reporter.fail(f"TCP {label}", detail)

    if source_address:
        iface = get_interface_for_source_ip(source_address)
        if iface:
            reporter.pass_("telegram source_address", f"{source_address} on {iface}")
        else:
            reporter.fail("telegram source_address", f"local IP not found: {source_address}")
        ok, detail = tcp_connect("api.telegram.org", 443, timeout=5, source_address=source_address)
        if ok:
            reporter.pass_("telegram source TCP bind", source_address)
        else:
            reporter.fail("telegram source TCP bind", detail)

    download_interface = str(config.get("settings", {}).get("download_interface", "")).strip()
    if download_interface:
        ok, detail = check_interface_state(download_interface)
        if ok:
            reporter.pass_("download interface", detail)
        else:
            reporter.fail("download interface", detail)

        if source_address:
            iface = get_interface_for_source_ip(source_address)
            if iface and iface == download_interface:
                reporter.pass_(
                    "source address matches download interface",
                    f"{source_address} -> {download_interface}",
                )
            elif iface:
                reporter.warn(
                    "source address interface mismatch",
                    f"{source_address} is on {iface}, download_interface={download_interface}",
                )

            ok, detail = find_rule_table_for_source_ip(source_address)
            if ok:
                reporter.pass_("policy routing rule", f"from {source_address} lookup {detail}")
                route_ok, route_detail = route_table_has_default_dev(detail, download_interface)
                if route_ok:
                    reporter.pass_("policy routing default route", route_detail)
                else:
                    reporter.fail("policy routing default route", route_detail)
            else:
                reporter.fail("policy routing rule", detail)

        ok, detail = curl_interface_head(download_interface, "https://api.telegram.org", timeout=10)
        if ok:
            reporter.pass_("curl via download interface to Telegram", detail)
        else:
            reporter.fail("curl via download interface to Telegram", detail)

        ok, detail = curl_interface_head(download_interface, "https://vimeo.com", timeout=10)
        if ok:
            reporter.pass_("curl via download interface to Vimeo web", detail)
        else:
            reporter.fail("curl via download interface to Vimeo web", detail)

        ok, detail = curl_interface_head(download_interface, "https://api.vimeo.com", timeout=10)
        if ok:
            reporter.pass_("curl via download interface to Vimeo", detail)
        else:
            reporter.fail("curl via download interface to Vimeo", detail)


def check_system_state(config, args, reporter):
    reporter.section("System")

    videos_dir = Path(config["files"]["videos_dir"])
    local_disk = shutil.disk_usage(videos_dir if videos_dir.exists() else videos_dir.parent)
    local_free_gb = local_disk.free / 1024 / 1024 / 1024
    if local_free_gb < args.min_local_free_gb:
        reporter.warn("local disk free", format_gb(local_free_gb))
    else:
        reporter.pass_("local disk free", format_gb(local_free_gb))

    offload_cfg = config.get("offload", {})
    storage_root = offload_cfg.get("storage_root", "")
    if offload_cfg.get("enabled") and storage_root and Path(storage_root).exists():
        storage_disk = shutil.disk_usage(storage_root)
        storage_free_gb = storage_disk.free / 1024 / 1024 / 1024
        if storage_free_gb < args.min_storage_free_gb:
            reporter.warn("storage free", format_gb(storage_free_gb))
        else:
            reporter.pass_("storage free", format_gb(storage_free_gb))

    mem_available_gb = available_memory_gb()
    if mem_available_gb is None:
        reporter.warn("memory", "MemAvailable not readable")
    elif mem_available_gb < args.min_mem_free_gb:
        reporter.warn("memory", format_gb(mem_available_gb))
    else:
        reporter.pass_("memory", format_gb(mem_available_gb))

    sockstat = read_sockstat()
    if sockstat:
        reporter.pass_(
            "sockets",
            "used={used} tcp_inuse={tcp_inuse} tcp_tw={tcp_tw} tcp_orphan={tcp_orphan} tcp_alloc={tcp_alloc}".format(
                used=sockstat.get("sockets_used", 0),
                tcp_inuse=sockstat.get("tcp_inuse", 0),
                tcp_tw=sockstat.get("tcp_tw", 0),
                tcp_orphan=sockstat.get("tcp_orphan", 0),
                tcp_alloc=sockstat.get("tcp_alloc", 0),
            ),
        )
        if sockstat.get("tcp_tw", 0) > 512:
            reporter.warn("tcp timewait", str(sockstat.get("tcp_tw", 0)))
        if sockstat.get("tcp_orphan", 0) > 32:
            reporter.warn("tcp orphan", str(sockstat.get("tcp_orphan", 0)))
        if sockstat.get("tcp_alloc", 0) > 2048:
            reporter.warn("tcp alloc", str(sockstat.get("tcp_alloc", 0)))


def check_processes(reporter):
    reporter.section("Processes")
    pgrep_binary = shutil.which("pgrep")
    if not pgrep_binary:
        reporter.warn("pgrep", "not found")
        return
    rc, stdout, stderr = run_command([pgrep_binary, "-af", "|".join(PROCESS_PATTERNS)], timeout=10)
    if rc == 0 and stdout:
        lines = stdout.splitlines()
        reporter.warn("existing parse processes", f"{len(lines)} found")
        for line in lines[:5]:
            print(f"      {line}")
    else:
        reporter.pass_("existing parse processes", "none")


def main():
    args = parse_args()
    reporter = Reporter()

    try:
        config = load_profile_config(args.config)
    except Exception as exc:
        print(f"FAIL  config load | {exc}")
        return 1

    import_python_modules(reporter)
    check_git(reporter)
    check_tools(config, reporter)
    check_config_and_paths(config, args, reporter)
    check_interfaces_and_network(config, reporter)
    check_system_state(config, args, reporter)
    check_processes(reporter)
    reporter.summary()

    if reporter.failures:
        print("Ready to start: NO")
        return 1

    print("Ready to start: YES")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

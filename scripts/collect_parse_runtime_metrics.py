#!/usr/bin/env python3
"""Collect parse runtime metrics for network/socket pressure analysis."""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import time
from pathlib import Path


CSV_FIELDS = [
    "timestamp",
    "epoch",
    "cpu_pct",
    "load1",
    "load5",
    "load15",
    "mem_used_bytes",
    "mem_available_bytes",
    "swap_used_bytes",
    "disk_used_bytes",
    "disk_avail_bytes",
    "rx_bytes",
    "tx_bytes",
    "sockets_used",
    "tcp_inuse",
    "tcp_tw",
    "tcp_orphan",
    "tcp_alloc",
    "udp_inuse",
    "raw_inuse",
    "ss_established",
    "ss_time_wait",
    "ss_close_wait",
    "ss_syn_sent",
    "ss_last_ack",
    "parser_count",
    "coordinator_count",
    "downloader_count",
    "chrome_count",
    "offload_count",
    "wg_handshake_age_seconds",
    "wg_rx_bytes",
    "wg_tx_bytes",
]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="CSV output path")
    parser.add_argument("--interval", type=int, default=10, help="Sampling interval in seconds")
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=7200,
        help="Total duration to collect. Use 0 to run until interrupted.",
    )
    parser.add_argument(
        "--iface",
        default="",
        help="Network interface to sample rx/tx bytes from. Defaults to default-route interface.",
    )
    parser.add_argument(
        "--disk-path",
        default=".",
        help="Path whose filesystem usage should be measured.",
    )
    parser.add_argument(
        "--wg-interface",
        default="telegram-wg",
        help="WireGuard interface to inspect. Use empty string to disable WG metrics.",
    )
    return parser.parse_args()


def detect_default_interface():
    try:
        output = subprocess.check_output(
            ["ip", "route", "show", "default"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ""
    for line in output.splitlines():
        parts = line.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    return ""


def read_cpu_totals():
    with open("/proc/stat", "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("cpu "):
                parts = [int(value) for value in line.split()[1:9]]
                total = sum(parts)
                idle = parts[3] + parts[4]
                return total, idle
    return 0, 0


def read_meminfo():
    values = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            try:
                values[key] = int(value.strip().split()[0]) * 1024
            except Exception:
                continue
    mem_total = values.get("MemTotal", 0)
    mem_available = values.get("MemAvailable", 0)
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    return {
        "mem_used_bytes": max(0, mem_total - mem_available),
        "mem_available_bytes": mem_available,
        "swap_used_bytes": max(0, swap_total - swap_free),
    }


def read_loadavg():
    with open("/proc/loadavg", "r", encoding="utf-8") as f:
        parts = f.read().strip().split()
    return {
        "load1": float(parts[0]),
        "load5": float(parts[1]),
        "load15": float(parts[2]),
    }


def read_disk_usage(path_value):
    st = os.statvfs(path_value)
    total_blocks = st.f_blocks * st.f_frsize
    avail_blocks = st.f_bavail * st.f_frsize
    used_blocks = max(0, total_blocks - (st.f_bfree * st.f_frsize))
    return {
        "disk_used_bytes": used_blocks,
        "disk_avail_bytes": avail_blocks,
    }


def read_iface_bytes(iface_name):
    rx_path = Path("/sys/class/net") / iface_name / "statistics" / "rx_bytes"
    tx_path = Path("/sys/class/net") / iface_name / "statistics" / "tx_bytes"
    return {
        "rx_bytes": int(rx_path.read_text(encoding="utf-8").strip()),
        "tx_bytes": int(tx_path.read_text(encoding="utf-8").strip()),
    }


def read_sockstat_file(path):
    stats = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or ":" not in line:
                    continue
                family, tail = line.split(":", 1)
                metrics = {}
                parts = tail.strip().split()
                for index in range(0, len(parts) - 1, 2):
                    key = parts[index]
                    try:
                        metrics[key] = int(parts[index + 1])
                    except ValueError:
                        continue
                stats[family.lower()] = metrics
    except Exception:
        return {}
    return stats


def read_socket_usage():
    sockstat = read_sockstat_file("/proc/net/sockstat")
    sockstat6 = read_sockstat_file("/proc/net/sockstat6")
    return {
        "sockets_used": int(sockstat.get("sockets", {}).get("used", 0)),
        "tcp_inuse": int(sockstat.get("tcp", {}).get("inuse", 0))
        + int(sockstat6.get("tcp6", {}).get("inuse", 0)),
        "tcp_tw": int(sockstat.get("tcp", {}).get("tw", 0)),
        "tcp_orphan": int(sockstat.get("tcp", {}).get("orphan", 0)),
        "tcp_alloc": int(sockstat.get("tcp", {}).get("alloc", 0)),
        "udp_inuse": int(sockstat.get("udp", {}).get("inuse", 0))
        + int(sockstat6.get("udp6", {}).get("inuse", 0)),
        "raw_inuse": int(sockstat.get("raw", {}).get("inuse", 0))
        + int(sockstat6.get("raw6", {}).get("inuse", 0)),
    }


def read_ss_state_counts():
    counts = {
        "ss_established": 0,
        "ss_time_wait": 0,
        "ss_close_wait": 0,
        "ss_syn_sent": 0,
        "ss_last_ack": 0,
    }
    try:
        output = subprocess.check_output(["ss", "-tan"], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return counts
    for line in output.splitlines()[1:]:
        parts = line.split()
        if not parts:
            continue
        state = parts[0].upper()
        if state == "ESTAB":
            counts["ss_established"] += 1
        elif state == "TIME-WAIT":
            counts["ss_time_wait"] += 1
        elif state == "CLOSE-WAIT":
            counts["ss_close_wait"] += 1
        elif state == "SYN-SENT":
            counts["ss_syn_sent"] += 1
        elif state == "LAST-ACK":
            counts["ss_last_ack"] += 1
    return counts


def count_processes(pattern):
    try:
        output = subprocess.check_output(
            ["pgrep", "-af", pattern],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return 0
    lines = [line for line in output.splitlines() if line.strip()]
    return len(lines)


def read_wg_metrics(interface_name):
    payload = {
        "wg_handshake_age_seconds": -1,
        "wg_rx_bytes": 0,
        "wg_tx_bytes": 0,
    }
    if not interface_name:
        return payload
    try:
        output = subprocess.check_output(
            ["wg", "show", interface_name, "dump"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return payload
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) < 2:
        return payload
    now_epoch = int(time.time())
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        latest_handshake = int(parts[5] or 0)
        rx_bytes = int(parts[6] or 0)
        tx_bytes = int(parts[7] or 0)
        payload["wg_rx_bytes"] += rx_bytes
        payload["wg_tx_bytes"] += tx_bytes
        if latest_handshake > 0:
            age = max(0, now_epoch - latest_handshake)
            if payload["wg_handshake_age_seconds"] < 0:
                payload["wg_handshake_age_seconds"] = age
            else:
                payload["wg_handshake_age_seconds"] = min(payload["wg_handshake_age_seconds"], age)
    return payload


def build_sample(iface_name, disk_path, wg_interface, prev_cpu_total, prev_cpu_idle):
    now_epoch = int(time.time())
    now_ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    cpu_total, cpu_idle = read_cpu_totals()
    total_delta = cpu_total - prev_cpu_total
    idle_delta = cpu_idle - prev_cpu_idle
    if total_delta <= 0:
        cpu_pct = 0.0
    else:
        cpu_pct = ((total_delta - idle_delta) / total_delta) * 100.0

    sample = {
        "timestamp": now_ts,
        "epoch": now_epoch,
        "cpu_pct": f"{cpu_pct:.2f}",
    }
    sample.update(read_loadavg())
    sample.update(read_meminfo())
    sample.update(read_disk_usage(disk_path))
    sample.update(read_iface_bytes(iface_name))
    sample.update(read_socket_usage())
    sample.update(read_ss_state_counts())
    sample.update(
        {
            "parser_count": count_processes("run_assigned_shards.py"),
            "coordinator_count": count_processes("run_assigned_shards.py"),
            "downloader_count": count_processes("download_vimeo_seleniumbase_v3.py"),
            "chrome_count": count_processes("chrome|chromedriver"),
            "offload_count": count_processes("offload_downloads.py"),
        }
    )
    sample.update(read_wg_metrics(wg_interface))
    return sample, cpu_total, cpu_idle


def main():
    args = parse_args()
    interval = max(1, int(args.interval))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    iface_name = args.iface.strip() or detect_default_interface()
    if not iface_name:
        raise SystemExit("Could not determine network interface")
    if not (Path("/sys/class/net") / iface_name).exists():
        raise SystemExit(f"Network interface not found: {iface_name}")

    disk_path = Path(args.disk_path)
    if not disk_path.exists():
        raise SystemExit(f"Disk path does not exist: {disk_path}")

    prev_cpu_total, prev_cpu_idle = read_cpu_totals()
    start_time = time.time()

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        while True:
            sample, prev_cpu_total, prev_cpu_idle = build_sample(
                iface_name,
                str(disk_path),
                args.wg_interface.strip(),
                prev_cpu_total,
                prev_cpu_idle,
            )
            writer.writerow(sample)
            f.flush()

            if args.duration_seconds > 0 and (time.time() - start_time) >= args.duration_seconds:
                break
            time.sleep(interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

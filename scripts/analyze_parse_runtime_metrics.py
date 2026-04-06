#!/usr/bin/env python3
"""Analyze parse runtime metrics and extrapolate to a higher worker count."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


DEFAULT_THRESHOLDS = {
    "max_tcp_inuse_to_start_worker": 120,
    "max_tcp_timewait_to_start_worker": 500,
    "max_tcp_orphan_to_start_worker": 32,
}


SCALABLE_METRICS = [
    "rx_mib_s",
    "tx_mib_s",
    "tcp_inuse",
    "tcp_tw",
    "tcp_orphan",
    "tcp_alloc",
    "ss_established",
    "ss_time_wait",
    "ss_close_wait",
    "ss_syn_sent",
    "ss_last_ack",
    "chrome_count",
]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", help="Path to metrics CSV")
    parser.add_argument("--from-workers", type=int, required=True, help="Observed configured worker count")
    parser.add_argument("--to-workers", type=int, default=10, help="Target worker count for extrapolation")
    parser.add_argument(
        "--config",
        default="",
        help="Optional config JSON path to read current socket pressure thresholds from",
    )
    return parser.parse_args()


def read_rows(csv_path):
    rows = []
    with Path(csv_path).open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def parse_float_series(rows, key):
    values = []
    for row in rows:
        raw = row.get(key, "")
        if raw in ("", None):
            continue
        try:
            values.append(float(raw))
        except Exception:
            continue
    return values


def parse_int_series(rows, key):
    values = []
    for row in rows:
        raw = row.get(key, "")
        if raw in ("", None):
            continue
        try:
            values.append(int(float(raw)))
        except Exception:
            continue
    return values


def avg(values):
    return sum(values) / len(values) if values else 0.0


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * pct
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return float(ordered[lower])
    weight = rank - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def bytes_to_gib(value):
    return float(value) / (1024 ** 3)


def load_thresholds(config_path):
    thresholds = dict(DEFAULT_THRESHOLDS)
    if not config_path:
        return thresholds
    path = Path(config_path)
    if not path.exists():
        return thresholds
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return thresholds
    workers_cfg = payload.get("workers", {})
    for key in thresholds:
        if key in workers_cfg:
            try:
                thresholds[key] = int(workers_cfg[key])
            except Exception:
                continue
    return thresholds


def main():
    args = parse_args()
    csv_path = Path(args.csv_path)
    rows = read_rows(csv_path)
    if not rows:
        print("No rows found")
        return 1

    first = rows[0]
    last = rows[-1]
    epochs = parse_int_series(rows, "epoch")
    duration_seconds = max(0, epochs[-1] - epochs[0]) if len(epochs) >= 2 else 0

    rx_bytes = parse_int_series(rows, "rx_bytes")
    tx_bytes = parse_int_series(rows, "tx_bytes")
    rx_mib_s = []
    tx_mib_s = []
    for prev, current in zip(rows, rows[1:]):
        dt = max(1, int(current["epoch"]) - int(prev["epoch"]))
        rx_rate = (int(current["rx_bytes"]) - int(prev["rx_bytes"])) / dt / (1024 ** 2)
        tx_rate = (int(current["tx_bytes"]) - int(prev["tx_bytes"])) / dt / (1024 ** 2)
        rx_mib_s.append(max(0.0, rx_rate))
        tx_mib_s.append(max(0.0, tx_rate))

    observed_active_downloaders = parse_int_series(rows, "downloader_count")
    active_downloaders_nonzero = [value for value in observed_active_downloaders if value > 0]
    if active_downloaders_nonzero:
        observed_downloader_peak = max(active_downloaders_nonzero)
        observed_downloader_avg = avg(active_downloaders_nonzero)
    else:
        observed_downloader_peak = 0
        observed_downloader_avg = 0.0

    scaling_ratio = (args.to_workers / args.from_workers) if args.from_workers > 0 else 0.0
    thresholds = load_thresholds(args.config)

    print(f"csv: {csv_path}")
    print(f"samples: {len(rows)}")
    print(f"from: {first['timestamp']}")
    print(f"to: {last['timestamp']}")
    print(f"duration_seconds: {duration_seconds}")
    print()
    print(f"configured_workers_observed: {args.from_workers}")
    print(f"configured_workers_target: {args.to_workers}")
    print(f"linear_scaling_ratio: {scaling_ratio:.2f}")
    print(f"downloader_count_avg_active: {observed_downloader_avg:.2f}")
    print(f"downloader_count_peak: {observed_downloader_peak}")
    print()

    cpu = parse_float_series(rows, "cpu_pct")
    load1 = parse_float_series(rows, "load1")
    mem_used = parse_int_series(rows, "mem_used_bytes")
    mem_avail = parse_int_series(rows, "mem_available_bytes")
    swap_used = parse_int_series(rows, "swap_used_bytes")
    mem_total = (mem_used[0] + mem_avail[0]) if mem_used and mem_avail else 0
    if cpu:
        print(f"cpu_avg_pct: {avg(cpu):.2f}")
        print(f"cpu_p95_pct: {percentile(cpu, 0.95):.2f}")
        print(f"cpu_peak_pct: {max(cpu):.2f}")
    if load1:
        print(f"load1_avg: {avg(load1):.2f}")
        print(f"load1_peak: {max(load1):.2f}")
    if mem_total:
        print(f"mem_total_gib: {bytes_to_gib(mem_total):.2f}")
        print(f"mem_used_avg_gib: {bytes_to_gib(avg(mem_used)):.2f}")
        print(f"mem_used_peak_gib: {bytes_to_gib(max(mem_used)):.2f}")
        print(f"swap_used_peak_gib: {bytes_to_gib(max(swap_used)):.2f}")
    print()

    total_rx_delta = max(0, rx_bytes[-1] - rx_bytes[0]) if len(rx_bytes) >= 2 else 0
    total_tx_delta = max(0, tx_bytes[-1] - tx_bytes[0]) if len(tx_bytes) >= 2 else 0
    print(f"rx_delta_gib: {bytes_to_gib(total_rx_delta):.2f}")
    print(f"tx_delta_gib: {bytes_to_gib(total_tx_delta):.2f}")
    print(f"rx_avg_mib_s: {avg(rx_mib_s):.2f}")
    print(f"rx_p95_mib_s: {percentile(rx_mib_s, 0.95):.2f}")
    print(f"rx_peak_mib_s: {max(rx_mib_s) if rx_mib_s else 0.0:.2f}")
    print(f"tx_avg_mib_s: {avg(tx_mib_s):.2f}")
    print(f"tx_p95_mib_s: {percentile(tx_mib_s, 0.95):.2f}")
    print(f"tx_peak_mib_s: {max(tx_mib_s) if tx_mib_s else 0.0:.2f}")
    print()

    print("socket_observed:")
    observed_socket_metrics = {}
    for key in [
        "tcp_inuse",
        "tcp_tw",
        "tcp_orphan",
        "tcp_alloc",
        "ss_established",
        "ss_time_wait",
        "ss_close_wait",
        "ss_syn_sent",
        "ss_last_ack",
        "chrome_count",
    ]:
        series = parse_int_series(rows, key)
        if not series:
            continue
        observed_socket_metrics[key] = {
            "avg": avg(series),
            "p95": percentile(series, 0.95),
            "peak": max(series),
        }
        print(
            f"  {key}: avg={observed_socket_metrics[key]['avg']:.2f} "
            f"p95={observed_socket_metrics[key]['p95']:.2f} "
            f"peak={observed_socket_metrics[key]['peak']:.2f}"
        )
    print()

    print("projection_linear_to_target_workers:")
    projected = {}
    for key in SCALABLE_METRICS:
        if key in ("rx_mib_s", "tx_mib_s"):
            series = rx_mib_s if key == "rx_mib_s" else tx_mib_s
        else:
            series = parse_float_series(rows, key)
        if not series:
            continue
        projected[key] = {
            "avg": avg(series) * scaling_ratio,
            "p95": percentile(series, 0.95) * scaling_ratio,
            "peak": max(series) * scaling_ratio,
        }
        print(
            f"  {key}: avg={projected[key]['avg']:.2f} "
            f"p95={projected[key]['p95']:.2f} "
            f"peak={projected[key]['peak']:.2f}"
        )
    print()

    print("pressure_gate_check_against_projection:")
    tcp_inuse_proj = projected.get("tcp_inuse", {})
    tcp_tw_proj = projected.get("tcp_tw", {})
    tcp_orphan_proj = projected.get("tcp_orphan", {})
    checks = [
        ("tcp_inuse", thresholds["max_tcp_inuse_to_start_worker"], tcp_inuse_proj.get("peak", 0.0)),
        ("tcp_tw", thresholds["max_tcp_timewait_to_start_worker"], tcp_tw_proj.get("peak", 0.0)),
        ("tcp_orphan", thresholds["max_tcp_orphan_to_start_worker"], tcp_orphan_proj.get("peak", 0.0)),
    ]
    for label, threshold, projected_peak in checks:
        verdict = "EXCEEDS" if projected_peak >= threshold else "below"
        print(f"  {label}: projected_peak={projected_peak:.2f} threshold={threshold} => {verdict}")

    wg_handshake = parse_int_series(rows, "wg_handshake_age_seconds")
    if wg_handshake:
        valid_handshakes = [value for value in wg_handshake if value >= 0]
        if valid_handshakes:
            print()
            print(f"wg_handshake_age_peak_seconds: {max(valid_handshakes)}")
            print(f"wg_handshake_age_p95_seconds: {percentile(valid_handshakes, 0.95):.2f}")
        else:
            print()
            print("wg_handshake_age: no valid handshakes recorded")

    print()
    print("note: projection is linear and conservative only as a first approximation.")
    print("note: browser stalls, provider limits, and socket-pressure gate make real 10-worker behavior non-linear.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

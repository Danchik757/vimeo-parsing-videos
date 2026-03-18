#!/usr/bin/env python3
"""Summarize machine metrics captured by collect_system_metrics.sh."""

import argparse
import csv
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Summarize collected system metrics CSV")
    parser.add_argument("csv_path", help="Path to metrics CSV file")
    return parser.parse_args()


def bytes_to_gib(value):
    return value / (1024 ** 3)


def main():
    args = parse_args()
    csv_path = Path(args.csv_path)
    rows = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
      reader = csv.DictReader(f)
      for row in reader:
          rows.append(row)

    if not rows:
        print("No rows found")
        return 1

    first = rows[0]
    last = rows[-1]

    epochs = [int(r["epoch"]) for r in rows]
    cpu = [float(r["cpu_pct"]) for r in rows]
    mem_used = [int(r["mem_used_bytes"]) for r in rows]
    mem_avail = [int(r["mem_available_bytes"]) for r in rows]
    swap_used = [int(r["swap_used_bytes"]) for r in rows]
    disk_used = [int(r["disk_used_bytes"]) for r in rows]
    disk_avail = [int(r["disk_avail_bytes"]) for r in rows]
    rx = [int(r["rx_bytes"]) for r in rows]
    tx = [int(r["tx_bytes"]) for r in rows]
    load1 = [float(r["load1"]) for r in rows]
    load5 = [float(r["load5"]) for r in rows]
    chrome = [int(r["chrome_count"]) for r in rows]
    downloaders = [int(r["downloader_count"]) for r in rows]

    duration = max(0, epochs[-1] - epochs[0])
    rx_delta = max(0, rx[-1] - rx[0])
    tx_delta = max(0, tx[-1] - tx[0])
    disk_delta = disk_used[-1] - disk_used[0]

    mem_total = mem_used[0] + mem_avail[0]
    mem_peak_pct = (max(mem_used) / mem_total * 100) if mem_total else 0.0

    def avg(values):
        return sum(values) / len(values)

    print(f"csv: {csv_path}")
    print(f"samples: {len(rows)}")
    print(f"from: {first['timestamp']}")
    print(f"to: {last['timestamp']}")
    print(f"duration_seconds: {duration}")
    print()
    print(f"cpu_avg_pct: {avg(cpu):.2f}")
    print(f"cpu_peak_pct: {max(cpu):.2f}")
    print(f"load1_avg: {avg(load1):.2f}")
    print(f"load1_peak: {max(load1):.2f}")
    print(f"load5_peak: {max(load5):.2f}")
    print()
    print(f"mem_total_gib: {bytes_to_gib(mem_total):.2f}")
    print(f"mem_used_avg_gib: {bytes_to_gib(avg(mem_used)):.2f}")
    print(f"mem_used_peak_gib: {bytes_to_gib(max(mem_used)):.2f}")
    print(f"mem_peak_pct: {mem_peak_pct:.2f}")
    print(f"swap_used_peak_gib: {bytes_to_gib(max(swap_used)):.2f}")
    print()
    print(f"disk_used_start_gib: {bytes_to_gib(disk_used[0]):.2f}")
    print(f"disk_used_end_gib: {bytes_to_gib(disk_used[-1]):.2f}")
    print(f"disk_used_delta_gib: {bytes_to_gib(disk_delta):.2f}")
    print(f"disk_avail_end_gib: {bytes_to_gib(disk_avail[-1]):.2f}")
    print()
    print(f"rx_delta_gib: {bytes_to_gib(rx_delta):.2f}")
    print(f"tx_delta_gib: {bytes_to_gib(tx_delta):.2f}")
    if duration > 0:
        print(f"rx_avg_mib_s: {rx_delta / duration / (1024 ** 2):.2f}")
        print(f"tx_avg_mib_s: {tx_delta / duration / (1024 ** 2):.2f}")
    else:
        print("rx_avg_mib_s: 0.00")
        print("tx_avg_mib_s: 0.00")
    print()
    print(f"downloader_count_peak: {max(downloaders)}")
    print(f"chrome_count_peak: {max(chrome)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

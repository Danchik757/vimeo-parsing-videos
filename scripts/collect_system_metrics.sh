#!/bin/bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: collect_system_metrics.sh --output PATH [--interval SECONDS] [--iface IFACE] [--disk-path PATH]

Collects machine metrics into a CSV file until interrupted.
EOF
}

INTERVAL=10
OUTPUT=""
IFACE=""
DISK_PATH="."

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT="${2:-}"
      shift 2
      ;;
    --interval)
      INTERVAL="${2:-}"
      shift 2
      ;;
    --iface)
      IFACE="${2:-}"
      shift 2
      ;;
    --disk-path)
      DISK_PATH="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$OUTPUT" ]]; then
  echo "--output is required" >&2
  usage >&2
  exit 1
fi

if [[ -z "$IFACE" ]]; then
  IFACE="$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')"
fi

if [[ -z "$IFACE" || ! -d "/sys/class/net/$IFACE" ]]; then
  echo "Could not determine network interface" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"

read_cpu_totals() {
  awk '/^cpu / {print $2+$3+$4+$5+$6+$7+$8+$9, $5+$6}' /proc/stat
}

read_meminfo_value() {
  local key="$1"
  awk -v needle="$key" '$1 == needle ":" {print $2 * 1024}' /proc/meminfo
}

read_disk_usage() {
  df -B1 --output=used,avail "$DISK_PATH" | tail -n1 | awk '{print $1","$2}'
}

count_processes() {
  local pattern="$1"
  pgrep -fc "$pattern" 2>/dev/null || true
}

PREV_CPU="$(read_cpu_totals)"
PREV_TOTAL="$(awk '{print $1}' <<<"$PREV_CPU")"
PREV_IDLE="$(awk '{print $2}' <<<"$PREV_CPU")"

cat > "$OUTPUT" <<EOF
timestamp,epoch,cpu_pct,load1,load5,load15,mem_used_bytes,mem_available_bytes,swap_used_bytes,disk_used_bytes,disk_avail_bytes,rx_bytes,tx_bytes,run_workers_count,downloader_count,chrome_count
EOF

echo "Collecting metrics to $OUTPUT every ${INTERVAL}s on iface $IFACE and disk path $DISK_PATH"

while true; do
  NOW_EPOCH="$(date +%s)"
  NOW_TS="$(date '+%Y-%m-%dT%H:%M:%S%z')"

  CPU_NOW="$(read_cpu_totals)"
  CPU_TOTAL="$(awk '{print $1}' <<<"$CPU_NOW")"
  CPU_IDLE="$(awk '{print $2}' <<<"$CPU_NOW")"
  CPU_TOTAL_DELTA=$((CPU_TOTAL - PREV_TOTAL))
  CPU_IDLE_DELTA=$((CPU_IDLE - PREV_IDLE))
  CPU_PCT="$(awk -v total="$CPU_TOTAL_DELTA" -v idle="$CPU_IDLE_DELTA" 'BEGIN { if (total <= 0) { printf "0.00" } else { printf "%.2f", ((total - idle) / total) * 100 } }')"
  PREV_TOTAL="$CPU_TOTAL"
  PREV_IDLE="$CPU_IDLE"

  read -r LOAD1 LOAD5 LOAD15 _ < /proc/loadavg

  MEM_TOTAL="$(read_meminfo_value MemTotal)"
  MEM_AVAILABLE="$(read_meminfo_value MemAvailable)"
  SWAP_TOTAL="$(read_meminfo_value SwapTotal)"
  SWAP_FREE="$(read_meminfo_value SwapFree)"
  MEM_USED=$((MEM_TOTAL - MEM_AVAILABLE))
  SWAP_USED=$((SWAP_TOTAL - SWAP_FREE))

  DISK_FIELDS="$(read_disk_usage)"
  DISK_USED="${DISK_FIELDS%,*}"
  DISK_AVAIL="${DISK_FIELDS#*,}"

  RX_BYTES="$(cat "/sys/class/net/$IFACE/statistics/rx_bytes")"
  TX_BYTES="$(cat "/sys/class/net/$IFACE/statistics/tx_bytes")"

  RUN_WORKERS_COUNT="$(count_processes 'python .*run_workers.py')"
  DOWNLOADER_COUNT="$(count_processes 'python .*download_vimeo_seleniumbase_v3.py')"
  CHROME_COUNT="$(count_processes 'chrome|chromedriver')"

  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$NOW_TS" \
    "$NOW_EPOCH" \
    "$CPU_PCT" \
    "$LOAD1" \
    "$LOAD5" \
    "$LOAD15" \
    "$MEM_USED" \
    "$MEM_AVAILABLE" \
    "$SWAP_USED" \
    "$DISK_USED" \
    "$DISK_AVAIL" \
    "$RX_BYTES" \
    "$TX_BYTES" \
    "$RUN_WORKERS_COUNT" \
    "$DOWNLOADER_COUNT" \
    "$CHROME_COUNT" >> "$OUTPUT"

  sleep "$INTERVAL"
done

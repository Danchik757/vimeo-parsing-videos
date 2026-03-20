# Runbook

## 1. Prerequisites

### Python Environment

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Linux Packages

```bash
sudo apt update
sudo apt install -y google-chrome-stable xvfb ffmpeg cifs-utils smbclient
```

`ffprobe` comes from `ffmpeg` and is required for the offload verifier.

## 2. Input Lists

The repository contains two important input files:

- `need_parse_unique.json`: `694343` unique Vimeo URLs
- `need_parse.json`: `2630344` URLs with duplicates

For production sharding between servers use `need_parse_unique.json`.

## 3. Config Layout

Recommended split:

- `config.profile.example.json`: machine/run-specific settings
- `config.secrets.example.json`: tokens, bot credentials, Vimeo login, worker API pool

Create your real files, for example:

- `config.parse-1.profile.json`
- `config.parse-1.secrets.json`

Then point the profile to the secrets file:

```json
"secrets_file": "config.parse-1.secrets.json"
```

### Per-Server Separation

Each server should have its own:

- Vimeo account
- Vimeo login in `vimeo_login`
- Telegram bot
- Telegram chat id
- API pool in `workers.api_pool`
- output root
- storage root, for example `.../parse-1`, `.../parse-2`, `.../parse-3`

For Telegram separation set the server label directly in:

```json
"runtime": {
  "job_name": "parse-1"
}
```

Then headers become:

- `parse-1 | coordinator`
- `parse-1 | worker-07`

## 4. Storage Mount

Example for mounted storage on Linux:

```bash
mkdir -p ~/mount/credentials
mkdir -p ~/mount/mimas
cat > ~/mount/credentials/mimas_creds << 'EOF'
username=YOUR_LOGIN
password=YOUR_PASSWORD
domain=GRAPHICS2
EOF
chmod 600 ~/mount/credentials/mimas_creds
```

List shares:

```bash
sudo smbclient -L //mimas -A /home/msu_cc/mount/credentials/mimas_creds -m SMB3
```

Mount a specific share:

```bash
sudo mount -t cifs //mimas/SHARE /home/msu_cc/mount/mimas \
  -o credentials=/home/msu_cc/mount/credentials/mimas_creds,uid=$(id -u),gid=$(id -g),iocharset=utf8,file_mode=0664,dir_mode=0775,vers=3.1.1,sec=ntlmssp
```

For this project the final path can look like:

```text
/home/msu_cc/mount/mimas/Datasets/vimeo/2025scraped/parse-1
```

## 5. Recommended Parser Settings

For the current production pattern:

```json
"runtime": {
  "vimeo_authenticated_session": true
},
"settings": {
  "download_only_original": true,
  "download_interface": "",
  "download_retry_interface": "telegram-wg",
  "direct_download_timeout_seconds": 10,
  "curl_stall_timeout_seconds": 600
},
"workers": {
  "count": 15,
  "stagger_start_seconds": 30,
  "shared_media_dirs": true
},
"telegram": {
  "notify_on_start": true,
  "notify_on_finish": true,
  "notify_on_error": false,
  "notify_download_every_n_successes": 0,
  "notify_skip_every_n_processed": 0,
  "notify_progress_every_n_processed": 0,
  "notify_progress_min_interval_seconds": 1800,
  "notify_coordinator_progress_every_seconds": 1800
}
```

Meaning:

- API/page parsing stays direct
- direct Vimeo media path is checked quickly and falls back after 10 seconds
- only failed media downloads retry through WireGuard
- worker starts are staggered to reduce Vimeo login collisions
- worker download/skip/progress spam is disabled
- worker start/finish still remains
- coordinator progress is limited to once every 30 minutes

## 6. Start Metrics Collection

```bash
mkdir -p output/runs/parse-1/metrics

nohup ./scripts/collect_system_metrics.sh \
  --output output/runs/parse-1/metrics/system_metrics_telegram-wg.csv \
  --interval 10 \
  --iface telegram-wg \
  --disk-path /29d_kon \
  > output/runs/parse-1/metrics/collector_telegram-wg.log 2>&1 &
echo $! > output/runs/parse-1/metrics/collector_telegram-wg.pid

nohup ./scripts/collect_system_metrics.sh \
  --output output/runs/parse-1/metrics/system_metrics_enp2s0.csv \
  --interval 10 \
  --iface enp2s0 \
  --disk-path /29d_kon \
  > output/runs/parse-1/metrics/collector_enp2s0.log 2>&1 &
echo $! > output/runs/parse-1/metrics/collector_enp2s0.pid
```

## 7. Start The Parser

```bash
tmux new -s vimeo-parse-1
```

Inside `tmux`:

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
source venv/bin/activate
python run_workers.py --config config.parse-1.profile.json --workers 15
```

If you already prepared fixed `10000`-URL shards for a server, for example:

- `data_shards/server_assignments_10000/parse-1/manifest.json`

and want each worker slot to keep pulling the next unfinished shard until the queue is empty, use:

```bash
python run_assigned_shards.py \
  --config config.parse-1.profile.json \
  --manifest data_shards/server_assignments_10000/parse-1/manifest.json \
  --workers 15
```

This queue mode is the recommended production path when shard difficulty is uneven, because worker slots are no longer tied to exactly `3` shard files each.

If your login credentials are not stored in `vimeo_login`, you can still override by environment:

```bash
VIMEO_EMAIL='...' VIMEO_PASSWORD='...' python run_workers.py --config config.parse-1.profile.json --workers 15
```

## 8. Start Offload Watcher

Run this in a second shell or second `tmux` pane:

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
source venv/bin/activate
python scripts/offload_downloads.py --config config.parse-1.profile.json --watch
```

If you want the watcher to delete local media after verified upload:

```bash
python scripts/offload_downloads.py --config config.parse-1.profile.json --watch --delete-local-video
```

### Who Runs The Offload Script

Use one of these patterns:

- manual second `tmux` pane during a long run
- `systemd` service on the server
- cron or supervisor job if you prefer periodic scans

Recommended production pattern:

- parser in one long-lived `tmux`/service
- offload watcher in a second long-lived `tmux`/service

This is better than waiting until the end of a `10000` URL run, because local disk can fill up first.

## 9. Monitoring

Coordinator:

```bash
tail -f output/runs/parse-1/workers/coordinator.log
```

Single worker:

```bash
tail -f output/runs/parse-1/workers/worker_07/download.log
```

Login failures:

```bash
grep -R "Login appears incomplete\\|login_failure_attempt" -n output/runs/parse-1/workers/worker_*/download.log
```

WG download retries:

```bash
grep -R "Retrying via interface telegram-wg\\|via curl on interface telegram-wg" -n output/runs/parse-1/workers/worker_*/download.log
```

Realtime CPU/RAM/disk:

```bash
watch -n 2 'free -h; echo; free | awk '\''/Mem:/ {printf "RAM used: %.1f%%\n", ($3/$2)*100}'\''; free | awk '\''/Swap:/ {if ($2>0) printf "Swap used: %.1f%%\n", ($3/$2)*100; else print "Swap used: 0.0%"}'\''; echo; df -h /29d_kon; df /29d_kon | awk '\''NR==2 {print "Disk used:", $5}'\'''
```

Realtime interface counters:

```bash
watch -n 2 "ip -s link show telegram-wg | sed -n '1,8p'; echo; ip -s link show enp2s0 | sed -n '1,8p'"
```

## 10. Stop The Run

Attach:

```bash
tmux attach -t vimeo-parse-1
```

Stop parser:

```text
Ctrl-C
```

Stop metrics:

```bash
kill "$(cat output/runs/parse-1/metrics/collector_telegram-wg.pid)"
kill "$(cat output/runs/parse-1/metrics/collector_enp2s0.pid)"
```

Stop offload watcher:

```bash
pkill -f "scripts/offload_downloads.py --config config.parse-1.profile.json"
```

## 11. Resume

Resume works as long as you keep:

- `workers/worker_XX/resume_state.json`
- `workers/worker_XX/results_manifest.json`
- local metadata JSONs in `videos/downloaded`, `videos/not_downloaded`, `videos/no_links`

After offload, local `.mp4` may be deleted, but local metadata JSON remains. That is enough for resume to skip already uploaded originals.

## 12. End-Of-Run Exports

The coordinator now writes three URL lists automatically into `workers/`:

- `downloaded_original_urls.txt`
- `not_downloaded_downloadable_urls.txt`
- `no_links_urls.txt`

You can also regenerate them manually:

```bash
python scripts/export_result_lists.py output/runs/parse-1/workers/aggregate_results_manifest.json
```

## 13. Metrics Summary

After the run:

```bash
python scripts/summarize_system_metrics.py output/runs/parse-1/metrics/system_metrics_telegram-wg.csv > output/runs/parse-1/metrics/summary_telegram-wg.txt
python scripts/summarize_system_metrics.py output/runs/parse-1/metrics/system_metrics_enp2s0.csv > output/runs/parse-1/metrics/summary_enp2s0.txt
```

## 14. Recommended Multi-Server Layout

Example split:

- `parse-1`: server 1, Vimeo account 1, Telegram bot 1, storage root `.../parse-1`
- `parse-2`: server 2, Vimeo account 2, Telegram bot 2, storage root `.../parse-2`
- `parse-3`: server 3, Vimeo account 3, Telegram bot 3, storage root `.../parse-3`

Each server should receive a disjoint input shard or batch range.

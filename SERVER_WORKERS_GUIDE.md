# Server Workers Guide

This file is the short entry point for server runs.

The detailed version is now split into:

- `docs/ARCHITECTURE.md`
- `docs/RUNBOOK.md`

## Production Checklist

1. Mount VPN and storage.
2. Prepare profile config and secrets config.
3. Start metrics collectors.
4. Start `run_workers.py` in `tmux`.
5. Start `scripts/offload_downloads.py` in a second `tmux` pane.
6. Monitor `workers/coordinator.log`.
7. Stop collectors and generate metrics summaries after the run.

## Recommended Production Settings

```json
"runtime": {
  "vimeo_authenticated_session": true
},
"settings": {
  "download_only_original": true,
  "download_interface": "",
  "download_retry_interface": "telegram-wg"
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
  "notify_progress_min_interval_seconds": 1800,
  "notify_coordinator_progress_every_seconds": 1800
}
```

## Launch

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
source venv/bin/activate
tmux new -s vimeo-parse-1
python run_workers.py --config config.parse-1.profile.json --workers 15
```

Second pane for offload:

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
source venv/bin/activate
python scripts/offload_downloads.py --config config.parse-1.profile.json --watch --delete-local-video
```

## Monitoring

```bash
tail -f output/runs/parse-1/workers/coordinator.log
```

```bash
grep -R "Login appears incomplete\\|login_failure_attempt" -n output/runs/parse-1/workers/worker_*/download.log
```

```bash
grep -R "Retrying via interface telegram-wg\\|via curl on interface telegram-wg" -n output/runs/parse-1/workers/worker_*/download.log
```

## Resume

Do not delete local metadata JSONs if you want resume to stay correct after offload.

The uploader removes only the local media file. Resume still works because `_offload.status="uploaded"` stays in the local metadata JSON.

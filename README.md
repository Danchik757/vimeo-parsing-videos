# Vimeo Original Downloader

Production-oriented Vimeo parser/downloader for large URL lists with:

- worker coordinator
- authenticated Vimeo page parsing
- original-only download policy
- Telegram monitoring
- resume support
- metrics collection
- storage offload with verification

## Main Entry Points

- `run_workers.py`: shard one input list across N workers
- `download_vimeo_seleniumbase_v3.py`: single worker
- `scripts/offload_downloads.py`: verified upload of completed downloads to mounted storage
- `scripts/collect_system_metrics.sh`: machine metrics over time
- `scripts/summarize_system_metrics.py`: post-run metrics summary

## Input Lists

- `need_parse_unique.json`: `694343` unique Vimeo URLs
- `need_parse.json`: `2630344` URLs with duplicates

For real processing use `need_parse_unique.json`.

## Config Pattern

Recommended split:

- profile config: machine/run-specific settings and paths
- secrets config: Vimeo login, Vimeo API tokens, Telegram bot, worker API pool

Included examples:

- `config.example.json`
- `config.profile.example.json`
- `config.secrets.example.json`

`config.profile.example.json` points to the secrets file through:

```json
"secrets_file": "config.secrets.example.json"
```

## Current Network Model

Recommended production settings:

- parse/API/page flow: direct
- media download: direct first
- failed media download retry: `telegram-wg`

Config:

```json
"settings": {
  "download_interface": "",
  "download_retry_interface": "telegram-wg"
}
```

## Output Buckets

- `videos/downloaded/<id>/`: successfully downloaded original
- `videos/not_downloaded/<id>.json`: downloadable later, but not downloaded now
- `videos/no_links/<id>.json`: no usable download link

Coordinator also exports:

- `workers/downloaded_original_urls.txt`
- `workers/not_downloaded_downloadable_urls.txt`
- `workers/no_links_urls.txt`

## Docs

- `docs/ARCHITECTURE.md`: pipeline, metadata model, logging, resume, offload
- `docs/RUNBOOK.md`: full setup and operational instructions
- `SERVER_WORKERS_GUIDE.md`: short server-oriented entry doc
- `WORKING_VERSION_GUIDE.md`: legacy snapshot of the older approach

## Minimal Start

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run_workers.py --config config.profile.example.json --workers 2
```

For the full production flow, use `docs/RUNBOOK.md`.

# Vimeo Original Downloader

Production-oriented Vimeo parser/downloader for large URL lists with:

- dynamic shard queue coordinator
- authenticated Vimeo page parsing
- original-only download policy
- Telegram monitoring
- resume support
- batch retries
- storage offload with verification

## Main Entry Points

- `run_assigned_shards.py`: queue runner for pre-generated `parse-1/parse-2/parse-3` manifests
- `run_workers.py`: fixed split runner for a single input list
- `download_vimeo_seleniumbase_v3.py`: one worker process
- `scripts/offload_downloads.py`: verified upload of completed downloads to mounted storage
- `scripts/collect_system_metrics.sh`: optional machine metrics collector
- `scripts/summarize_system_metrics.py`: optional post-run metrics summary

## Repository And Branches

Repository:

- `https://github.com/Danchik757/vimeo-parsing-videos.git`

Current branch layout:

- `main`: shared baseline
- `codex/parse-1-production`: branch for the first server
- `codex/parse-2-production`: branch for the second server

The intent is to keep server-specific operational changes off `main`.

## Input Lists

- `need_parse_unique.json`: `694343` unique Vimeo URLs
- `need_parse.json`: `2630344` URLs with duplicates

For real processing use `need_parse_unique.json`.

Pre-generated `10000`-URL shard assignments already exist:

- `data_shards/server_assignments_10000/parse-1`
- `data_shards/server_assignments_10000/parse-2`
- `data_shards/server_assignments_10000/parse-3`

Current split:

- `parse-1`: `24` shard batches
- `parse-2`: `18` shard batches
- `parse-3`: `28` shard batches

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

## Current Production Queue Model

- `run_assigned_shards.py` starts N worker slots
- each slot takes the next `pending` shard batch
- fast slots can process more shard batches than slow ones
- one failed batch does not have to stop the whole run
- failed batches can be re-queued automatically up to a configured retry limit

Recommended queue settings:

```json
"batches": {
  "stop_on_batch_error": false,
  "max_batch_retries": 2
}
```

## Current Network Model

Current recommended production settings for `parse-1`:

- parse/API/page flow: direct
- media download: directly through `telegram-wg`
- no direct-first media attempt
- original-only mode enabled

Config:

```json
"settings": {
  "download_interface": "telegram-wg",
  "download_retry_interface": "",
  "direct_download_timeout_seconds": 10,
  "curl_stall_timeout_seconds": 60
}
```

## Output Buckets

- `videos/downloaded/<id>/`: successfully downloaded original media and metadata
- `videos/not_downloaded/<id>.json`: compact metadata for downloadable-but-not-original videos
- `workers/no_links_urls.txt`: URLs with no usable download link

Coordinator also exports:

- `workers/downloaded_original_urls.txt`
- `workers/not_downloaded_downloadable_urls.txt`
- `workers/no_links_urls.txt`

Queue/offload state files:

- `workers/assignment_state.json`
- `workers/aggregate_summary.json`
- `workers/aggregate_results_manifest.json`
- `offload_registry.json`

Offload behavior:

- watcher uploads only completed local media files
- media is verified with `ffprobe`
- local `.mp4` can be deleted after verified upload
- watcher is restart-safe through `offload_registry.json` and `_offload` metadata

## Docs

- `ARCHITECTURE.md`: pipeline, metadata model, logging, resume, offload
- `RUNBOOK.md`: full setup and operational instructions
- `SERVER_WORKERS_GUIDE.md`: short server-oriented entry doc
- `README.md`: docs index and quick navigation
- `WORKING_VERSION_GUIDE.md`: legacy snapshot of the older approach
- `../PARSE1_PRODUCTION_OPERATOR_GUIDE.txt`: current parse-1 operator guide

## Minimal Queue Start

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run_assigned_shards.py \
  --config config.profile.example.json \
  --manifest data_shards/server_assignments_10000/parse-1/manifest.json \
  --workers 2
```

Parallel watcher:

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
source venv/bin/activate
python scripts/offload_downloads.py --config config.profile.example.json --watch --delete-local-video
```

For the full production flow, use `RUNBOOK.md`.

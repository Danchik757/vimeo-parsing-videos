# Architecture

## What The Pipeline Does

The project processes Vimeo URLs in four stages:

1. `run_workers.py` shards the source list and launches worker processes.
2. Each worker runs `download_vimeo_seleniumbase_v3.py`.
3. The worker first probes Vimeo API metadata, then opens the page only when needed.
4. If `download_only_original=true`, the worker downloads only the `Original` option and records everything else as metadata.

The current production pattern is:

- API and page parsing go through the normal network path.
- Direct media download is attempted normally first.
- If the download stalls, the worker retries only the media download through `settings.download_retry_interface`, for example `telegram-wg`.

## Main Files

- `download_vimeo_seleniumbase_v3.py`: single worker, download logic, metadata, resume.
- `run_workers.py`: worker coordinator, sharding, aggregate summaries.
- `telegram_notifier.py`: async Telegram sender with rate controls.
- `scripts/offload_downloads.py`: uploads completed media to mounted storage and optionally removes the local media file.
- `scripts/export_result_lists.py`: exports the three URL buckets from a results manifest.
- `scripts/collect_system_metrics.sh`: samples CPU, RAM, disk, interface counters.
- `scripts/summarize_system_metrics.py`: summarizes collected metrics.

## Output Layout

Typical worker run output:

```text
output/run_name/
├── videos/
│   ├── downloaded/
│   │   └── 1058440992/
│   │       ├── 1058440992.mp4
│   │       └── 1058440992.json
│   ├── not_downloaded/
│   │   └── 1064731121.json
│   └── no_links/
│       └── 1069999999.json
├── workers/
│   ├── coordinator.log
│   ├── aggregate_summary.json
│   ├── aggregate_results_manifest.json
│   ├── downloaded_original_urls.txt
│   ├── not_downloaded_downloadable_urls.txt
│   ├── no_links_urls.txt
│   └── worker_01/
│       ├── config.json
│       ├── download.log
│       ├── results_manifest.json
│       ├── resume_state.json
│       └── summary.json
├── failed_downloads.json
└── metrics/
```

## Why Per-Video JSON Contains Many Fields

Each per-video JSON is both an audit record and a resume/debug artifact.

The structure is intentional:

- `_download`: final decision and file-level facts.
- `_api`: what Vimeo API said before page parsing.
- `_page_probe`: what the real Vimeo page exposed in the download modal.
- `_storage`: where the metadata/file belongs in local storage.
- `_offload`: whether the final file was uploaded to remote storage.
- `vimeo_video`: source metadata summary or full payload.

Why this is useful:

- You can later prove why a video was skipped.
- You can extract original download links without rerunning the page flow.
- You can audit which quality was chosen.
- Resume and offload can keep working even after local media files are deleted.

### What Is Already Compact

The project already stores metadata differently by bucket:

- `downloaded/<id>/<id>.json`: richer record, because it backs downloaded media.
- `not_downloaded/<id>.json`: compact video summary plus download facts.
- `no_links/<id>.json`: compact video summary plus probe facts.

### What Can Be Reduced

The largest payload is usually `vimeo_video` for downloaded items.

There is now a config flag:

- `settings.store_full_api_payload_for_downloaded`

If you set it to `false`, downloaded metadata also stores only the compact summary instead of the full API payload.

Recommended default:

- keep it `true` while stabilizing parsing and offload.
- switch it to `false` only when you are confident that audit/debug depth is no longer needed.

## Result Buckets

### `downloaded`

The media file was downloaded successfully.

Important flags:

- `_download.status = "downloaded"`
- `_download.downloadable = true`
- `_download.is_original = true/false`
- `_download.download_link`

### `not_downloaded`

The video exposed a downloadable link, but the worker intentionally did not download it.

Typical reasons:

- best available option is not `Original`
- policy skipped it
- download link existed but you chose not to download

Important flags:

- `_download.status = "skipped"`
- `_download.downloadable = true`
- `_download.download_link` is usually present
- `_download.is_original = false`

### `no_links`

The worker found no usable download link.

Typical reasons:

- `privacy.download=false`
- `404`
- page had no download access

Important flags:

- `_download.downloadable = false`
- `_download.download_link = null`

## What `video_id` Means

`video_id` is the canonical numeric Vimeo clip id, for example `1058440992`.

It is used for:

- file and folder naming
- manifest records
- resume state
- offload registry keys
- de-duplication between reruns

The downloader tries to normalize it from:

- the original URL
- Vimeo API payload
- page context like `__NEXT_DATA__`

## Logging Layers

There are several logging layers and they solve different problems.

### Runtime Log

`workers/worker_XX/download.log`

Human-readable execution log:

- page opening
- button detection
- chosen quality
- retries
- download progress
- final outcome

### Per-URL Machine Log

`workers/worker_XX/results_manifest.json`

Structured status for every input URL:

- `pending`
- `downloaded`
- `skipped`
- `failed`

### Aggregate Machine Log

`workers/aggregate_results_manifest.json`

Same as above, but merged across all workers.

### Final Counters

`workers/aggregate_summary.json`

Coordinator summary:

- total processed
- downloaded
- skipped
- failed

### Telegram

Operational monitoring only. Not the source of truth.

### Metrics

`metrics/system_metrics_*.csv`

Machine resource usage over time.

## Telegram Delays And Throttling

Telegram sending is asynchronous and rate-limited by config:

- `notify_progress_every_n_processed`
- `notify_progress_min_interval_seconds`
- `notify_coordinator_progress_every_seconds`
- `notify_download_every_n_successes`
- `notify_skip_every_n_processed`
- `notify_on_error`

How it works:

- progress messages are sent only if the processed counter hits the configured step,
- and only if the minimum interval has passed,
- coordinator messages have their own time-based interval,
- error messages are immediate when `notify_on_error=true`.

For high-worker runs, the stable setting is:

- `notify_on_error=false`
- `notify_progress_min_interval_seconds=1800`
- `notify_coordinator_progress_every_seconds=1800`

## Resume

Each worker keeps its own:

- `resume_state.json`
- `results_manifest.json`

Resume behavior:

- if the same shard and signature are reused, the worker continues from `next_index`
- already completed local files are skipped
- already offloaded files are also skipped if their metadata JSON is still present and `_offload.status="uploaded"`

This is why the uploader should remove only the media file, not the local metadata JSON.

## Offload Model

The offload script is intentionally separate from parsing.

It scans completed downloads and:

1. ignores `.part` files
2. verifies local media with `ffprobe`
3. copies media and metadata to mounted storage
4. verifies the copied media by size and `ffprobe`
5. records the result in `offload_registry.json`
6. updates local metadata `_offload`
7. optionally deletes the local media file

This keeps local disk usage bounded without losing resume knowledge.

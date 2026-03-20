# Vimeo Downloader Docs

Repository:
- `https://github.com/Danchik757/vimeo-parsing-videos.git`

Current branch model:
- `main`: shared base
- `codex/parse-1-production`: branch for the first server
- `codex/parse-2-production`: branch for the second server

Current production model:
- server queue runner: `run_assigned_shards.py`
- worker process: `download_vimeo_seleniumbase_v3.py`
- offload watcher: `scripts/offload_downloads.py`
- output offload with `ffprobe` verification
- automatic queue retries for failed shard batches

Current parse-1 production behavior:
- `download_only_original = true`
- `vimeo_authenticated_session = true`
- media download goes directly through `telegram-wg`
- `batches.stop_on_batch_error = false`
- `batches.max_batch_retries = 2`

Main docs:
- [PROJECT_README.md](PROJECT_README.md): project overview and current architecture
- [RUNBOOK.md](RUNBOOK.md): setup, launch, restart, monitoring
- [ARCHITECTURE.md](ARCHITECTURE.md): pipeline and state model
- [SERVER_WORKERS_GUIDE.md](SERVER_WORKERS_GUIDE.md): short server-oriented notes
- [PROJECT_MEMO.md](PROJECT_MEMO.md): historical notes and decisions

parse-1 operator doc:
- [`PARSE1_PRODUCTION_OPERATOR_GUIDE.txt`](../PARSE1_PRODUCTION_OPERATOR_GUIDE.txt)

Shard references:
- [DATA_SHARDS_SERVER_ASSIGNMENTS_10000_README.md](DATA_SHARDS_SERVER_ASSIGNMENTS_10000_README.md)
- `data_shards/server_assignments_10000/parse-1/manifest.json`
- `data_shards/server_assignments_10000/parse-2/manifest.json`
- `data_shards/server_assignments_10000/parse-3/manifest.json`

Quick production start pattern:

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
source venv/bin/activate
python run_assigned_shards.py \
  --config config.parse-1.profile.json \
  --manifest data_shards/server_assignments_10000/parse-1/manifest.json \
  --workers 8
```

Parallel offload watcher:

```bash
cd /Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix
source venv/bin/activate
python scripts/offload_downloads.py --config config.parse-1.profile.json --watch --delete-local-video
```

For real operational details, use `RUNBOOK.md` and `PARSE1_PRODUCTION_OPERATOR_GUIDE.txt`.

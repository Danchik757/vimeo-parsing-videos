# Remaining URL Recovery Output

Snapshot: linux_progress_20260511.json
Assignment root: /Users/admin/Documents/LAB/CODECS/PARSING/codex_vimeo_fix/data_shards/server_assignments_10000
Original total URLs: 694343
Removed URLs: 80176
Remaining URLs: 614167
Generated batches: 62

Important assumption:
Count-based cut from coordinator progress only. Fully completed batches are removed entirely. For active batches, the first d+s+f URLs are removed in batch order. This also removes failed URLs inside the processed prefix because per-URL manifests from the old machine are unavailable.

Generated files:
- need_parse_unique.remaining_after_linux_progress_20260511.json
- manifest.json
- rebuild_report.json

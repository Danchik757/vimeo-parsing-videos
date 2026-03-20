import json
import tempfile
import unittest
from pathlib import Path

from run_assigned_shards import (
    build_progress_lines,
    load_assignment_state,
    normalize_assignment_manifest,
    resolve_manifest_source_json,
    save_assignment_state,
    select_batches,
)


class _LoggerStub:
    def warning(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None


class AssignedShardsTests(unittest.TestCase):
    def test_build_progress_lines_include_live_active_batch_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            batch_dir = tmpdir / "batch_0001"
            batch_dir.mkdir(parents=True, exist_ok=True)
            (batch_dir / "results_manifest.json").write_text(
                json.dumps(
                    {
                        "total_urls": 7,
                        "items": [
                            {"url": "u1", "status": "downloaded"},
                            {"url": "u2", "status": "skipped"},
                            {"url": "u3", "status": "skipped"},
                            {"url": "u4", "status": "failed"},
                            {"url": "u5", "status": "pending"},
                            {"url": "u6", "status": "pending"},
                            {"url": "u7", "status": "pending"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            state = {
                "items": {
                    "1": {"batch_number": 1, "status": "running"},
                    "2": {"batch_number": 2, "status": "pending"},
                }
            }
            global_results = {
                "total_urls": 100,
                "counts": {"downloaded": 0, "skipped": 0, "failed": 0, "finalized": 0},
            }
            active_processes = {
                "worker-01": {
                    "batch_number": 1,
                    "batch_dir": str(batch_dir),
                    "source_json": str(batch_dir / "batch_0001.json"),
                }
            }

            lines = build_progress_lines(state, global_results, active_processes)
            self.assertIn("finalized_urls: <code>3/100 (3.0%)</code>", lines)
            self.assertIn("downloaded: <code>1</code>", lines)
            self.assertIn("skipped: <code>2</code>", lines)
            self.assertIn("failed_logged: <code>1</code>", lines)
            self.assertIn(
                "worker-01 / batch <code>0001</code> / shard <code>batch_0001.json</code> / d <code>1</code> / s <code>2</code> / f <code>1</code> / p <code>3</code>",
                lines,
            )

    def test_resolve_manifest_source_json_falls_back_to_manifest_dir_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            manifest_dir = tmpdir / "parse-1"
            manifest_dir.mkdir(parents=True, exist_ok=True)
            shard_path = manifest_dir / "batch_0001.json"
            shard_path.write_text(json.dumps(["https://vimeo.com/1"]), encoding="utf-8")
            manifest_path = manifest_dir / "manifest.json"
            manifest_path.write_text("{}", encoding="utf-8")

            resolved = resolve_manifest_source_json(
                manifest_path,
                "/Users/admin/Documents/LAB/CODECS/4k/Parse/codex_vimeo_fix/data_shards/server_assignments_10000/parse-1/batch_0001.json",
                filename="batch_0001.json",
            )

            self.assertEqual(resolved, str(shard_path.resolve()))

    def test_normalize_assignment_manifest_accepts_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            batch_path = tmpdir / "batch_0001.json"
            batch_path.write_text(json.dumps(["https://vimeo.com/1"]), encoding="utf-8")

            manifest_path = tmpdir / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "server_name": "parse-1",
                        "source_json": "../../../need_parse_unique.json",
                        "batch_size": 10000,
                        "shard_count": 1,
                        "total_urls": 1,
                        "batches": [
                            {"batch_number": 1, "filename": "batch_0001.json", "path": "batch_0001.json", "url_count": 1},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            manifest = normalize_assignment_manifest(manifest_path)
            self.assertEqual(manifest["batches"][0]["source_json"], str(batch_path.resolve()))

    def test_select_batches_respects_range_and_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            batch_1 = tmpdir / "batch_0001.json"
            batch_2 = tmpdir / "batch_0002.json"
            batch_3 = tmpdir / "batch_0003.json"
            for path in (batch_1, batch_2, batch_3):
                path.write_text(json.dumps(["https://vimeo.com/1"]), encoding="utf-8")

            manifest_path = tmpdir / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "server_name": "parse-1",
                        "source_json": str(tmpdir / "all.json"),
                        "batch_size": 10000,
                        "shard_count": 3,
                        "total_urls": 30000,
                        "batches": [
                            {"batch_number": 1, "path": str(batch_1), "url_count": 10000},
                            {"batch_number": 2, "path": str(batch_2), "url_count": 10000},
                            {"batch_number": 3, "path": str(batch_3), "url_count": 10000},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            manifest = normalize_assignment_manifest(manifest_path)
            selected = select_batches(manifest, batch_start=2, batch_end=3, max_batches=1)

            self.assertEqual([item["batch_number"] for item in selected], [2])

    def test_running_batches_are_reset_to_pending_on_state_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            batch_path = tmpdir / "batch_0001.json"
            batch_path.write_text(json.dumps(["https://vimeo.com/1"]), encoding="utf-8")

            manifest = {
                "manifest_path": str(tmpdir / "manifest.json"),
                "server_name": "parse-1",
                "batches": [
                    {
                        "batch_number": 1,
                        "source_json": str(batch_path),
                        "filename": batch_path.name,
                        "total_urls": 1,
                    }
                ],
            }
            selected = manifest["batches"]
            state_path = tmpdir / "assignment_state.json"
            state = {
                "manifest_path": manifest["manifest_path"],
                "server_name": "parse-1",
                "items": {
                    "1": {
                        "batch_number": 1,
                        "source_json": str(batch_path),
                        "total_urls": 1,
                        "status": "running",
                        "assigned_worker": "worker-01",
                    }
                },
            }
            save_assignment_state(state_path, state)

            loaded = load_assignment_state(state_path, manifest, selected, _LoggerStub())
            self.assertEqual(loaded["items"]["1"]["status"], "pending")
            self.assertIsNone(loaded["items"]["1"]["assigned_worker"])


if __name__ == "__main__":
    unittest.main()

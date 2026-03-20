import json
import tempfile
import unittest
from pathlib import Path

from run_assigned_shards import (
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

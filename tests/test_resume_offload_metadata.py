import json
import tempfile
import unittest
from pathlib import Path

from download_vimeo_seleniumbase_v3 import (
    load_existing_download_metadata,
    should_skip_offloaded_metadata,
)


class ResumeOffloadMetadataTests(unittest.TestCase):
    def test_offloaded_original_metadata_is_enough_for_resume_skip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = Path(tmpdir) / "1058440992.json"
            payload = {
                "_download": {
                    "selected_quality": "Original 23.72MB | 1080 x 1080",
                    "source": "page",
                    "is_original": True,
                    "filename": "1058440992.mp4",
                    "file_path": "/local/output/downloaded/1058440992/1058440992.mp4",
                    "file_size_mb": 23.72,
                },
                "_offload": {
                    "status": "uploaded",
                    "remote_video_file": "/mnt/mimas/parse-1/downloaded/1058440992/1058440992.mp4",
                    "remote_metadata_json": "/mnt/mimas/parse-1/downloaded/1058440992/1058440992.json",
                },
            }
            metadata_path.write_text(json.dumps(payload), encoding="utf-8")

            existing = load_existing_download_metadata(
                metadata_path,
                config={"settings": {"download_only_original": True}},
            )

            self.assertTrue(existing["offloaded"])
            self.assertTrue(existing["is_original"])
            self.assertTrue(
                should_skip_offloaded_metadata(
                    existing,
                    {"settings": {"download_only_original": True}},
                )
            )


if __name__ == "__main__":
    unittest.main()

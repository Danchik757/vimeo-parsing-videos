import json
import tempfile
import unittest
from pathlib import Path

from download_vimeo_seleniumbase_v3 import (
    build_page_probe,
    load_existing_download_metadata,
    save_video_metadata,
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

    def test_no_links_is_recorded_in_shared_url_list_without_per_video_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            config = {
                "runtime": {
                    "worker_name": "worker-01",
                    "vimeo_authenticated_session": False,
                },
                "browser": {
                    "headless": False,
                    "xvfb": False,
                    "uc": True,
                },
                "settings": {
                    "download_only_original": True,
                    "store_full_api_payload_for_downloaded": True,
                },
                "files": {
                    "logs_dir": str(tmpdir / "logs"),
                },
            }
            video_dir = tmpdir / "videos"
            result = {
                "downloadable": False,
                "download_link_found": False,
                "download_link": None,
                "selected_quality": None,
                "is_original": None,
                "page_visited": False,
                "page_url": None,
                "page_title": None,
                "authenticated_session": False,
                "session_relogin": False,
                "cloudflare_detected": False,
                "button_found": False,
                "available_options_count": 0,
                "page_best_option": None,
                "has_original_option": False,
                "probe_error": None,
                "page_context": None,
            }

            ref_path = save_video_metadata(
                config,
                video_dir,
                "999",
                "https://vimeo.com/999",
                None,
                {"status_code": 404},
                result,
                "skipped",
                "404 not found",
            )

            self.assertEqual(ref_path.name, "no_links_urls.txt")
            self.assertTrue(ref_path.exists())
            self.assertIn("https://vimeo.com/999", ref_path.read_text(encoding="utf-8"))
            self.assertFalse((video_dir / "no_links" / "999.json").exists())

    def test_not_downloaded_page_probe_is_compact(self):
        result = {
            "page_visited": True,
            "page_url": "https://vimeo.com/123",
            "page_title": "Video",
            "authenticated_session": True,
            "session_relogin": False,
            "cloudflare_detected": False,
            "button_found": True,
            "download_link_found": True,
            "available_options_count": 3,
            "available_options": [{"text": "Original", "href": "https://example.com"}],
            "page_best_option": {"text": "1080p", "href": "https://example.com/1080"},
            "has_original_option": False,
            "probe_error": None,
            "page_context": {"next_data": {"clip_id": "123"}},
        }

        compact_probe = build_page_probe(result, include_extended=False)
        full_probe = build_page_probe(result, include_extended=True)

        self.assertNotIn("available_options", compact_probe)
        self.assertNotIn("context", compact_probe)
        self.assertIn("available_options", full_probe)
        self.assertIn("context", full_probe)


if __name__ == "__main__":
    unittest.main()

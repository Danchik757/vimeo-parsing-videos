import unittest

from result_exports import build_result_url_lists


class ResultExportsTests(unittest.TestCase):
    def test_builds_expected_url_buckets(self):
        items = [
            {
                "url": "https://vimeo.com/1",
                "status": "downloaded",
                "downloadable": True,
                "download_link": "https://example.com/1",
            },
            {
                "url": "https://vimeo.com/2",
                "status": "skipped",
                "downloadable": True,
                "download_link": "https://example.com/2",
            },
            {
                "url": "https://vimeo.com/3",
                "status": "skipped",
                "downloadable": False,
                "download_link": None,
            },
            {
                "url": "https://vimeo.com/4",
                "status": "failed",
                "reason": "Transcript download modal opened instead of video download",
                "downloadable": True,
                "download_link": None,
            },
        ]

        buckets = build_result_url_lists(items)

        self.assertEqual(buckets["downloaded_original"], ["https://vimeo.com/1"])
        self.assertEqual(buckets["not_downloaded_downloadable"], ["https://vimeo.com/2"])
        self.assertEqual(buckets["no_links"], ["https://vimeo.com/3"])
        self.assertEqual(buckets["transcript_modal"], ["https://vimeo.com/4"])


if __name__ == "__main__":
    unittest.main()

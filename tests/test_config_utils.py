import json
import tempfile
import unittest
from pathlib import Path

from config_utils import load_json_config_with_optional_secrets
from run_workers import build_worker_config


class ConfigUtilsTests(unittest.TestCase):
    def test_loads_optional_secrets_with_deep_merge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            profile_path = tmpdir / "profile.json"
            secrets_path = tmpdir / "secrets.json"

            profile = {
                "secrets_file": "secrets.json",
                "telegram": {
                    "enabled": True,
                    "notify_on_error": False,
                },
                "workers": {
                    "count": 2,
                    "api_pool": [],
                },
            }
            secrets = {
                "telegram": {
                    "bot_token": "token",
                    "chat_id": "chat",
                },
                "workers": {
                    "api_pool": [{"client_id": "a", "client_secret": "b", "token": "c"}],
                },
            }

            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            secrets_path.write_text(json.dumps(secrets), encoding="utf-8")

            config, resolved_profile, config_dir, resolved_secrets = (
                load_json_config_with_optional_secrets(profile_path)
            )

            self.assertEqual(resolved_profile, profile_path.resolve())
            self.assertEqual(config_dir, profile_path.resolve().parent)
            self.assertEqual(resolved_secrets, secrets_path.resolve())
            self.assertTrue(config["telegram"]["enabled"])
            self.assertFalse(config["telegram"]["notify_on_error"])
            self.assertEqual(config["telegram"]["bot_token"], "token")
            self.assertEqual(config["workers"]["count"], 2)
            self.assertEqual(len(config["workers"]["api_pool"]), 1)

    def test_worker_config_is_self_contained_after_layered_load(self):
        master_config = {
            "secrets_file": "config.parse-1.secrets.json",
            "_meta": {
                "config_path": "/tmp/config.parse-1.profile.json",
                "config_dir": "/tmp",
                "secrets_path": "/tmp/config.parse-1.secrets.json",
            },
            "files": {
                "source_json": "/tmp/source.json",
                "videos_dir": "/tmp/videos",
                "jsons_dir": "/tmp/jsons",
                "logs_dir": "/tmp/logs",
                "failed_downloads": "/tmp/failed.json",
                "log_file": "/tmp/log.txt",
                "summary_file": "/tmp/summary.json",
                "results_file": "/tmp/results.json",
            },
            "workers": {
                "shared_media_dirs": True,
                "api_pool": [
                    {
                        "client_id": "cid1",
                        "client_secret": "secret1",
                        "token": "token1",
                    }
                ],
            },
            "runtime": {
                "vimeo_authenticated_session": True,
            },
            "vimeo_login": {
                "email": "user@example.com",
                "password": "pass",
            },
        }

        worker_name, worker_config = build_worker_config(
            master_config,
            Path("/tmp/output/workers/worker_01"),
            Path("/tmp/output/workers/worker_01/source.json"),
            1,
            2,
        )

        self.assertEqual(worker_name, "worker-01")
        self.assertNotIn("secrets_file", worker_config)
        self.assertNotIn("_meta", worker_config)
        self.assertEqual(worker_config["vimeo_login"]["email"], "user@example.com")
        self.assertEqual(worker_config["vimeo_api"]["client_id"], "cid1")
        self.assertEqual(worker_config["runtime"]["worker_name"], "worker-01")


if __name__ == "__main__":
    unittest.main()

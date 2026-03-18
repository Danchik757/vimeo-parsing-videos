import json
import tempfile
import unittest
from pathlib import Path

from config_utils import load_json_config_with_optional_secrets


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


if __name__ == "__main__":
    unittest.main()

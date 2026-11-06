import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from credential_config import load_credentials, initialize_credentials, read_credentials


class CredentialConfigTests(unittest.TestCase):
    def test_environment_named_file_keys_are_loaded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "credentials.json"
            path.write_text(json.dumps({"datadog": {"api_key": "", "DD_API_KEY": "file-key", "DD_APP_KEY": "file-app"}}))
            path.chmod(0o600)
            with patch.dict(os.environ, {"DD_APP_KEY": "env-app"}, clear=True):
                load_credentials(path)
                self.assertEqual(os.environ["DD_API_KEY"], "file-key")
                self.assertEqual(os.environ["DD_APP_KEY"], "env-app")

    def test_file_loads_each_tool_and_environment_wins(self):
        data = {
            "dynatrace": {"environment_url": "https://dt.example", "api_token": "dt-file"},
            "datadog": {"site": "datadoghq.eu", "api_key": "dd-file", "application_key": "app-file"},
            "zabbix": {"url": "https://zbx.example", "api_token": "zbx-file"},
            "splunk": {"url": "https://splunk.example:8089", "api_token": "splunk-file"},
            "solarwinds": {"url": "https://sw.example:17778", "username": "reader", "password": "sw-file"},
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "credentials.json"
            path.write_text(json.dumps(data))
            path.chmod(0o600)
            with patch.dict(os.environ, {"DD_API_KEY": "dd-env"}, clear=True):
                load_credentials(path)
                self.assertEqual(os.environ["DT_API_TOKEN"], "dt-file")
                self.assertEqual(os.environ["DD_API_KEY"], "dd-env")
                self.assertEqual(os.environ["ZBX_TOKEN"], "zbx-file")
                self.assertEqual(os.environ["SPLUNK_TOKEN"], "splunk-file")
                self.assertEqual(os.environ["SW_PASSWORD"], "sw-file")

    def test_initialize_creates_all_tools_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "credentials.json"
            initialize_credentials(path)
            data = read_credentials(path)
            self.assertTrue({"dynatrace", "datadog", "zabbix", "splunk", "solarwinds"}.issubset(data))
            self.assertEqual(data["dynatrace"]["api_token"], "")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                initialize_credentials(path)

    def test_invalid_file_does_not_partially_load_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "credentials.json"
            path.write_text(json.dumps({"dynatrace": {"api_token": "secret"}, "splunk": {"api_token": 42}}))
            path.chmod(0o600)
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(RuntimeError):
                    load_credentials(path)
                self.assertNotIn("DT_API_TOKEN", os.environ)

    @unittest.skipIf(os.name == "nt", "Unix permissions only")
    def test_rejects_world_readable_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "credentials.json"
            path.write_text("{}")
            path.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "readable by other users"):
                load_credentials(path)


if __name__ == "__main__":
    unittest.main()

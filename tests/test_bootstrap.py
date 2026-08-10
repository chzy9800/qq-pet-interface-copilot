from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qqpet_app.bootstrap import (
    OneBotEndpoint,
    configure_local_onebot,
    endpoints_from_config,
    probe_login,
)
from qqpet_app.single_instance import SingleInstance


class BootstrapTests(unittest.TestCase):
    def test_reads_only_enabled_loopback_http_servers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "onebot11_123456.json"
            path.write_text(
                json.dumps(
                    {
                        "network": {
                            "httpServers": [
                                {"enable": True, "host": "127.0.0.1", "port": 6201, "token": "local"},
                                {"enable": True, "host": "0.0.0.0", "port": 6202, "token": "unsafe"},
                                {"enable": False, "host": "127.0.0.1", "port": 6203},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                endpoints_from_config(path),
                (OneBotEndpoint("http://127.0.0.1:6201", "local", "123456", path),),
            )

    def test_configure_adds_random_token_without_replacing_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "onebot11_987654.json"
            path.write_text(
                json.dumps({"musicSignUrl": "kept", "network": {"websocketClients": []}}),
                encoding="utf-8",
            )
            endpoint = configure_local_onebot(path, 6210)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["musicSignUrl"], "kept")
            self.assertEqual(endpoint.url, "http://127.0.0.1:6210")
            self.assertGreater(len(endpoint.token), 20)
            self.assertEqual(saved["network"]["httpServers"][0]["host"], "127.0.0.1")
            self.assertFalse(saved["network"]["httpServers"][0]["enableCors"])

    @mock.patch("qqpet_app.bootstrap.onebot_action")
    def test_probe_requires_real_login_info(self, action):
        action.return_value = {
            "status": "ok",
            "retcode": 0,
            "data": {"user_id": 123456, "nickname": "tester"},
        }
        endpoint = OneBotEndpoint("http://127.0.0.1:6201", "secret")
        session = probe_login(endpoint)
        self.assertIsNotNone(session)
        self.assertEqual(session.uin, "123456")
        self.assertEqual(session.nickname, "tester")

    def test_single_instance_releases_its_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            first = SingleInstance(Path(directory) / "console.lock")
            self.assertTrue(first.acquire())
            first.release()
            second = SingleInstance(Path(directory) / "console.lock")
            self.assertTrue(second.acquire())
            second.release()


if __name__ == "__main__":
    unittest.main()

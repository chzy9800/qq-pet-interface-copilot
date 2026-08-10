from __future__ import annotations

import unittest

from qqpet_app.notifications import NotificationManager


class NotificationTests(unittest.TestCase):
    def test_enabled_channels_are_fanned_out_without_exposing_secrets(self):
        calls = []
        toasts = []
        config = {
            "notifications": {
                "enabled": True,
                "windows_toast": True,
                "bark": {"enabled": True, "device_key": "bark-secret", "base_url": "https://api.day.app"},
                "pushplus": {"enabled": True, "token": "push-secret", "topic": ""},
                "serverchan": {"enabled": True, "sendkey": "server-secret"},
                "smtp": {"enabled": False},
                "webhook": {"enabled": True, "url": "https://example.test/hook"},
            }
        }

        def sender(provider, **kwargs):
            calls.append((provider, kwargs))
            return object()

        results = NotificationManager(
            config, onepush_sender=sender, toast_sender=lambda title, body: toasts.append((title, body))
        ).send("title", "body")
        self.assertEqual([item.channel for item in results], ["windows_toast", "bark", "pushplus", "serverchan", "webhook"])
        self.assertEqual([item[0] for item in calls], ["bark", "pushplus", "serverchanturbo", "custom"])
        self.assertEqual(toasts, [("title", "body")])

    def test_disabled_notifications_send_nothing(self):
        manager = NotificationManager({"notifications": {"enabled": False}})
        self.assertEqual(manager.send("title", "body"), ())


if __name__ == "__main__":
    unittest.main()

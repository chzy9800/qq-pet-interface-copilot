from __future__ import annotations

import unittest
from pathlib import Path

from qqpet_app.mobile_protocol import (
    MobileProtocolReader,
    MobileProtocolServerError,
    frida_architecture,
    select_adb_serial,
)
from qqpet_app.proto import field_bytes, field_fixed32


class MobileProtocolTests(unittest.TestCase):
    def test_encourage_is_open_on_mobile_one_shot_write_channel(self) -> None:
        self.assertIn(MobileProtocolReader.STORY_ENCOURAGE, MobileProtocolReader.WRITE_ALLOWLIST)
        hook = Path("hooks/qqpet_mobile_read_agent.js").read_text(encoding="utf-8")
        self.assertIn("'OidbSvcTrpcTcp.0x9c44_1': '40004:1'", hook)

    def test_write_server_error_preserves_numeric_code(self) -> None:
        reader = MobileProtocolReader(".")
        reader._connect = lambda: None  # type: ignore[method-assign]

        class Exports:
            @staticmethod
            def send_oidb_write_once(*_args):
                return {"code": 135061, "message": "你的宠物还未达到该职业参与要求"}

        class Script:
            exports_sync = Exports()

        reader._script = Script()
        with self.assertRaises(MobileProtocolServerError) as raised:
            reader.send_oidb_write_once(*reader.STORY_START, b"")
        self.assertEqual(raised.exception.code, 135061)
        self.assertIn("职业参与要求", str(raised.exception))

    def test_read_server_error_does_not_detach_live_frida_agent(self) -> None:
        reader = MobileProtocolReader(".")
        reader._connect = lambda: None  # type: ignore[method-assign]
        disconnects = []
        reader._disconnect = lambda: disconnects.append(True)  # type: ignore[method-assign]

        class Exports:
            @staticmethod
            def send_oidb_read(*_args):
                return {"code": 14561, "message": "你的宠物还未达到该职业参与要求"}

        class Script:
            exports_sync = Exports()

        reader._script = Script()
        with self.assertRaises(MobileProtocolServerError):
            reader.send_oidb_read(*reader.SCENE_OPTIONS, b"")
        self.assertEqual(disconnects, [])

    def test_empty_read_retries_without_detaching_live_frida_agent(self) -> None:
        reader = MobileProtocolReader(".")
        reader._connect = lambda: None  # type: ignore[method-assign]
        disconnects = []
        reader._disconnect = lambda: disconnects.append(True)  # type: ignore[method-assign]

        class Exports:
            calls = 0

            @classmethod
            def send_oidb_read(cls, *_args):
                cls.calls += 1
                return {"code": 0, "data_hex": ""}

        class Script:
            exports_sync = Exports()

        reader._script = Script()
        with self.assertRaisesRegex(Exception, "手机 QQ 返回空响应"):
            reader.send_oidb_read(*reader.STORY_STATUS, b"")
        self.assertEqual(Exports.calls, 2)
        self.assertEqual(disconnects, [])

    def test_select_adb_serial_prefers_configured_online_device(self) -> None:
        output = "List of devices attached\n127.0.0.1:16384\tdevice\nemulator-5554\tdevice\n"
        self.assertEqual(select_adb_serial(output, "emulator-5554"), "emulator-5554")

    def test_select_adb_serial_falls_back_to_running_mumu_instance(self) -> None:
        output = "List of devices attached\n127.0.0.1:16416\toffline\n127.0.0.1:16384\tdevice\n"
        self.assertEqual(select_adb_serial(output, "127.0.0.1:16416"), "127.0.0.1:16384")

    def test_frida_architecture_uses_kernel_architecture(self) -> None:
        self.assertEqual(frida_architecture("aarch64\n"), "arm64")
        self.assertEqual(frida_architecture("x86_64"), "x86_64")

    def test_mobile_state_and_gold_packets_are_decoded(self) -> None:
        display = b"".join(
            field_bytes(index, field_fixed32(3, value))
            for index, value in enumerate((98.0, 100.0, 97.0, 98.8), start=1)
        )
        personal = field_bytes(4, display)
        state = field_bytes(1, field_bytes(5, personal))
        gold = field_bytes(1, field_bytes(5, field_fixed32(3, 2147.0)))

        reader = MobileProtocolReader(".")
        replies = iter((state, gold))
        reader._send_read = lambda _spec, _body: next(replies)  # type: ignore[method-assign]

        values = reader.query_values("pet-id")
        self.assertEqual(values.feel, 98.0)
        self.assertEqual(values.hunger, 100.0)
        self.assertEqual(values.clean, 97.0)
        self.assertAlmostEqual(values.total, 98.8, places=2)
        self.assertEqual(values.gold, 2147.0)


if __name__ == "__main__":
    unittest.main()

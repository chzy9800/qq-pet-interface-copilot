from __future__ import annotations

import unittest

from qqpet_app.mobile_protocol import MobileProtocolReader
from qqpet_app.proto import field_bytes, field_fixed32


class MobileProtocolTests(unittest.TestCase):
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

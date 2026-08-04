from __future__ import annotations

import unittest

from qqpet_app.client import NapCatClient, PageRules, QQPetError, StoryStatus
from qqpet_app.proto import (
    field_bytes,
    field_fixed32,
    field_string,
    field_varint,
    first_bytes,
    first_string,
    first_varint,
    parse_message,
)


def oidb_response(command: int, sub: int, body: bytes) -> dict:
    raw = field_varint(1, command) + field_varint(2, sub) + field_varint(3, 0) + field_bytes(4, body)
    return {"status": "ok", "retcode": 0, "data": raw.hex()}


class ProtoAndClientTests(unittest.TestCase):
    def test_wire_roundtrip(self) -> None:
        raw = field_varint(1, 12345) + field_bytes(2, b"abc")
        parsed = parse_message(raw)
        self.assertEqual(first_varint(parsed, 1), 12345)
        self.assertEqual(first_bytes(parsed, 2), b"abc")

    def test_display_values_are_decoded(self) -> None:
        value_messages = []
        for value in (100.0, 73.0, 100.0, 91.9):
            value_messages.append(field_fixed32(1, 100.0) + field_fixed32(3, value))
        common_root = b"".join(field_bytes(index + 1, item) for index, item in enumerate(value_messages))
        common_body = field_bytes(1, common_root)
        gold_body = field_bytes(1, field_bytes(5, field_fixed32(3, 1578.75)))
        calls = 0

        def transport(_command: str, _data: str) -> dict:
            nonlocal calls
            calls += 1
            return oidb_response(38642, 1, common_body if calls == 1 else gold_body)

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        values = client.query_values()
        self.assertEqual(values.feel, 100.0)
        self.assertEqual(values.hunger, 73.0)
        self.assertEqual(values.clean, 100.0)
        self.assertAlmostEqual(values.total, 91.9, places=2)
        self.assertEqual(values.gold, 1578.75)

    def test_story_completion_uses_progress_not_unix_time(self) -> None:
        running = StoryStatus("6400_x", 51, remaining_seconds=5739, duration_seconds=14400, started_at=1785600267)
        done = StoryStatus("6400_x", 51, remaining_seconds=0, duration_seconds=14400, started_at=1785600267)
        self.assertFalse(running.finished)
        self.assertEqual(running.elapsed_seconds, 8661)
        self.assertEqual(running.remaining_seconds, 5739)
        self.assertTrue(done.finished)

    def test_story_status_query_includes_mobile_outdoor_version(self) -> None:
        def transport(command: str, data: str) -> dict:
            self.assertEqual(command, "OidbSvcTrpcTcp.0x975a_1")
            outer = parse_message(bytes.fromhex(data))
            request = parse_message(first_bytes(outer, 4))
            self.assertEqual(first_string(request, 1), "pet")
            self.assertEqual(first_varint(request, 100), 2)
            return oidb_response(38746, 1, b"")

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        self.assertEqual(client.query_story(), StoryStatus())

    def test_story_settlement_includes_mobile_outdoor_version(self) -> None:
        def transport(command: str, data: str) -> dict:
            self.assertEqual(command, "OidbSvcTrpcTcp.0x9760_1")
            outer = parse_message(bytes.fromhex(data))
            request = parse_message(first_bytes(outer, 4))
            self.assertEqual(first_string(request, 1), "6700_story")
            self.assertEqual(first_varint(request, 2), 1000)
            self.assertEqual(first_string(request, 3), "pet")
            self.assertEqual(first_varint(request, 100), 2)
            return oidb_response(38752, 1, b"")

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        client.settle_story("6700_story")

    def test_page_rules_decode_scene_paths(self) -> None:
        path = field_varint(1, 6000) + field_varint(2, 6400) + field_varint(3, 6501)
        page_event = field_bytes(1, path)
        response = field_string(1, '{"pkey":"x","count":1}') + field_bytes(2, page_event)

        def transport(_command: str, _data: str) -> dict:
            return oidb_response(38564, 1, response)

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        rules = client.query_page_rules()
        self.assertEqual(rules.declared_count, 1)
        self.assertTrue(rules.allows((6000, 6400, 6501)))
        self.assertFalse(rules.allows((6000, 6100, 6101)))

    def test_scene_start_rejects_path_not_offered_by_server(self) -> None:
        client = NapCatClient("http://unused", "token", "pet", transport=lambda *_: {})
        with self.assertRaises(QQPetError):
            client.start_scene("school", "culture", PageRules(paths=((6000, 6400, 6401),)))

    def test_food_inventory_uses_empty_request_and_decodes_response(self) -> None:
        def transport(_command: str, data: str) -> dict:
            request = parse_message(bytes.fromhex(data))
            self.assertEqual(first_bytes(request, 4), b"")
            return oidb_response(39241, 1, field_varint(1, 12) + field_varint(2, 10))

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        inventory = client.query_food_inventory()
        self.assertEqual(inventory.biscuits, 12)
        self.assertEqual(inventory.shrimp, 10)
        self.assertEqual(inventory.total, 22)

    def test_bath_shop_and_inventory_are_decoded(self) -> None:
        soap = (
            field_string(1, "香皂片")
            + field_string(2, "1")
            + field_varint(5, 2)
            + field_varint(6, 10)
            + field_string(7, "清洁值+10")
            + field_varint(8, 10)
            + field_varint(9, 10)
            + field_varint(10, 1)
            + field_varint(11, 99)
            + field_varint(12, 1)
        )
        inventory = field_bytes(1, field_string(1, "1") + field_varint(2, 14))
        calls = 0

        def transport(command: str, data: str) -> dict:
            nonlocal calls
            calls += 1
            request = parse_message(bytes.fromhex(data))
            self.assertEqual(first_bytes(request, 4), field_varint(1, 1))
            if calls == 1:
                self.assertEqual(command, "OidbSvcTrpcTcp.0x9bf1_1")
                return oidb_response(39921, 1, field_bytes(1, soap))
            self.assertEqual(command, "OidbSvcTrpcTcp.0x9bf2_1")
            return oidb_response(39922, 1, field_bytes(1, inventory))

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        item = client.query_bath_items()[0]
        self.assertEqual((item.item_id, item.gold_price, item.clean_gain), ("1", 2, 10))
        self.assertEqual(client.query_bath_inventory().soap, 14)

    def test_food_and_bath_purchase_packets_match_mobile_protocol(self) -> None:
        calls = 0

        def transport(command: str, data: str) -> dict:
            nonlocal calls
            calls += 1
            outer = parse_message(bytes.fromhex(data))
            request_body = first_bytes(outer, 4)
            request = parse_message(request_body)
            if calls == 1:
                self.assertEqual(command, "OidbSvcTrpcTcp.0x99df_1")
                self.assertEqual(first_varint(request, 1), 3)
                response = (
                    field_varint(1, 10)
                    + field_varint(2, 2181)
                    + field_varint(3, 3)
                    + field_varint(4, 6)
                )
                return oidb_response(39391, 1, response)

            if calls == 3:
                self.assertEqual(command, "OidbSvcTrpcTcp.0x9bf3_1")
                self.assertEqual(first_string(request, 1), "pet")
                self.assertEqual(first_string(request, 2), "1")
                self.assertEqual(first_varint(request, 3), 1)
                return oidb_response(39923, 1, field_varint(1, 45) + field_varint(3, 13))

            self.assertEqual(command, "OidbSvcTrpcTcp.0x9bd0_0")
            self.assertEqual(first_varint(request, 2), 1001)
            self.assertEqual(first_varint(request, 4), 21)
            user = parse_message(first_bytes(request, 1))
            mall = parse_message(first_bytes(request, 3))
            self.assertEqual(first_varint(user, 1), 1)
            self.assertEqual(first_varint(user, 2), 1001)
            self.assertEqual(first_varint(mall, 1), 355)
            self.assertEqual(first_varint(mall, 2), 2)
            self.assertEqual(first_varint(mall, 3), 5)
            return oidb_response(39888, 0, field_varint(1, 0) + field_string(2, "order"))

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        food = client.buy_food(3)
        self.assertEqual((food.bought, food.cost_gold), (3, 6))
        bath = client.buy_bath_item("2", 5)
        self.assertTrue(bath.succeeded)
        wash = client.use_bath_item("1")
        self.assertEqual((wash.clean, wash.remaining), (45, 13))


if __name__ == "__main__":
    unittest.main()

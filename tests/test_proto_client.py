from __future__ import annotations

import unittest

from qqpet_app.client import (
    AdventureOption,
    NapCatClient,
    OidbResponse,
    PageRules,
    PKOpponent,
    PKResult,
    PetValues,
    QQPetError,
    SchoolCourse,
    StoryStatus,
    WorkJob,
)
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
    def test_pk_friend_candidates_decode_real_friend_pet_fields(self) -> None:
        profile = field_string(1, "宠物甲") + field_string(8, "pet-10001")
        user = field_varint(1, 10001) + field_string(2, "好友甲")
        power = field_varint(3, 2) + field_varint(4, 99)
        friend = (
            field_bytes(1, profile)
            + field_bytes(2, user)
            + field_varint(3, 0)
            + field_bytes(14, power)
        )

        def transport(command: str, data: str) -> dict:
            self.assertEqual(command, "OidbSvcTrpcTcp.0x985d_0")
            outer = parse_message(bytes.fromhex(data))
            request = parse_message(first_bytes(outer, 4))
            self.assertEqual(first_varint(request, 2), 6)
            return oidb_response(
                39005,
                0,
                field_bytes(1, friend) + field_varint(3, 0),
            )

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        self.assertEqual(
            client.query_pk_friend_candidates(),
            (PKOpponent("10001", "pet-10001", "好友甲", "宠物甲", 99, 2, 0),),
        )

    def test_pk_power_start_and_settlement_packets_match_mobile_protocol(self) -> None:
        calls = 0

        def transport(command: str, data: str) -> dict:
            nonlocal calls
            calls += 1
            outer = parse_message(bytes.fromhex(data))
            request = parse_message(first_bytes(outer, 4))
            if calls == 1:
                self.assertEqual(command, "OidbSvcTrpcTcp.0x9ad4_1")
                self.assertEqual(first_string(request, 1), "pet")
                self.assertEqual(first_varint(request, 100), 2)
                power = field_varint(3, 2) + field_varint(4, 1713)
                return oidb_response(39636, 1, field_bytes(1, power))
            if calls == 2:
                self.assertEqual(command, "OidbSvcTrpcTcp.0x975e_1")
                self.assertEqual(first_varint(request, 1), 6900)
                self.assertEqual(first_string(request, 2), "pet")
                friend = parse_message(first_bytes(request, 4))
                self.assertEqual(first_string(friend, 1), "friend-pet")
                self.assertEqual(first_string(friend, 2), "10001")
                mode = parse_message(first_bytes(request, 6))
                self.assertEqual(first_varint(mode, 10), 75)
                self.assertEqual(first_varint(request, 7), 6901)
                self.assertEqual(first_varint(request, 100), 2)
                return oidb_response(38750, 1, field_string(1, "6900_story"))
            self.assertEqual(command, "OidbSvcTrpcTcp.0x9760_1")
            self.assertEqual(first_string(request, 1), "6900_story")
            self.assertEqual(first_varint(request, 2), 6000)
            self.assertEqual(first_string(request, 3), "pet")
            self.assertEqual(first_varint(request, 4), 0)
            self.assertEqual(first_varint(request, 100), 2)
            return oidb_response(38752, 1, field_string(1, "verified-result"))

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        power = client.query_pk_power()
        self.assertEqual((power.power, power.dominant_type), (1713, 2))
        started = client.start_pk("10001", "friend-pet")
        self.assertEqual(started.story_id, "6900_story")
        settled = client.settle_pk(started.story_id)
        self.assertEqual(first_string(parse_message(settled.body), 1), "verified-result")

    def test_pk_result_requires_real_server_state_delta(self) -> None:
        response = OidbResponse(38752, 1, 0, b"result", b"raw")
        result = PKResult(
            "10001",
            "friend-pet",
            "6900_story",
            PetValues(feel=80, hunger=90, clean=90, gold=100),
            PetValues(feel=82, hunger=85, clean=85, gold=142),
            response,
        )
        self.assertTrue(result.verified)
        self.assertEqual(result.gold_delta, 42)
        self.assertEqual((result.hunger_cost, result.clean_cost), (5, 5))

    def test_friend_list_uses_onebot_and_normalizes_fields(self) -> None:
        client = NapCatClient("http://unused", "token", "pet")
        calls = []

        def action(name: str, params=None):
            calls.append((name, params))
            return {
                "status": "ok",
                "retcode": 0,
                "data": [
                    {
                        "user_id": 12345,
                        "nickname": "好友",
                        "remark": "备注",
                        "category_id": 2,
                    },
                    {"nickname": "缺少账号"},
                ],
            }

        client._onebot_action = action
        friends = client.query_friend_list()
        self.assertEqual(calls, [("get_friend_list", None)])
        self.assertEqual(len(friends), 1)
        self.assertEqual(friends[0].user_id, "12345")
        self.assertEqual(friends[0].remark, "备注")

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

    def test_removed_legacy_adventure_path_is_rejected(self) -> None:
        client = NapCatClient("http://unused", "token", "pet", transport=lambda *_: {})
        with self.assertRaises(QQPetError):
            client.scene_path("adventure", "coins")

    def test_school_start_uses_live_stage_and_highest_attribute_reward(self) -> None:
        calls = 0
        short_course = (
            field_string(1, "体能课")
            + field_string(7, "10分钟")
            + field_string(8, "力量+10")
            + field_varint(50, 1)
            + field_varint(52, 6115001)
        )
        best_course = (
            field_string(1, "田径运动课")
            + field_string(6, "金币72，体力15")
            + field_string(7, "30分钟")
            + field_string(8, "力量+25")
            + field_varint(50, 1)
            + field_varint(52, 6115004)
        )

        def transport(command: str, data: str) -> dict:
            nonlocal calls
            calls += 1
            outer = parse_message(bytes.fromhex(data))
            request = parse_message(first_bytes(outer, 4))
            if calls == 1:
                self.assertEqual(command, "OidbSvcTrpcTcp.0x9b60_1")
                self.assertEqual(first_varint(request, 1), 6100)
                self.assertEqual(first_string(request, 2), "pet")
                self.assertEqual(first_varint(request, 100), 2)
                return oidb_response(39776, 1, field_varint(4, 1))
            if calls == 2:
                self.assertEqual(command, "OidbSvcTrpcTcp.0x9ab2_1")
                self.assertEqual(first_varint(request, 11), 1)
                return oidb_response(
                    39602,
                    1,
                    field_bytes(1, short_course) + field_bytes(1, best_course),
                )
            self.assertEqual(command, "OidbSvcTrpcTcp.0x975e_1")
            self.assertEqual(first_varint(request, 1), 6100)
            self.assertEqual(first_string(request, 2), "pet")
            self.assertEqual(first_string(request, 3), "")
            self.assertEqual(first_string(request, 6), "田径运动课")
            self.assertEqual(first_varint(request, 7), 6115004)
            self.assertEqual(first_varint(request, 100), 2)
            return oidb_response(38750, 1, field_string(1, "6100_created"))

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        result = client.start_school("physical")
        self.assertEqual(result.course.name, "田径运动课")
        self.assertEqual(result.story_id, "6100_created")

    def test_school_course_can_be_selected_by_frontend_id(self) -> None:
        client = NapCatClient("http://unused", "token", "pet", transport=lambda *_: {})
        courses = (
            SchoolCourse("看图识世界课", 6115002, "智力+10", "10分钟", can_do=True),
            SchoolCourse("世界地理课", 6115005, "智力+25", "30分钟", can_do=True),
        )
        client.query_school_courses = lambda _stage=None: courses
        selected = client.select_school_course("culture", 6115002)
        self.assertEqual(selected.name, "看图识世界课")

    def test_work_start_uses_live_career_jobs_and_real_begin_packet(self) -> None:
        calls = 0
        career = (
            field_string(1, "彩虹画室")
            + field_varint(4, 1)
            + field_varint(20, 1)
        )
        short_job = (
            field_string(1, "帮店主补招牌")
            + field_string(7, "10分钟")
            + field_string(8, "金币 65")
            + field_varint(50, 1)
            + field_varint(52, 6411001)
        )
        best_job = (
            field_string(1, "熬夜赶参赛稿")
            + field_string(6, "体力40，清洁16")
            + field_string(7, "4小时")
            + field_string(8, "![金币](https://example/123456.png) 539")
            + field_varint(50, 1)
            + field_varint(52, 6411004)
        )

        def transport(command: str, data: str) -> dict:
            nonlocal calls
            calls += 1
            outer = parse_message(bytes.fromhex(data))
            request = parse_message(first_bytes(outer, 4))
            if calls == 1:
                self.assertEqual(command, "OidbSvcTrpcTcp.0x9b60_1")
                self.assertEqual(first_varint(request, 1), 6400)
                self.assertEqual(first_string(request, 2), "pet")
                self.assertEqual(first_varint(request, 100), 2)
                return oidb_response(
                    39776,
                    1,
                    field_bytes(1, career)
                    + field_varint(3, 1)
                    + field_varint(5, 6411001),
                )
            if calls == 2:
                self.assertEqual(command, "OidbSvcTrpcTcp.0x9ab2_1")
                self.assertEqual(first_varint(request, 1), 6400)
                self.assertEqual(first_varint(request, 10), 1)
                return oidb_response(
                    39602,
                    1,
                    field_bytes(1, short_job)
                    + field_bytes(1, best_job)
                    + field_string(2, "涂鸦小徒"),
                )
            self.assertEqual(command, "OidbSvcTrpcTcp.0x975e_1")
            self.assertEqual(first_varint(request, 1), 6400)
            self.assertEqual(first_string(request, 2), "pet")
            self.assertEqual(first_string(request, 3), "")
            self.assertEqual(first_string(request, 6), "熬夜赶参赛稿")
            self.assertEqual(first_varint(request, 7), 6411004)
            self.assertEqual(first_varint(request, 100), 2)
            return oidb_response(38750, 1, field_string(1, "6400_created"))

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        result = client.start_work(career_type=1)
        self.assertEqual(result.job.name, "熬夜赶参赛稿")
        self.assertEqual(result.job.reward_value, 539)
        self.assertEqual(result.story_id, "6400_created")

    def test_work_start_encodes_hired_friend_as_nested_user_info(self) -> None:
        def transport(command: str, data: str) -> dict:
            self.assertEqual(command, "OidbSvcTrpcTcp.0x975e_1")
            outer = parse_message(bytes.fromhex(data))
            request = parse_message(first_bytes(outer, 4))
            friend = parse_message(first_bytes(request, 4))
            self.assertEqual(first_string(friend, 1), "friend-user")
            self.assertEqual(first_string(friend, 2), "friend-pet")
            return oidb_response(38750, 1, field_string(1, "6400_friend"))

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        client.select_work_job = lambda *_args: WorkJob(
            1,
            "涂鸦小徒",
            "熬夜赶参赛稿",
            6411004,
            "金币 539",
            "4小时",
            can_do=True,
        )
        result = client.start_work(hired_user_id="friend-user", hired_pet_id="friend-pet")
        self.assertTrue(result.hired_friend)
        self.assertEqual(result.story_id, "6400_friend")

    def test_adventure_start_uses_live_option_and_real_begin_packet(self) -> None:
        calls = 0
        option = (
            field_string(1, "打招呼")
            + field_string(6, "体力5，清洁5")
            + field_string(7, "45秒")
            + field_string(10, "可能发生特殊的事情，包括偶遇哦")
            + field_varint(50, 1)
        )

        def transport(command: str, data: str) -> dict:
            nonlocal calls
            calls += 1
            outer = parse_message(bytes.fromhex(data))
            request = parse_message(first_bytes(outer, 4))
            if calls == 1:
                self.assertEqual(command, "OidbSvcTrpcTcp.0x9ab2_1")
                self.assertEqual(first_varint(request, 1), 6700)
                self.assertEqual(first_string(request, 2), "pet")
                self.assertEqual(first_string(request, 3), "")
                self.assertEqual(first_varint(request, 100), 2)
                return oidb_response(39602, 1, field_bytes(1, option))
            self.assertEqual(command, "OidbSvcTrpcTcp.0x975e_1")
            self.assertEqual(first_varint(request, 1), 6700)
            self.assertEqual(first_string(request, 2), "pet")
            self.assertEqual(first_string(request, 3), "")
            self.assertEqual(first_string(request, 6), "打招呼")
            self.assertEqual(first_varint(request, 7), 0)
            self.assertEqual(first_varint(request, 100), 2)
            return oidb_response(38750, 1, field_string(1, "6700_created"))

        client = NapCatClient("http://unused", "token", "pet", transport=transport)
        result = client.start_adventure()
        self.assertEqual(
            result.option,
            AdventureOption(
                "打招呼",
                cost="体力5，清洁5",
                duration="45秒",
                description="可能发生特殊的事情，包括偶遇哦",
                can_do=True,
            ),
        )
        self.assertEqual(result.story_id, "6700_created")

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

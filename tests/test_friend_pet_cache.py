import json
import tempfile
import unittest
from pathlib import Path

from main import MainWindow
from qqpet_app.client import PKOpponent, QQFriend
from qqpet_app.friend_pet_cache import load_latest_friend_pet_capture


class FriendPetCaptureTests(unittest.TestCase):
    def test_loads_only_verified_pet_owners_from_latest_daily_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "friend-pets-2026-08-09.json").write_text(
                json.dumps({"friends": [{"uin": "1", "has_pet": True, "pet_id": "old"}]}),
                encoding="utf-8",
            )
            (directory / "friend-pets-2026-08-10.json").write_text(
                json.dumps(
                    {
                        "friends": [
                            {
                                "uin": "10001",
                                "has_pet": True,
                                "pet_id": "pet-10001",
                                "remark": "好友甲",
                                "pet_name": "乐乐",
                                "power": 42,
                            },
                            {"uin": "10002", "has_pet": False, "pet_id": ""},
                            {"uin": "bad", "has_pet": True, "pet_id": "bad-pet"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            opponents = load_latest_friend_pet_capture(directory)

            self.assertEqual(len(opponents), 1)
            self.assertEqual(opponents[0].user_id, "10001")
            self.assertEqual(opponents[0].pet_id, "pet-10001")
            self.assertEqual(opponents[0].nickname, "好友甲")
            self.assertEqual(opponents[0].power, 42)

    def test_ignores_probe_and_malformed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "friend-pets-probe-20260810-120000.json").write_text(
                "{}", encoding="utf-8"
            )
            (directory / "friend-pets-2026-08-10.json").write_text(
                "not json", encoding="utf-8"
            )
            self.assertEqual(load_latest_friend_pet_capture(directory), ())

    def test_friend_choices_put_pet_owners_first_and_can_hide_non_owners(self) -> None:
        friends = (
            QQFriend("10001", nickname="阿明"),
            QQFriend("10002", nickname="小周"),
            QQFriend("10003", nickname="宝宝"),
        )
        opponents = {
            "10002": PKOpponent("10002", "pet-2", nickname="小周"),
            "10003": PKOpponent("10003", "pet-3", nickname="宝宝"),
        }

        rows = MainWindow._friend_choice_rows(friends, opponents, "", False)
        self.assertEqual([uin for _label, uin in rows], ["10003", "10002", "10001"])
        self.assertIn("有宠物", rows[0][0])
        self.assertIn("服务器未返回宠物", rows[-1][0])

        filtered = MainWindow._friend_choice_rows(friends, opponents, "", True)
        self.assertEqual([uin for _label, uin in filtered], ["10003", "10002"])

    def test_friend_filter_keeps_unknown_entries_when_pet_pool_is_unavailable(self) -> None:
        friends = (QQFriend("10001", nickname="资料未知好友"),)
        rows = MainWindow._friend_choice_rows(friends, {}, "接口不可用", True)
        self.assertEqual(len(rows), 1)
        self.assertIn("宠物资料未知", rows[0][0])


if __name__ == "__main__":
    unittest.main()

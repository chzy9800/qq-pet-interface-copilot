from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qqpet_app.client import PKOpponent, QQFriend
from qqpet_app.friend_visits import (
    FriendVisitProgress,
    current_pet_friends,
    eligible_friends,
    parse_uin_list,
)


class FriendVisitTests(unittest.TestCase):
    def test_parse_and_filter_friend_lists(self) -> None:
        friends = (
            QQFriend("100", "self"),
            QQFriend("200", "allowed"),
            QQFriend("300", "blocked"),
            QQFriend("200", "duplicate"),
        )
        self.assertEqual(parse_uin_list("200， 300\n400"), {"200", "300", "400"})
        result = eligible_friends(friends, "100", "200,300", "300")
        self.assertEqual(tuple(item.user_id for item in result), ("200",))

    def test_daily_progress_is_resumable_and_separated_by_date(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            first = FriendVisitProgress(folder, "2026-08-10")
            first.record_scan(67, 20)
            first.mark("200", "success", pet_id="pet-200", poked=True)
            first.mark("300", "failed", detail="timeout")

            resumed = FriendVisitProgress(folder, "2026-08-10")
            self.assertTrue(resumed.completed("200"))
            self.assertTrue(resumed.scanned())
            self.assertFalse(resumed.completed("300"))
            self.assertEqual(resumed.summary()["success"], 1)
            self.assertEqual(resumed.summary()["failed"], 1)

            next_day = FriendVisitProgress(folder, "2026-08-11")
            self.assertEqual(next_day.summary()["success"], 0)
            self.assertFalse(next_day.scanned())
            self.assertNotEqual(first.path, next_day.path)

    def test_historical_pet_cache_is_limited_to_current_qq_friends(self) -> None:
        friends = (QQFriend("100", "甲"), QQFriend("200", "乙"))
        live = (PKOpponent("100", "live-100"),)
        captured = (
            PKOpponent("100", "old-100"),
            PKOpponent("999", "stale-pet"),
        )
        matched = current_pet_friends(friends, live, captured)
        self.assertEqual(set(matched), {"100"})
        self.assertEqual(matched["100"].pet_id, "live-100")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from qqpet_app.client import (
    BathInventory,
    FoodInventory,
    FoodItem,
    PetValues,
    OidbResponse,
    QQPetError,
    StoryStatus,
    WorkJob,
    WorkStartResult,
)
from qqpet_app.config import DEFAULT_CONFIG
from qqpet_app.interface_tests import InterfaceTestRunner


class InterfaceTestRunnerTests(unittest.TestCase):
    def test_selected_food_is_sent_and_verified_by_inventory_delta(self) -> None:
        class FakeClient:
            fed = False
            selected = ""

            def query_values(self):
                return PetValues(hunger=50 if not self.fed else 70, clean=90)

            def query_food_inventory(self):
                return FoodInventory(biscuits=10, shrimp=3 if not self.fed else 2)

            def query_food_items(self):
                return (FoodItem("shrimp-id", "虾仁", 3 if not self.fed else 2),)

            def feed(self, food_id=""):
                self.selected = food_id
                self.fed = True

        config = {key: dict(value) for key, value in DEFAULT_CONFIG.items()}
        config["safety"]["safe_mode"] = False
        config["care"]["verify_delay_seconds"] = 0
        client = FakeClient()
        result = InterfaceTestRunner(client, config).feed("shrimp-id", "虾仁")
        self.assertTrue(result.succeeded)
        self.assertEqual(client.selected, "shrimp-id")
        self.assertIn("虾仁 3→2", result.detail)

    def test_story_write_is_blocked_while_another_story_is_active(self) -> None:
        class FakeClient:
            def query_story(self):
                return StoryStatus(
                    story_id="6400-active",
                    remaining_seconds=300,
                    duration_seconds=3600,
                )

        config = {key: dict(value) for key, value in DEFAULT_CONFIG.items()}
        config["safety"]["safe_mode"] = False
        config["safety"]["allow_experimental_scene_actions"] = True
        with self.assertRaises(QQPetError) as raised:
            InterfaceTestRunner(FakeClient(), config).start_school(61001, "力量课")
        self.assertIn("当前已有任务进行中", str(raised.exception))

    def test_safe_mode_blocks_real_care_test(self) -> None:
        config = {key: dict(value) for key, value in DEFAULT_CONFIG.items()}
        with self.assertRaises(QQPetError) as raised:
            InterfaceTestRunner(object(), config).wash("1", "香皂片")
        self.assertIn("安全模式", str(raised.exception))

    def test_work_hire_sends_selected_friend_identity(self) -> None:
        class FakeClient:
            started = None

            def query_story(self):
                return StoryStatus() if self.started is None else StoryStatus("6400-hired", 51, 90, 100)

            def start_work(
                self, career_type, sub_event, strategy, hired_user_id, hired_pet_id
            ):
                self.started = (hired_user_id, hired_pet_id)
                return WorkStartResult(
                    WorkJob(career_type, "职业", "岗位", sub_event, can_do=True),
                    "6400-hired",
                    hired_friend=True,
                )

        config = {key: dict(value) for key, value in DEFAULT_CONFIG.items()}
        config["safety"]["safe_mode"] = False
        config["safety"]["allow_experimental_scene_actions"] = True
        client = FakeClient()
        result = InterfaceTestRunner(client, config).start_work(
            8, 6481001, "替摊主看摊", "10001", "pet-one", "好友甲"
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(client.started, ("10001", "pet-one"))
        self.assertIn("好友甲", result.detail)

    def test_recall_employed_verifies_story_is_cleared(self) -> None:
        class FakeClient:
            recalled = False

            def query_story(self):
                if self.recalled:
                    return StoryStatus()
                return StoryStatus("6500_employed", 51, 75, 100, recallable=True)

            def settle_story(self, story_id):
                self.recalled = True
                return OidbResponse(38752, 1, 0, b"verified", b"raw")

        config = {key: dict(value) for key, value in DEFAULT_CONFIG.items()}
        config["safety"]["safe_mode"] = False
        config["safety"]["allow_experimental_scene_actions"] = True
        config["care"]["verify_delay_seconds"] = 0
        result = InterfaceTestRunner(FakeClient(), config).recall_employed()
        self.assertTrue(result.succeeded)
        self.assertIn("6500_employed", result.detail)


if __name__ == "__main__":
    unittest.main()

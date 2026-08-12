from __future__ import annotations

import unittest

from qqpet_app.client import (
    BathInventory,
    FoodInventory,
    FoodItem,
    PetValues,
    QQPetError,
    StoryStatus,
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


if __name__ == "__main__":
    unittest.main()

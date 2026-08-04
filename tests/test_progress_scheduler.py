from __future__ import annotations

import tempfile
import unittest
import json
from datetime import datetime
from pathlib import Path

from qqpet_app.client import (
    AdventureOption,
    AdventureStartResult,
    BathInventory,
    FoodInventory,
    PageRules,
    PetValues,
    SchoolCourse,
    SchoolStartResult,
    StoryStatus,
    WorkJob,
    WorkStartResult,
)
from qqpet_app.config import ConfigStore
from qqpet_app.progress import DailyProgress
from qqpet_app.scheduler import Scheduler


class ProgressAndSchedulerTests(unittest.TestCase):
    def test_frontend_status_includes_bath_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            captured = []

            class FakeClient:
                def query_values(self):
                    return PetValues(gold=1000, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus(
                        "6400_active", 51, remaining_seconds=100, duration_seconds=200
                    )

                def query_food_inventory(self):
                    return FoodInventory(biscuits=12, shrimp=10)

                def query_bath_inventory(self):
                    return BathInventory(counts=(("1", 3), ("2", 4)))

            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: FakeClient(),
                status_callback=lambda _values, _story, state: captured.append(state),
            )

            self.assertEqual(scheduler.run_once(), "story")
            self.assertEqual(captured[0]["bath_inventory"], {"soap": 3, "bath_ball": 4})

    def test_daily_rollover_archives_and_resets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "progress.json"
            path.write_text(
                json.dumps({"date": "2000-01-01", "counts": {"school": 2}, "history": [], "pending": None}),
                encoding="utf-8",
            )
            progress = DailyProgress(path)
            state = progress.snapshot()
            self.assertEqual(state["counts"]["school"], 0)
            self.assertEqual(state["history"][-1]["counts"]["school"], 2)

    def test_dispatch_priority_and_unlimited_school(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["adventure"]["start_time"] = "23:59"
            store.save(config)
            scheduler = Scheduler(root / "config.yaml", root / "progress.json")
            rich = PetValues(gold=1000)
            poor = PetValues(gold=1)
            now = datetime(2026, 8, 2, 12, 0)
            self.assertEqual(scheduler.decide(config, rich, now), "school")
            self.assertEqual(scheduler.decide(config, poor, now), "work")
            scheduler.progress.increment("school", 11)
            self.assertEqual(scheduler.decide(config, rich, now), "school")
            config["school"]["times_per_day"] = 1  # 兼容旧配置时也不能重新限制学习
            self.assertEqual(scheduler.decide(config, rich, now), "school")

    def test_adventure_wins_after_configured_time(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["adventure"]["start_time"] = "08:00"
            store.save(config)
            scheduler = Scheduler(root / "config.yaml", root / "progress.json")
            action = scheduler.decide(config, PetValues(gold=1000), datetime(2026, 8, 2, 9, 0))
            self.assertEqual(action, "adventure")

    def test_story_kind_can_recover_interrupted_work(self) -> None:
        self.assertEqual(Scheduler._story_kind("6400_example"), "work")
        self.assertEqual(Scheduler._story_kind("6100_example"), "school")
        self.assertEqual(Scheduler._story_kind("6700_example"), "adventure")

    def test_settled_story_id_is_persisted_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "progress.json"
            progress = DailyProgress(path)
            progress.mark_story_settled("6700_once")
            progress.mark_story_settled("6700_once")
            self.assertTrue(progress.story_was_settled("6700_once"))
            self.assertEqual(progress.snapshot()["settled_story_ids"], ["6700_once"])

    def test_run_once_starts_only_server_offered_scene(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["adventure"]["start_time"] = "23:59"
            config["safety"]["safe_mode"] = False
            config["safety"]["allow_experimental_scene_actions"] = True
            store.save(config)

            class FakeClient:
                started = None

                def query_values(self):
                    return PetValues(gold=1000, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    from qqpet_app.client import FoodInventory

                    return FoodInventory(biscuits=12, shrimp=10)

                def start_school(self, option, preferred_sub_event=0):
                    self.started = ("school", option)
                    self.assert_preferred = preferred_sub_event
                    return SchoolStartResult(
                        SchoolCourse("田径运动课", 6115004, "力量+25", "30分钟", can_do=True),
                        "6100_created",
                    )

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "school")
            self.assertEqual(fake.started, ("school", "physical"))
            self.assertEqual(scheduler.progress.snapshot()["pending"]["kind"], "school")

    def test_run_once_starts_work_through_real_career_interface(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["scheduler"]["coin_threshold"] = 500
            config["adventure"]["enabled"] = False
            config["safety"]["safe_mode"] = False
            config["safety"]["allow_experimental_scene_actions"] = True
            store.save(config)

            class FakeClient:
                started = None

                def query_values(self):
                    return PetValues(gold=1, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    from qqpet_app.client import FoodInventory

                    return FoodInventory(biscuits=12, shrimp=10)

                def start_work(self, career_type, preferred_sub_event, strategy):
                    self.started = (career_type, preferred_sub_event, strategy)
                    return WorkStartResult(
                        WorkJob(
                            1,
                            "涂鸦小徒",
                            "熬夜赶参赛稿",
                            6411004,
                            "金币 539",
                            "4小时",
                            can_do=True,
                        ),
                        "6400_created",
                    )

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "work")
            self.assertEqual(fake.started, (0, 0, "highest_total"))
            self.assertEqual(scheduler.progress.snapshot()["pending"]["kind"], "work")

    def test_run_once_starts_adventure_through_real_option_interface(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["adventure"]["enabled"] = True
            config["adventure"]["start_time"] = "00:00"
            config["safety"]["safe_mode"] = False
            config["safety"]["allow_experimental_scene_actions"] = True
            store.save(config)

            class FakeClient:
                started = None

                def query_values(self):
                    return PetValues(gold=1000, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    from qqpet_app.client import FoodInventory

                    return FoodInventory(biscuits=12, shrimp=10)

                def start_adventure(self, preferred_name):
                    self.started = preferred_name
                    return AdventureStartResult(
                        AdventureOption(
                            "打招呼",
                            duration="45秒",
                            cost="体力5，清洁5",
                            can_do=True,
                        ),
                        "6700_created",
                    )

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "adventure")
            self.assertEqual(fake.started, "")
            self.assertEqual(
                scheduler.progress.snapshot()["pending"]["kind"], "adventure"
            )

    def test_empty_biscuit_inventory_blocks_tasks_without_feeding(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["safety"]["safe_mode"] = False
            config["care"]["auto_buy_supplies"] = False
            store.save(config)

            class FakeClient:
                def query_values(self):
                    return PetValues(gold=1000, hunger=1, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    from qqpet_app.client import FoodInventory

                    return FoodInventory(biscuits=0, shrimp=10)

                def feed(self):
                    raise AssertionError("饼干为零时不应发送当前喂食请求")

            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: FakeClient(),
            )
            self.assertEqual(scheduler.run_once(), "feed_unavailable")
            self.assertIsNotNone(scheduler.progress.active_care_block("feed"))
            self.assertIsNone(scheduler.progress.snapshot()["pending"])

    def test_care_runs_while_story_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["safety"]["safe_mode"] = False
            config["care"]["hunger_threshold"] = 80
            store.save(config)

            class FakeClient:
                fed = False

                def query_values(self):
                    return PetValues(gold=1000, hunger=90 if self.fed else 79, clean=100)

                def query_story(self):
                    return StoryStatus("6100_active", 2, remaining_seconds=600, duration_seconds=1800)

                def query_food_inventory(self):
                    from qqpet_app.client import FoodInventory

                    return FoodInventory(biscuits=9 if self.fed else 10, shrimp=10)

                def feed(self):
                    self.fed = True

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "feed")
            self.assertTrue(fake.fed)
            self.assertEqual(scheduler.progress.count("feed"), 1)


if __name__ == "__main__":
    unittest.main()

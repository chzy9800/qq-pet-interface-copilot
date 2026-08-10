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
    OidbResponse,
    PKPower,
    PKOpponent,
    PKResult,
    QQPetError,
    QQFriend,
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
    def test_friend_care_feeds_only_listed_hungry_friend_and_verifies_change(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["safety"]["safe_mode"] = False
            config["friend_care"].update(
                {
                    "enabled": True,
                    "check_interval_seconds": 15,
                    "hunger_threshold": 80,
                    "verify_delay_seconds": 0,
                    "targets": [
                        {"uin": "10001", "pet_id": "friend-pet", "name": "好友甲"}
                    ],
                }
            )
            store.save(config)

            class FriendClient:
                def __init__(self):
                    self.fed = False

                def query_values(self):
                    return PetValues(hunger=90 if self.fed else 50, clean=90)

                def feed(self):
                    self.fed = True

            friend_client = FriendClient()

            def factory(received):
                self.assertEqual(received["account"]["pet_id"], "friend-pet")
                return friend_client

            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=factory,
            )
            self.assertEqual(
                scheduler._run_friend_care_if_due(object(), config), "friend_feed"
            )
            self.assertTrue(friend_client.fed)
            self.assertEqual(scheduler.progress.count("friend_feed"), 1)
            self.assertEqual(scheduler.progress.count("pk"), 0)

    def test_friend_care_does_not_feed_friend_above_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["friend_care"].update(
                {
                    "enabled": True,
                    "check_interval_seconds": 15,
                    "hunger_threshold": 80,
                    "targets": [
                        {"uin": "10001", "pet_id": "friend-pet", "name": "好友甲"}
                    ],
                }
            )
            store.save(config)

            class FriendClient:
                def query_values(self):
                    return PetValues(hunger=80, clean=90)

                def feed(self):
                    raise AssertionError("达到阈值的好友不应被喂食")

            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: FriendClient(),
            )
            self.assertIsNone(scheduler._run_friend_care_if_due(object(), config))
            self.assertEqual(scheduler.progress.count("friend_feed"), 0)

    def test_friend_care_does_not_count_success_code_without_hunger_change(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["safety"]["safe_mode"] = False
            config["friend_care"].update(
                {
                    "enabled": True,
                    "check_interval_seconds": 15,
                    "hunger_threshold": 80,
                    "verify_delay_seconds": 0,
                    "failure_cooldown_seconds": 600,
                    "targets": [
                        {"uin": "10001", "pet_id": "friend-pet", "name": "好友甲"}
                    ],
                }
            )
            store.save(config)

            class FriendClient:
                def query_values(self):
                    return PetValues(hunger=50, clean=90)

                def feed(self):
                    return None

            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: FriendClient(),
            )
            self.assertEqual(
                scheduler._run_friend_care_if_due(object(), config),
                "friend_feed_failed",
            )
            self.assertEqual(scheduler.progress.count("friend_feed"), 0)
            self.assertIsNotNone(scheduler.progress.active_care_block("friend_feed:10001"))

    def test_employed_recall_waits_for_best_split_and_counts_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["safety"]["safe_mode"] = False
            config["story"]["employed_recall_mode"] = "best_split"
            store.save(config)
            scheduler = Scheduler(root / "config.yaml", root / "progress.json")

            class FakeClient:
                def __init__(self):
                    self.settled = []

                def settle_story(self, story_id):
                    self.settled.append(story_id)
                    return OidbResponse(38752, 1, 0, b"verified", b"raw")

            fake = FakeClient()
            before = StoryStatus(
                "6500_employed", 51, remaining_seconds=76, duration_seconds=100, recallable=True
            )
            self.assertTrue(scheduler._handle_story(fake, config, before))
            self.assertEqual(fake.settled, [])
            self.assertEqual(scheduler.progress.count("employed"), 0)

            at_best_split = StoryStatus(
                "6500_employed", 51, remaining_seconds=75, duration_seconds=100, recallable=True
            )
            self.assertTrue(scheduler._handle_story(fake, config, at_best_split))
            self.assertEqual(fake.settled, ["6500_employed"])
            self.assertEqual(scheduler.progress.count("employed"), 1)
            self.assertFalse(scheduler._handle_story(fake, config, at_best_split))
            self.assertEqual(fake.settled, ["6500_employed"])
            self.assertEqual(scheduler.progress.count("employed"), 1)

    def test_employed_recall_immediate_does_not_wait_for_25_percent(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["safety"]["safe_mode"] = False
            config["story"]["employed_recall_mode"] = "immediate"
            store.save(config)
            scheduler = Scheduler(root / "config.yaml", root / "progress.json")

            class FakeClient:
                def __init__(self):
                    self.settled = []

                def settle_story(self, story_id):
                    self.settled.append(story_id)
                    return OidbResponse(38752, 1, 0, b"verified", b"raw")

            fake = FakeClient()
            story = StoryStatus(
                "6500_employed", 51, remaining_seconds=99, duration_seconds=100, recallable=True
            )
            self.assertTrue(scheduler._handle_story(fake, config, story))
            self.assertEqual(fake.settled, ["6500_employed"])
            self.assertEqual(scheduler.progress.count("employed"), 1)

    def test_failure_alert_threshold_and_recovery_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["notifications"].update(
                {"enabled": True, "failure_threshold": 2, "cooldown_seconds": 3600, "send_recovery": True}
            )
            store.save(config)
            scheduler = Scheduler(root / "config.yaml", root / "progress.json")
            sent = []
            scheduler._send_notification_async = lambda title, content, event: sent.append((title, content, event))
            scheduler._record_failure("first")
            self.assertEqual(sent, [])
            scheduler._record_failure("second")
            scheduler._record_failure("third")
            self.assertEqual([item[2] for item in sent], ["failure"])
            scheduler._record_success()
            self.assertEqual([item[2] for item in sent], ["failure", "recovery"])

    def test_reconnect_uses_backoff_and_resumes_after_login_probe(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["napcat"]["reconnect_initial_seconds"] = 0.01
            config["napcat"]["reconnect_max_seconds"] = 0.02
            store.save(config)
            attempts = []

            class FakeClient:
                def check_connection(self):
                    attempts.append(1)
                    if len(attempts) == 1:
                        raise QQPetError("offline")
                    return "123456"

            logs = []
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                log=logs.append,
                client_factory=lambda _config: FakeClient(),
            )
            self.assertTrue(scheduler._reconnect_until_ready())
            self.assertEqual(len(attempts), 2)
            self.assertTrue(any("登录会话已恢复" in line for line in logs))

    def test_auto_pk_runs_once_and_persists_verified_daily_result(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["pk"].update(
                {
                    "enabled": True,
                    "start_time": "00:00",
                    "max_per_day": 1,
                    "opponent_mode": "fixed",
                    "opponent_uin": "10001",
                    "opponent_pet_id": "friend-pet",
                    "opponent_name": "弱对手",
                    "opponent_power": 10,
                }
            )
            config["school"]["enabled"] = False
            config["work"]["enabled"] = False
            config["adventure"]["enabled"] = False
            config["safety"]["safe_mode"] = False
            store.save(config)

            class FakeClient:
                runs = 0

                def query_values(self):
                    return PetValues(feel=80, gold=100, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    return FoodInventory(biscuits=10, shrimp=10)

                def query_pk_power(self, pet_id=""):
                    return PKPower(pet_id or "self", 10 if pet_id else 1713)

                def perform_pk(self, uin, pet_id, wait_seconds):
                    self.runs += 1
                    return PKResult(
                        uin,
                        pet_id,
                        "6900_verified",
                        PetValues(feel=80, gold=100, hunger=100, clean=100),
                        PetValues(feel=82, gold=142, hunger=95, clean=95),
                        OidbResponse(38752, 1, 0, b"result", b"raw"),
                    )

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "pk")
            self.assertEqual(scheduler.run_once(), None)
            self.assertEqual(fake.runs, 1)
            saved = scheduler.pk_progress.snapshot()
            self.assertEqual(saved["success"], 1)
            self.assertEqual(saved["gold_earned"], 42)
            self.assertTrue(saved["records"][0]["verified"])

    def test_auto_pk_runs_while_primary_story_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["pk"].update(
                {
                    "enabled": True,
                    "start_time": "00:00",
                    "max_per_day": 1,
                    "opponent_mode": "fixed",
                    "opponent_uin": "10001",
                    "opponent_pet_id": "friend-pet",
                    "opponent_name": "弱对手",
                    "opponent_power": 10,
                }
            )
            config["safety"]["safe_mode"] = False
            store.save(config)

            class FakeClient:
                runs = 0

                def query_values(self):
                    return PetValues(feel=80, gold=100, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus(
                        story_id="6400_working",
                        state_code=51,
                        remaining_seconds=600,
                        duration_seconds=14400,
                    )

                def query_food_inventory(self):
                    return FoodInventory(biscuits=10, shrimp=10)

                def query_pk_power(self, pet_id=""):
                    return PKPower(pet_id or "self", 10 if pet_id else 1713)

                def perform_pk(self, uin, pet_id, wait_seconds):
                    self.runs += 1
                    return PKResult(
                        uin,
                        pet_id,
                        "6900_verified",
                        PetValues(feel=80, gold=100, hunger=100, clean=100),
                        PetValues(feel=82, gold=142, hunger=95, clean=95),
                        OidbResponse(38752, 1, 0, b"result", b"raw"),
                    )

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "pk")
            self.assertEqual(fake.runs, 1)
            self.assertEqual(scheduler.pk_progress.snapshot()["success"], 1)

    def test_friend_care_runs_before_an_active_primary_story(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["friend_care"]["enabled"] = True
            store.save(config)

            class FakeClient:
                def query_values(self):
                    return PetValues(feel=80, gold=100, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus(
                        story_id="6500_studying",
                        state_code=2,
                        remaining_seconds=600,
                        duration_seconds=3600,
                    )

                def query_food_inventory(self):
                    return FoodInventory(biscuits=10, shrimp=10)

            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: FakeClient(),
            )
            scheduler._run_friend_care_if_due = lambda _client, _config: "friend_feed"
            self.assertEqual(scheduler.run_once(), "friend_feed")

    def test_auto_pk_uses_each_friend_three_times_before_switching(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["pk"].update(
                {
                    "enabled": True,
                    "start_time": "00:00",
                    "max_per_day": 4,
                    "opponent_mode": "all_friends",
                    "opponent_uin": "",
                    "opponent_pet_id": "",
                }
            )
            config["school"]["enabled"] = False
            config["work"]["enabled"] = False
            config["adventure"]["enabled"] = False
            config["safety"]["safe_mode"] = False
            store.save(config)

            class FakeClient:
                opponents = []

                def query_values(self):
                    return PetValues(feel=80, gold=100, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    return FoodInventory(biscuits=10, shrimp=10)

                def query_pk_friend_candidates(self):
                    return (
                        PKOpponent("10001", "pet-a", "甲", power=20),
                        PKOpponent("10002", "pet-b", "乙", power=10),
                    )

                def query_pk_power(self, pet_id=""):
                    powers = {"pet-a": 20, "pet-b": 10}
                    return PKPower(pet_id or "self", powers.get(pet_id, 100))

                def perform_pk(self, uin, pet_id, wait_seconds):
                    self.opponents.append(uin)
                    return PKResult(
                        uin,
                        pet_id,
                        f"6900_{uin}",
                        PetValues(feel=80, gold=100, hunger=100, clean=100),
                        PetValues(feel=82, gold=142, hunger=95, clean=95),
                        OidbResponse(38752, 1, 0, b"result", b"raw"),
                    )

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            for _ in range(4):
                self.assertEqual(scheduler.run_once(), "pk")
            self.assertEqual(fake.opponents, ["10002", "10002", "10002", "10001"])

    def test_daily_friend_scan_runs_once_after_configured_time(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["friend_visits"]["enabled"] = True
            config["friend_visits"]["start_time"] = "00:00"
            config["friend_visits"]["max_per_day"] = 1
            store.save(config)

            class FakeClient:
                scans = 0

                def query_friend_list(self):
                    self.scans += 1
                    return (QQFriend("10001", "甲"), QQFriend("10002", "乙"))

                def query_values(self):
                    return PetValues(gold=1000, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus(
                        "6400_active", 51, remaining_seconds=100, duration_seconds=200
                    )

                def query_food_inventory(self):
                    return FoodInventory(biscuits=12, shrimp=10)

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )

            scheduler.run_once()
            scheduler.run_once()
            self.assertEqual(fake.scans, 1)
            self.assertEqual(
                scheduler.friend_progress.snapshot()["scan"]["eligible"], 1
            )

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

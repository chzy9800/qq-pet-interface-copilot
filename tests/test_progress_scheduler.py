from __future__ import annotations

import copy
import tempfile
import unittest
import json
from datetime import datetime
from pathlib import Path

from qqpet_app.client import (
    AdventureOption,
    AdventureStartResult,
    BathInventory,
    EncourageResult,
    FoodInventory,
    FoodItem,
    PageRules,
    PetValues,
    OidbResponse,
    PKPower,
    PKOpponent,
    PKResult,
    QQPetEmptyResponse,
    QQPetConnectionError,
    QQPetError,
    QQFriend,
    SchoolCourse,
    SchoolStartResult,
    StoryStatus,
    WorkJob,
    WorkEligibilityError,
    WorkStartResult,
)
from qqpet_app.config import ConfigStore, DEFAULT_CONFIG
from qqpet_app.progress import DailyProgress
from qqpet_app.scheduler import Scheduler


class ProgressAndSchedulerTests(unittest.TestCase):
    def test_login_guard_blocks_all_pet_reads_for_wrong_account(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["account"]["uin"] = "123456"
            store.save(config)

            class WrongAccountClient:
                queried = False

                def check_connection(self):
                    return "654321"

                def query_values(self):
                    self.queried = True
                    raise AssertionError("账号不一致时不应读取或写入宠物接口")

            fake = WrongAccountClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            sent = []
            scheduler._send_notification_async = lambda title, content, event: sent.append(event)
            with self.assertRaisesRegex(QQPetConnectionError, "账号不一致"):
                scheduler.run_once()
            self.assertFalse(fake.queried)
            self.assertEqual(sent, ["login_guard"])

    def test_work_eligibility_error_falls_back_to_next_job_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["scheduler"]["coin_threshold"] = 500
            config["adventure"]["enabled"] = False
            config["safety"]["safe_mode"] = False
            config["safety"]["allow_experimental_scene_actions"] = True
            config["work"]["employ_friend"] = False
            store.save(config)

            rejected = WorkJob(
                3, "高级职业", "高级岗位", 6433001, "金币 600", "4小时", can_do=True
            )
            fallback = WorkJob(
                1, "基础职业", "基础岗位", 6411001, "金币 100", "1小时", can_do=True
            )

            class FakeClient:
                calls: list[int] = []

                def query_values(self):
                    return PetValues(gold=1, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    return FoodInventory(biscuits=12, shrimp=1)

                def select_work_job(
                    self,
                    _career_type,
                    _preferred_sub_event,
                    _strategy,
                    _hired_pet_id,
                    excluded_sub_events=(),
                ):
                    self.excluded = tuple(excluded_sub_events)
                    return fallback

                def start_work(
                    self,
                    career_type,
                    preferred_sub_event,
                    _strategy,
                    _hired_user_id,
                    _hired_pet_id,
                ):
                    selected = preferred_sub_event or rejected.sub_event_type
                    self.calls.append(selected)
                    if selected == rejected.sub_event_type:
                        raise WorkEligibilityError(
                            "你的宠物还未达到该职业参与要求", rejected
                        )
                    return WorkStartResult(fallback, "6400_fallback")

            fake = FakeClient()
            logs: list[str] = []
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                log=logs.append,
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "work")
            self.assertEqual(fake.calls, [6433001, 6411001])
            self.assertEqual(fake.excluded, (6433001,))
            self.assertTrue(any("正在尝试下一可用岗位" in line for line in logs))

    def test_work_eligibility_failure_is_cooled_down_between_scheduler_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["scheduler"]["coin_threshold"] = 500
            config["adventure"]["enabled"] = False
            config["safety"]["safe_mode"] = False
            config["safety"]["allow_experimental_scene_actions"] = True
            config["work"]["employ_friend"] = False
            config["care"]["failure_cooldown_seconds"] = 600
            store.save(config)

            rejected = WorkJob(
                3, "高级职业", "高级岗位", 6433001, "金币 600", "4小时", can_do=True
            )

            class FakeClient:
                start_calls = 0
                select_calls = 0

                def query_values(self):
                    return PetValues(gold=1, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    return FoodInventory(biscuits=12, shrimp=1)

                def select_work_job(
                    self,
                    _career_type,
                    _preferred_sub_event,
                    _strategy,
                    _hired_pet_id,
                    excluded_sub_events=(),
                ):
                    self.select_calls += 1
                    if excluded_sub_events:
                        raise WorkEligibilityError("没有符合当前宠物条件的开放岗位")
                    return rejected

                def start_work(self, *_args):
                    self.start_calls += 1
                    raise WorkEligibilityError(
                        "你的宠物还未达到该职业参与要求", rejected
                    )

            fake = FakeClient()
            logs: list[str] = []
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                log=logs.append,
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "work_unavailable")
            self.assertEqual(scheduler.run_once(), "work_blocked")
            self.assertEqual(fake.start_calls, 1)
            self.assertEqual(fake.select_calls, 1)
            self.assertTrue(any("600s 后重新读取" in line for line in logs))
            self.assertTrue(any("打工资格暂不可用" in line for line in logs))

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
            self.assertTrue(saved["daily_run_completed"])
            self.assertTrue(saved["daily_run_completed_at"])

    def test_auto_pk_does_not_start_before_daily_scheduled_time(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["pk"].update({"enabled": True, "start_time": "21:00"})
            store.save(config)
            scheduler = Scheduler(root / "config.yaml", root / "progress.json")
            result = scheduler._run_pk_if_due(
                object(),
                config,
                PetValues(hunger=100, clean=100),
                datetime(2026, 8, 12, 20, 59),
            )
            self.assertIsNone(result)
            self.assertFalse(scheduler.pk_progress.daily_run_completed())

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
            self.assertEqual(scheduler.run_once(), "pk")
            self.assertIsNone(scheduler.run_once())
            self.assertEqual(fake.opponents, ["10002", "10002", "10002", "10001"])
            self.assertTrue(scheduler.pk_progress.daily_run_completed())

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

                def check_connection(self):
                    return "99999"

                def query_friend_list(self):
                    self.scans += 1
                    return (QQFriend("10001", "甲"), QQFriend("10002", "乙"))

                def query_pk_friend_candidates(self):
                    return (
                        PKOpponent("10001", "pet-one", nickname="甲"),
                        PKOpponent("10002", "pet-two", nickname="乙"),
                    )

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

    def test_dispatch_priority_and_configurable_daily_limits(self) -> None:
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
            config["school"]["limit_enabled"] = True
            config["school"]["times_per_day"] = 11
            self.assertEqual(scheduler.decide(config, rich, now), "work")
            config["work"]["limit_enabled"] = True
            config["work"]["times_per_day"] = 1
            scheduler.progress.increment("work")
            self.assertIsNone(scheduler.decide(config, rich, now))

    def test_legacy_work_limit_is_migrated_but_school_stays_unlimited(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.yaml"
            legacy = copy.deepcopy(DEFAULT_CONFIG)
            legacy["account"]["pet_id"] = "pet"
            legacy["school"].pop("limit_enabled", None)
            legacy["school"]["times_per_day"] = 1
            legacy["work"].pop("limit_enabled", None)
            legacy["work"]["times_per_day"] = 3
            path.write_text(json.dumps(legacy), encoding="utf-8")
            migrated = ConfigStore(path).data
            self.assertFalse(migrated["school"]["limit_enabled"])
            self.assertTrue(migrated["work"]["limit_enabled"])
            self.assertEqual(migrated["work"]["times_per_day"], 3)

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

    def test_stale_settled_story_does_not_bypass_new_pending_guard(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            scheduler = Scheduler(root / "config.yaml", root / "progress.json")
            scheduler.progress.mark_story_settled("6100_old")
            scheduler.progress.set_pending("school")

            handled = scheduler._handle_story(
                None,
                store.data,
                StoryStatus(
                    story_id="6100_old",
                    remaining_seconds=0,
                    duration_seconds=3600,
                ),
            )

            self.assertTrue(handled)
            self.assertEqual(scheduler.progress.snapshot()["pending"]["kind"], "school")

    def test_run_once_starts_only_server_offered_scene(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["adventure"]["start_time"] = "23:59"
            config["safety"]["safe_mode"] = False
            config["safety"]["allow_experimental_scene_actions"] = True
            config["work"]["employ_friend"] = False
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

    def test_empty_start_response_waits_for_story_instead_of_resending(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["adventure"]["enabled"] = False
            config["safety"]["safe_mode"] = False
            config["safety"]["allow_experimental_scene_actions"] = True
            store.save(config)

            class FakeClient:
                starts = 0

                def query_values(self):
                    return PetValues(gold=1000, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    return FoodInventory(biscuits=12, shrimp=10)

                def start_school(self, _option, _preferred_sub_event=0):
                    self.starts += 1
                    raise QQPetEmptyResponse("OidbSvcTrpcTcp.0x975e_1")

            fake = FakeClient()
            logs: list[str] = []
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                log=logs.append,
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "school")
            self.assertEqual(fake.starts, 1)
            self.assertEqual(scheduler.progress.snapshot()["pending"]["kind"], "school")

            self.assertEqual(scheduler.run_once(), "story")
            self.assertEqual(fake.starts, 1)
            self.assertTrue(any("不会重复开课" in line for line in logs))

    def test_run_once_starts_work_through_real_career_interface(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["scheduler"]["coin_threshold"] = 500
            config["adventure"]["enabled"] = False
            config["safety"]["safe_mode"] = False
            config["safety"]["allow_experimental_scene_actions"] = True
            config["work"]["employ_friend"] = False
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

                def start_work(
                    self,
                    career_type,
                    preferred_sub_event,
                    strategy,
                    hired_user_id,
                    hired_pet_id,
                ):
                    self.assert_no_hire = (hired_user_id, hired_pet_id)
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
            self.assertEqual(fake.started, (0, 0, "shortest_duration"))
            self.assertEqual(fake.assert_no_hire, ("", ""))
            self.assertEqual(scheduler.progress.snapshot()["pending"]["kind"], "work")

    def test_run_once_employs_verified_friend_for_work(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["scheduler"]["coin_threshold"] = 500
            config["adventure"]["enabled"] = False
            config["safety"]["safe_mode"] = False
            config["safety"]["allow_experimental_scene_actions"] = True
            config["work"]["employ_friend"] = True
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

                def query_pk_friend_candidates(self):
                    return (
                        PKOpponent("10001", "pet-one", nickname="好友甲", power=10),
                        PKOpponent("10002", "pet-two", nickname="好友乙", power=20),
                    )

                def select_work_job(
                    self, career_type, preferred_sub_event, strategy, hired_pet_id
                ):
                    self.selected_with = hired_pet_id
                    return WorkJob(
                        1,
                        "涂鸦小徒",
                        "熬夜赶参赛稿",
                        6411004,
                        "金币 539",
                        "4小时",
                        can_do=True,
                    )

                def start_work(
                    self,
                    career_type,
                    preferred_sub_event,
                    strategy,
                    hired_user_id,
                    hired_pet_id,
                ):
                    self.started = (hired_user_id, hired_pet_id)
                    return WorkStartResult(
                        WorkJob(
                            career_type,
                            "涂鸦小徒",
                            "熬夜赶参赛稿",
                            preferred_sub_event,
                            "金币 539",
                            "4小时",
                            can_do=True,
                        ),
                        "6400_friend",
                        hired_friend=True,
                    )

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "work")
            self.assertEqual(fake.started, ("10002", "pet-two"))

    def test_tired_hired_friend_falls_back_to_solo_work_and_is_skipped_today(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["scheduler"]["coin_threshold"] = 500
            config["adventure"]["enabled"] = False
            config["safety"]["safe_mode"] = False
            config["safety"]["allow_experimental_scene_actions"] = True
            config["work"]["employ_friend"] = True
            store.save(config)

            class FakeClient:
                calls = []

                def query_values(self):
                    return PetValues(gold=1, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    return FoodInventory(biscuits=12, shrimp=10)

                def query_pk_friend_candidates(self):
                    return (
                        PKOpponent("10001", "pet-one", nickname="好友甲", power=10),
                        PKOpponent("10002", "pet-two", nickname="好友乙", power=20),
                    )

                def start_work(
                    self, career_type, preferred_sub_event, strategy,
                    hired_user_id, hired_pet_id,
                ):
                    self.calls.append((hired_user_id, hired_pet_id))
                    if hired_user_id:
                        raise QQPetError("该好友今天很累了，无法继续被雇佣")
                    return WorkStartResult(
                        WorkJob(
                            1, "职业", "普通岗位", 64001,
                            "金币 77", "10分钟", can_do=True,
                        ),
                        "6400_solo",
                        hired_friend=False,
                    )

            fake = FakeClient()
            logs = []
            scheduler = Scheduler(
                root / "config.yaml", root / "progress.json",
                log=logs.append, client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "work")
            self.assertEqual(fake.calls, [("10002", "pet-two"), ("", "")])
            self.assertIn("10002", scheduler.progress.work_hire_unavailable_uins())
            self.assertTrue(any("无好友开工" in line for line in logs))

            selected = scheduler._select_work_hire(fake, store.data)
            self.assertIsNotNone(selected)
            self.assertEqual(selected.user_id, "10001")

    def test_manual_work_hire_uses_configured_friend(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["work"]["employ_friend"] = True
            config["work"]["hire_mode"] = "manual"
            config["work"]["hire_friend_uin"] = "10001"
            config["work"]["hire_friend_pet_id"] = "saved-pet"
            config["work"]["hire_friend_name"] = "好友甲"
            store.save(config)
            scheduler = Scheduler(root / "config.yaml", root / "progress.json")

            class FakeClient:
                def query_pk_friend_candidates(self):
                    return (
                        PKOpponent("10001", "live-pet", nickname="好友甲", power=10),
                        PKOpponent("10002", "pet-two", nickname="好友乙", power=99),
                    )

            selected = scheduler._select_work_hire(FakeClient(), config)
            self.assertIsNotNone(selected)
            self.assertEqual(selected.user_id, "10001")
            self.assertEqual(selected.pet_id, "live-pet")

    def test_unavailable_saved_work_job_falls_back_before_start(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["scheduler"]["coin_threshold"] = 500
            config["adventure"]["enabled"] = False
            config["safety"]["safe_mode"] = False
            config["safety"]["allow_experimental_scene_actions"] = True
            config["work"].update(
                {
                    "career_type": 1,
                    "job_sub_event": 6411001,
                    "employ_friend": False,
                }
            )
            store.save(config)

            class FakeClient:
                def query_values(self):
                    return PetValues(gold=1, hunger=100, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    from qqpet_app.client import FoodInventory

                    return FoodInventory(biscuits=12, shrimp=10)

                def select_work_job(
                    self, career_type, preferred_sub_event, strategy, hired_pet_id
                ):
                    if preferred_sub_event:
                        raise QQPetError("指定岗位暂不可用")
                    return WorkJob(
                        2,
                        "开放职业",
                        "当前最高收益岗位",
                        6422002,
                        "金币 600",
                        "4小时",
                        can_do=True,
                    )

                def start_work(
                    self,
                    career_type,
                    preferred_sub_event,
                    strategy,
                    hired_user_id,
                    hired_pet_id,
                ):
                    self.started = (career_type, preferred_sub_event)
                    return WorkStartResult(
                        WorkJob(
                            career_type,
                            "开放职业",
                            "当前最高收益岗位",
                            preferred_sub_event,
                            "金币 600",
                            "4小时",
                            can_do=True,
                        ),
                        "6400_fallback",
                    )

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "work")
            self.assertEqual(fake.started, (2, 6422002))

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

    def test_auto_care_can_feed_selected_shrimp(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["safety"]["safe_mode"] = False
            config["care"]["food_item"] = "shrimp"
            config["care"]["verify_delay_seconds"] = 0
            store.save(config)

            class FakeClient:
                fed = False
                selected = ""

                def query_values(self):
                    return PetValues(hunger=90 if self.fed else 70, clean=100)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    return FoodInventory(biscuits=10, shrimp=4 if self.fed else 5)

                def query_food_items(self):
                    return (FoodItem("3", "虾仁", 4 if self.fed else 5),)

                def feed(self, food_id=""):
                    self.selected = food_id
                    self.fed = True

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "feed")
            self.assertEqual(fake.selected, "3")

    def test_auto_care_can_wash_with_selected_bath_ball(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["safety"]["safe_mode"] = False
            config["care"]["bath_item"] = "bath_ball"
            config["care"]["verify_delay_seconds"] = 0
            store.save(config)

            class FakeClient:
                washed = False
                selected = ""

                def query_values(self):
                    return PetValues(hunger=100, clean=90 if self.washed else 70)

                def query_story(self):
                    return StoryStatus()

                def query_food_inventory(self):
                    return FoodInventory(biscuits=10, shrimp=5)

                def query_bath_inventory(self):
                    return BathInventory((('1', 10), ('2', 4 if self.washed else 5)))

                def use_bath_item(self, item_id):
                    self.selected = item_id
                    self.washed = True

            fake = FakeClient()
            scheduler = Scheduler(
                root / "config.yaml",
                root / "progress.json",
                client_factory=lambda _config: fake,
            )
            self.assertEqual(scheduler.run_once(), "wash")
            self.assertEqual(fake.selected, "2")

    def test_encouragement_is_persisted_and_only_sent_once_per_story(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ConfigStore(root / "config.yaml")
            config = store.data
            config["safety"]["safe_mode"] = False
            store.save(config)

            class FakeClient:
                calls = 0

                def encourage_story(self, story_id):
                    self.calls += 1
                    return EncourageResult(credit=20, toast="鼓励成功")

            fake = FakeClient()
            scheduler = Scheduler(root / "config.yaml", root / "progress.json")
            self.assertTrue(
                scheduler._encourage_story_if_needed(fake, config, "school", "6100_once")
            )
            self.assertTrue(
                scheduler._encourage_story_if_needed(fake, config, "school", "6100_once")
            )
            self.assertEqual(fake.calls, 1)
            self.assertTrue(scheduler.progress.story_was_encouraged("6100_once"))

    def test_old_highest_total_config_is_migrated_to_shortest_duration(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.yaml"
            store = ConfigStore(path)
            old = store.data
            old["work"]["strategy"] = "highest_total"
            path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
            store.reload(force=True)
            self.assertEqual(store.data["work"]["strategy"], "shortest_duration")


if __name__ == "__main__":
    unittest.main()

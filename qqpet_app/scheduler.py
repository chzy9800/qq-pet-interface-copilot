from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .client import NapCatClient, PetValues, QQPetError, StoryStatus
from .config import ConfigStore
from .progress import DailyProgress


LogCallback = Callable[[str], None]
StatusCallback = Callable[[PetValues | None, StoryStatus | None, dict], None]
ActivityCallback = Callable[[str], None]


class Scheduler:
    def __init__(
        self,
        config_path: str | Path,
        progress_path: str | Path,
        log: LogCallback | None = None,
        status_callback: StatusCallback | None = None,
        activity_callback: ActivityCallback | None = None,
        client_factory: Callable[[dict], NapCatClient] | None = None,
    ) -> None:
        self.config_store = ConfigStore(config_path)
        self.progress = DailyProgress(progress_path)
        self.log_callback = log or print
        self.status_callback = status_callback
        self.activity_callback = activity_callback
        self.client_factory = client_factory or self._make_client
        self._stop = threading.Event()

    @staticmethod
    def _make_client(config: dict) -> NapCatClient:
        return NapCatClient(
            config["napcat"]["url"],
            config["napcat"]["token"],
            config["account"]["pet_id"],
            float(config["napcat"]["timeout_seconds"]),
        )

    def log(self, message: str) -> None:
        self.log_callback(f"[{datetime.now():%H:%M:%S}] {message}")

    def activity(self, message: str) -> None:
        if self.activity_callback:
            self.activity_callback(message)

    @staticmethod
    def _action_name(kind: str | None) -> str:
        return {
            "school": "学习",
            "work": "打工",
            "adventure": "冒险",
            "employed": "被雇佣任务",
        }.get(kind or "", "任务")

    def stop(self) -> None:
        self._stop.set()

    def decide(self, config: dict, values: PetValues, now: datetime | None = None) -> str | None:
        now = now or datetime.now()
        counts = self.progress.snapshot()["counts"]
        adventure = config["adventure"]
        due = now.strftime("%H:%M") >= adventure["start_time"]
        adventure_limit = int(adventure["times_per_day"])
        if adventure["enabled"] and due and (adventure_limit == 0 or counts["adventure"] < adventure_limit):
            return "adventure"

        points = (
            counts["school"] * int(config["scheduler"]["school_factor"])
            + counts["work"] * int(config["scheduler"]["work_factor"])
        )
        if points > int(config["scheduler"]["daily_point_limit"]):
            return "work" if self._under_limit(config, counts, "work") else None

        if values.gold >= float(config["scheduler"]["coin_threshold"]):
            if self._under_limit(config, counts, "school"):
                return "school"
            return "work" if self._under_limit(config, counts, "work") else None
        return "work" if self._under_limit(config, counts, "work") else None

    @staticmethod
    def _under_limit(config: dict, counts: dict, kind: str) -> bool:
        if not config[kind]["enabled"]:
            return False
        limit = int(config[kind]["times_per_day"])
        return limit == 0 or counts[kind] < limit

    def _safe_or_blocked(self, config: dict, action: str, experimental: bool = False) -> bool:
        if config["safety"]["safe_mode"]:
            self.log(f"安全模式：计划执行 {action}，本轮不发送写请求")
            return True
        if experimental and not config["safety"]["allow_experimental_scene_actions"]:
            self.log(f"已阻止实验性动作 {action}；请在设置中明确开启")
            return True
        return False

    def _handle_story(self, client: NapCatClient, config: dict, story: StoryStatus) -> bool:
        state = self.progress.snapshot()
        pending = state.get("pending")
        if story.story_id:
            if story.finished and self.progress.story_was_settled(story.story_id):
                self.activity("上次任务已结算，正在准备下一项")
                return False
            if story.recallable and not pending:
                self.activity("正在召回被雇佣任务")
                if self._safe_or_blocked(config, "提前召回被雇佣任务"):
                    return True
                if self._settle_and_verify(client, config, story):
                    self.progress.increment("employed")
                    self.log("被雇佣任务已提前召回、验证并计数")
                return True

            if not pending:
                recovered_kind = self._story_kind(story.story_id)
                if recovered_kind:
                    self.progress.set_pending(recovered_kind, confirmed=True, story_id=story.story_id)
                    pending = self.progress.snapshot().get("pending")
                    self.log(f"恢复进行中的 {recovered_kind} 任务，后续将接着计数")
            if pending and not pending.get("confirmed"):
                self.progress.confirm_pending(story.story_id)
                pending = self.progress.snapshot().get("pending")
                self.log(f"任务已由服务器确认，storyId={story.story_id}")
            kind = pending.get("kind") if pending else self._story_kind(story.story_id)
            action_name = self._action_name(kind)
            if story.finished and config["story"]["auto_settle_when_end_time_reached"]:
                self.activity(f"正在结算{action_name}")
                if self._safe_or_blocked(config, "结算已完成任务"):
                    return True
                settled = self._settle_and_verify(client, config, story)
                if settled and pending:
                    self.progress.increment(pending["kind"])
                    self.progress.clear_pending()
                    self.log(f"{pending['kind']} 已结算、服务器验证通过并计数")
                return True
            self.activity(f"正在{action_name}（剩余 {story.remaining_seconds}s）")
            self.log(
                f"任务进行中：state={story.state_code}，进度 "
                f"{story.elapsed_seconds}/{story.duration_seconds}s，"
                f"剩余 {story.remaining_seconds}s，storyId={story.story_id}"
            )
            return True

        if pending:
            action_name = self._action_name(pending.get("kind"))
            self.activity(f"等待{action_name}任务确认")
            created = datetime.fromisoformat(pending["created_at"])
            age = (datetime.now().astimezone() - created).total_seconds()
            if pending.get("confirmed"):
                self.progress.increment(pending["kind"])
                self.progress.clear_pending()
                self.log(f"检测到任务已离开进行中状态，补记 {pending['kind']} 1 次")
            elif age >= float(config["story"]["start_confirm_seconds"]):
                self.progress.clear_pending()
                self.log(f"服务器未确认 {pending['kind']} 启动，已取消待确认记录")
            else:
                self.log(f"等待服务器确认 {pending['kind']} 启动（{age:.0f}s）")
            return True
        return False

    def _settle_and_verify(
        self, client: NapCatClient, config: dict, story: StoryStatus
    ) -> bool:
        block_key = f"settle:{story.story_id}"
        block = self.progress.active_care_block(block_key)
        if block:
            remaining = max(0, int(float(block["until"]) - datetime.now().astimezone().timestamp()))
            self.activity(f"结算未生效，{remaining}s 后重试")
            self.log(f"同一任务结算处于冷却期，{remaining}s 后重试，storyId={story.story_id}")
            return False

        try:
            response = client.settle_story(story.story_id)
        except QQPetError as exc:
            retry_seconds = float(config["story"]["settle_retry_seconds"])
            self.progress.set_care_block(
                block_key,
                f"结算请求失败：{exc}",
                retry_seconds,
            )
            self.activity(f"结算未生效，{int(retry_seconds)}s 后重试")
            self.log(
                f"结算请求失败：{exc}；"
                f"不计数，{int(retry_seconds)}s 后重试"
            )
            return False

        # GetPetStoryStatus intentionally keeps returning the last completed
        # story, so clearing of story_id is not a valid success condition.  The
        # mobile client treats a successful 0x9760 response as completion and
        # then renders the result carried in its response body.  Persist the ID
        # to make that acknowledgement idempotent across later scheduler runs.
        self.progress.mark_story_settled(story.story_id)
        self.progress.clear_care_block(block_key)
        self.log(
            f"结算结果已由服务器返回（{len(response.body)} bytes），"
            f"已记录 storyId，后续不会重复结算"
        )
        return True

    @staticmethod
    def _story_kind(story_id: str) -> str | None:
        prefix = story_id.split("_", 1)[0]
        return {"6100": "school", "6400": "work", "6700": "adventure"}.get(prefix)

    def _care_blocked(self, kind: str) -> bool:
        block = self.progress.active_care_block(kind)
        if not block:
            return False
        remaining = max(0, int(float(block["until"]) - datetime.now().astimezone().timestamp()))
        self.log(f"{block['reason']}；{remaining}s 后重试，本轮不启动其他任务")
        return True

    def _block_care(self, config: dict, kind: str, reason: str) -> None:
        seconds = float(config["care"]["failure_cooldown_seconds"])
        self.progress.set_care_block(kind, reason, seconds)
        self.log(f"{reason}；已暂停相关任务 {int(seconds)}s")

    def _verify_delay(self, config: dict) -> None:
        delay = max(0.0, float(config["care"]["verify_delay_seconds"]))
        if delay:
            self._stop.wait(delay)

    def run_once(self) -> str | None:
        self.activity("正在检查宠物状态")
        config = self.config_store.data
        if self.progress.rollover():
            self.log("检测到新的一天，昨日次数已归档，今日计数清零")
        client = self.client_factory(config)
        values = client.query_values()
        story = client.query_story()
        state = self.progress.snapshot()
        inventory = client.query_food_inventory()
        state["food_inventory"] = {
            "biscuits": inventory.biscuits,
            "shrimp": inventory.shrimp,
        }
        if self.status_callback:
            self.status_callback(values, story, state)
        self.log(
                f"状态：金币 {values.gold:.2f}，心情 {values.feel:.1f}，体力 {values.hunger:.1f}，"
            f"清洁 {values.clean:.1f}，饼干 {inventory.biscuits}，虾仁 {inventory.shrimp}，"
            f"今日 {state['counts']}"
        )

        if self._handle_story(client, config, story):
            return "story"

        care = config["care"]
        if care["enabled"] and values.hunger < float(care["hunger_threshold"]):
            if self._care_blocked("feed"):
                self.activity("等待喂食条件恢复")
                return "feed_blocked"
            if not self._safe_or_blocked(config, "喂食"):
                # The recovered Feeding request uses feed_type=0, which is the
                # biscuit path.  Do not assume it can consume shrimp.
                if inventory.biscuits <= 0:
                    if not care["auto_buy_supplies"]:
                        self.activity("缺少食物，自动购买未开启")
                        self._block_care(config, "feed", f"饼干不足（虾仁 {inventory.shrimp}），自动购买已关闭")
                        return "feed_unavailable"
                    self.activity("正在购买食物")
                    buy_count = int(care["food_purchase_count"])
                    before_count = inventory.biscuits
                    purchase = client.buy_food(buy_count)
                    inventory = client.query_food_inventory()
                    if purchase.bought <= 0 and inventory.biscuits <= before_count:
                        self._block_care(config, "feed", "金币购买饼干未到账")
                        self.activity("购买食物失败，等待重试")
                        return "feed_unavailable"
                    self.log(
                        f"饼干不足，已用金币购买 {purchase.bought or buy_count} 个，"
                        f"花费 {purchase.cost_gold}，当前饼干 {inventory.biscuits}"
                    )
                self.activity("正在喂食")
                client.feed()
                self._verify_delay(config)
                after = client.query_values()
                inventory_after = client.query_food_inventory()
                if after.hunger > values.hunger or inventory_after.biscuits < inventory.biscuits:
                    self.progress.clear_care_block("feed")
                    self.progress.increment("feed")
                    self.log(
                        f"体力不足，已自动喂食并验证成功：{values.hunger:.1f}→{after.hunger:.1f}，"
                        f"剩余饼干 {inventory_after.biscuits}、虾仁 {inventory_after.shrimp}"
                    )
                    self.activity("喂食完成，等待下一轮")
                else:
                    self._block_care(config, "feed", "喂食响应成功但体力和饼干库存均未变化")
                    self.activity("喂食未生效，等待重试")
            return "feed"
        if care["enabled"] and values.clean < float(care["clean_threshold"]):
            if self._care_blocked("wash"):
                self.activity("等待洗澡条件恢复")
                return "wash_blocked"
            if not self._safe_or_blocked(config, "洗澡"):
                bath_inventory = client.query_bath_inventory()
                if bath_inventory.soap <= 0:
                    if not care["auto_buy_supplies"]:
                        self.activity("缺少洗护用品，自动购买未开启")
                        self._block_care(config, "wash", "香皂片不足，自动购买已关闭")
                        return "wash_unavailable"
                    self.activity("正在购买洗护用品")
                    buy_count = int(care["soap_purchase_count"])
                    purchase = client.buy_bath_item("1", buy_count)
                    after_purchase = client.query_bath_inventory()
                    if not purchase.succeeded and after_purchase.soap <= bath_inventory.soap:
                        self._block_care(config, "wash", f"金币购买香皂片未成功（result={purchase.result}）")
                        self.activity("购买洗护用品失败，等待重试")
                        return "wash_unavailable"
                    bath_inventory = after_purchase
                    self.log(f"香皂片不足，已用金币购买 {buy_count} 个，当前香皂片 {bath_inventory.soap}")
                self.activity("正在洗澡")
                client.use_bath_item("1")
                self._verify_delay(config)
                after = client.query_values()
                bath_after = client.query_bath_inventory()
                if after.clean > values.clean or bath_after.soap < bath_inventory.soap:
                    self.progress.clear_care_block("wash")
                    self.progress.increment("wash")
                    self.log(
                        f"清洁不足，已消耗香皂片洗澡并验证成功："
                        f"{values.clean:.1f}→{after.clean:.1f}，剩余香皂片 {bath_after.soap}"
                    )
                    self.activity("洗澡完成，等待下一轮")
                else:
                    self._block_care(
                        config,
                        "wash",
                        "洗澡响应成功但清洁值和香皂片库存均未变化",
                    )
                    self.activity("洗澡未生效，等待重试")
            return "wash"

        action = self.decide(config, values)
        if not action:
            self.activity("空闲：今日任务已完成")
            self.log("今日已没有符合限制的任务")
            return None
        if self._safe_or_blocked(config, action, experimental=True):
            self.activity(f"已暂停：计划{self._action_name(action)}")
            return action
        action_name = self._action_name(action)
        self.activity(f"正在准备{action_name}")
        option = config[action].get("attribute") or config[action].get("option")
        rules = client.query_page_rules(6000)
        path = client.scene_path(action, option)
        if not rules.allows(path):
            count_text = "未知" if rules.declared_count is None else str(rules.declared_count)
            self.log(
                f"服务器当前未开放 {action}.{option}；"
                f"户外规则数 {count_text}，本轮不发送启动请求"
            )
            self.activity(f"当前无可执行{action_name}路线，本轮跳过")
            return None
        self.activity(f"正在启动{action_name}")
        client.start_scene(action, option, rules)
        self.progress.set_pending(action)
        self.log(
            f"服务器规则已确认 {action}.{option} 可用，已发送启动请求，"
            "等待 storyId 确认"
        )
        self.activity(f"等待{action_name}任务确认")
        return action

    def run_forever(self) -> None:
        self._stop.clear()
        self.activity("自动控制已启动，正在检查")
        self.log("接口调度器已启动")
        while not self._stop.is_set():
            try:
                self.run_once()
            except QQPetError as exc:
                self.activity("接口连接异常，等待重试")
                self.log(f"接口错误：{exc}")
            except Exception as exc:  # 保证后台循环不会因单轮配置错误退出
                self.activity("本轮执行失败，等待重试")
                self.log(f"本轮失败：{type(exc).__name__}: {exc}")
            interval = max(3.0, float(self.config_store.data["scheduler"]["interval_seconds"]))
            self._stop.wait(interval)
        self.activity("自动控制已停止")
        self.log("接口调度器已停止")

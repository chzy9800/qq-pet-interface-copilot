from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .client import NapCatClient, QQPetError


@dataclass(frozen=True)
class InterfaceTestResult:
    action: str
    target: str
    succeeded: bool
    detail: str


class InterfaceTestRunner:
    """Explicit, single-shot real actions used by the settings test console."""

    def __init__(self, client: NapCatClient, config: dict[str, Any]) -> None:
        self.client = client
        self.config = config

    def _require_write(self, *, story_action: bool = False) -> None:
        safety = self.config["safety"]
        if safety.get("safe_mode", True):
            raise QQPetError("安全模式已开启；真实接口测试前请关闭安全模式")
        if story_action and not safety.get("allow_experimental_scene_actions", False):
            raise QQPetError("尚未允许真实学习/打工/冒险，请先开启真实场景操作")

    def _require_idle_story(self) -> None:
        story = self.client.query_story()
        if story.story_id and not story.finished:
            raise QQPetError(
                f"当前已有任务进行中（storyId={story.story_id}，剩余 "
                f"{story.remaining_seconds}s），不能重复启动主任务"
            )

    def check_state(self) -> InterfaceTestResult:
        values = self.client.query_values()
        food = self.client.query_food_inventory()
        bath = self.client.query_bath_inventory()
        return InterfaceTestResult(
            "状态读取",
            "本人宠物",
            True,
            f"金币 {values.gold:.0f}，体力 {values.hunger:.0f}，清洁 {values.clean:.0f}，"
            f"饼干 {food.biscuits}，虾仁 {food.shrimp}，香皂片 {bath.soap}，沐浴球 {bath.bath_ball}",
        )

    def check_work_rules(self) -> InterfaceTestResult:
        catalog = self.client.query_work_catalog()
        overview = catalog.overview
        open_careers = [career for career in overview.careers if career.available]
        available_jobs = [
            job for job in catalog.jobs if job.can_do and job.sub_event_type > 0
        ]
        detail = f"开放职业 {len(open_careers)} 个，可执行岗位 {len(available_jobs)} 个"
        if available_jobs:
            detail += "；" + "、".join(
                f"{job.career_name}/{job.name}({job.sub_event_type})"
                for job in available_jobs
            )
        if catalog.rejected_careers:
            detail += "；当前宠物未满足：" + "、".join(
                career.name or str(career.career_type)
                for career, _message in catalog.rejected_careers
            )
        return InterfaceTestResult("岗位规则读取", "服务器职业目录", True, detail)

    def feed(self, food_id: str, food_name: str) -> InterfaceTestResult:
        self._require_write()
        before_values = self.client.query_values()
        before_inventory = self.client.query_food_inventory()
        before_items = {item.food_id: item.balance for item in self.client.query_food_items()}
        self.client.feed(food_id)
        time.sleep(float(self.config["care"].get("verify_delay_seconds", 1)))
        after_values = self.client.query_values()
        after_inventory = self.client.query_food_inventory()
        after_items = {item.food_id: item.balance for item in self.client.query_food_items()}
        selected_changed = bool(
            food_id
            and food_id in before_items
            and after_items.get(food_id, before_items[food_id]) < before_items[food_id]
        )
        inventory_changed = after_inventory.total < before_inventory.total
        succeeded = after_values.hunger > before_values.hunger or selected_changed or inventory_changed
        detail = (
            f"体力 {before_values.hunger:.0f}→{after_values.hunger:.0f}；"
            f"饼干 {before_inventory.biscuits}→{after_inventory.biscuits}，"
            f"虾仁 {before_inventory.shrimp}→{after_inventory.shrimp}"
        )
        if not succeeded:
            detail += "；服务器响应后状态与库存均未变化"
        return InterfaceTestResult("喂食", food_name, succeeded, detail)

    def wash(self, item_id: str, item_name: str) -> InterfaceTestResult:
        self._require_write()
        before_values = self.client.query_values()
        before = self.client.query_bath_inventory()
        response = self.client.use_bath_item(item_id)
        time.sleep(float(self.config["care"].get("verify_delay_seconds", 1)))
        after_values = self.client.query_values()
        after = self.client.query_bath_inventory()
        succeeded = after_values.clean > before_values.clean or after.count(item_id) < before.count(item_id)
        detail = (
            f"清洁 {before_values.clean:.0f}→{after_values.clean:.0f}；"
            f"库存 {before.count(item_id)}→{after.count(item_id)}；"
            f"返回 remaining={response.remaining}, completed={response.completed}"
        )
        if not succeeded:
            detail += "；服务器响应后状态与库存均未变化"
        return InterfaceTestResult("洗澡", item_name, succeeded, detail)

    def start_school(self, sub_event: int, label: str) -> InterfaceTestResult:
        self._require_write(story_action=True)
        self._require_idle_story()
        attribute = str(self.config["school"].get("attribute", "physical"))
        result = self.client.start_school(attribute, sub_event)
        story = self.client.query_story()
        succeeded = bool(result.story_id or story.story_id)
        return InterfaceTestResult(
            "学习开课",
            label,
            succeeded,
            f"storyId={result.story_id or story.story_id or '未返回'}，服务器剩余 {story.remaining_seconds}s",
        )

    def start_work(
        self,
        career_type: int,
        sub_event: int,
        label: str,
        hired_user_id: str = "",
        hired_pet_id: str = "",
        hired_name: str = "",
    ) -> InterfaceTestResult:
        self._require_write(story_action=True)
        self._require_idle_story()
        result = self.client.start_work(
            career_type,
            sub_event,
            "shortest_duration",
            hired_user_id,
            hired_pet_id,
        )
        story = self.client.query_story()
        succeeded = bool(result.story_id or story.story_id)
        hire_detail = ""
        if hired_user_id:
            hire_target = hired_name or hired_user_id
            friend_echoed = bool(
                story.raw_body
                and (
                    hired_pet_id.encode("utf-8") in story.raw_body
                    or hired_user_id.encode("utf-8") in story.raw_body
                )
            )
            if friend_echoed:
                hire_detail = f"，服务器任务状态已回显雇佣好友 {hire_target}"
            elif result.hired_friend:
                hire_detail = (
                    f"，雇佣好友 {hire_target} 已写入开工封包；任务已开始，"
                    "但状态接口未回显好友身份"
                )
            else:
                hire_detail = f"，好友 {hire_target} 未写入开工结果"
        return InterfaceTestResult(
            "雇佣好友开工" if hired_user_id else "打工开工",
            label,
            succeeded,
            f"storyId={result.story_id or story.story_id or '未返回'}，"
            f"服务器剩余 {story.remaining_seconds}s{hire_detail}",
        )

    def recall_employed(self) -> InterfaceTestResult:
        self._require_write(story_action=True)
        before = self.client.query_story()
        if not before.story_id or not before.recallable:
            raise QQPetError("当前没有可召回的被雇佣任务")
        if not before.story_id.startswith("6500_"):
            raise QQPetError(
                f"当前任务 {before.story_id} 不是被雇佣任务，已阻止召回测试"
            )
        response = self.client.settle_story(before.story_id)
        time.sleep(float(self.config["care"].get("verify_delay_seconds", 1)))
        after = self.client.query_story()
        succeeded = bool(
            response.body
            and (
                not after.story_id
                or after.story_id != before.story_id
                or after.finished
                or not after.recallable
            )
        )
        detail = (
            f"召回前 storyId={before.story_id}，进度 "
            f"{before.elapsed_seconds}/{before.duration_seconds}s；"
            f"召回后 storyId={after.story_id or '无'}，recallable={after.recallable}"
        )
        if not succeeded:
            detail += "；服务器仍返回同一个可召回任务，未确认召回生效"
        return InterfaceTestResult("被雇佣召回", before.story_id, succeeded, detail)

    def start_adventure(self, option_name: str, label: str) -> InterfaceTestResult:
        self._require_write(story_action=True)
        self._require_idle_story()
        result = self.client.start_adventure(option_name)
        story = self.client.query_story()
        succeeded = bool(result.story_id or story.story_id)
        return InterfaceTestResult(
            "冒险启动",
            label,
            succeeded,
            f"storyId={result.story_id or story.story_id or '未返回'}，服务器剩余 {story.remaining_seconds}s",
        )

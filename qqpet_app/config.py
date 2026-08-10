from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "napcat": {
        "url": "http://127.0.0.1:6201",
        "token": "CHANGE_ME_LOCAL_TOKEN",
        "timeout_seconds": 15,
        "auto_reconnect": True,
        "reconnect_initial_seconds": 3,
        "reconnect_max_seconds": 60,
    },
    "account": {
        "uin": "YOUR_QQ_UIN",
        "pet_id": "YOUR_PET_ID",
    },
    "scheduler": {
        "interval_seconds": 15,
        "coin_threshold": 500,
    },
    "school": {
        "enabled": True,
        "attribute": "physical",
        "course_sub_event": 0,
    },
    "work": {
        "enabled": True,
        "attribute": "culture",
        "career_type": 0,
        "job_sub_event": 0,
        "strategy": "highest_total",
        "times_per_day": 0,
        "employ_friend": True,
    },
    "adventure": {
        "enabled": True,
        "option_name": "",
        "start_time": "20:00",
        "times_per_day": 3,
    },
    "pk": {
        "enabled": False,
        "start_time": "00:00",
        "max_per_day": 10,
        "opponent_mode": "all_friends",
        "friend_whitelist": "",
        "friend_exclude": "",
        "friend_refresh_seconds": 1800,
        "per_friend_limit": 3,
        "opponent_uin": "",
        "opponent_pet_id": "",
        "opponent_name": "",
        "opponent_power": 0,
        "only_weaker": True,
        "minimum_hunger": 80,
        "minimum_clean": 80,
        "wait_seconds": 9,
        "retry_cooldown_seconds": 300,
    },
    "friend_visits": {
        "enabled": False,
        "start_time": "21:00",
        "max_per_day": 20,
        "interval_min_seconds": 3,
        "interval_max_seconds": 5,
        "poke_enabled": False,
        "whitelist": "",
        "exclude": "",
    },
    "care": {
        "enabled": True,
        "hunger_threshold": 80,
        "clean_threshold": 80,
        "auto_buy_supplies": True,
        "food_purchase_count": 10,
        "soap_purchase_count": 10,
        "verify_delay_seconds": 1,
        "failure_cooldown_seconds": 3600,
    },
    "story": {
        "recall_check_seconds": 15,
        "start_confirm_seconds": 45,
        "settle_retry_seconds": 60,
        "auto_settle_when_end_time_reached": True,
    },
    "safety": {
        "safe_mode": True,
        "allow_experimental_scene_actions": False,
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


class ConfigStore:
    """config.yaml 使用 JSON 语法；JSON 本身是合法的 YAML 1.2。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._mtime_ns = -1
        self._config = copy.deepcopy(DEFAULT_CONFIG)
        if not self.path.exists():
            self.save(self._config)
        self.reload(force=True)

    @property
    def data(self) -> dict[str, Any]:
        self.reload()
        return copy.deepcopy(self._config)

    def reload(self, force: bool = False) -> bool:
        mtime = self.path.stat().st_mtime_ns
        if not force and mtime == self._mtime_ns:
            return False
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("config.yaml 顶层必须是对象")
        self._config = _merge(DEFAULT_CONFIG, loaded)
        self._validate(self._config)
        self._mtime_ns = mtime
        return True

    def save(self, config: dict[str, Any]) -> None:
        merged = _merge(DEFAULT_CONFIG, config)
        self._validate(merged)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)
        self._config = merged
        self._mtime_ns = self.path.stat().st_mtime_ns

    @staticmethod
    def _validate(config: dict[str, Any]) -> None:
        if not config["account"]["pet_id"]:
            raise ValueError("account.pet_id 不能为空")
        if float(config["napcat"]["timeout_seconds"]) <= 0:
            raise ValueError("napcat.timeout_seconds 必须大于 0")
        initial = float(config["napcat"]["reconnect_initial_seconds"])
        maximum = float(config["napcat"]["reconnect_max_seconds"])
        if initial <= 0 or maximum < initial:
            raise ValueError("自动重连间隔必须大于 0，且最大间隔不能小于初始间隔")
        if config["school"]["attribute"] not in {"culture", "physical", "art"}:
            raise ValueError("school.attribute 必须是 culture/physical/art")
        if int(config["school"].get("course_sub_event", 0)) < 0:
            raise ValueError("school.course_sub_event 不能小于 0")
        if config["work"]["attribute"] not in {"culture", "physical", "art"}:
            raise ValueError("work.attribute 必须是 culture/physical/art")
        if int(config["work"].get("career_type", 0)) < 0:
            raise ValueError("work.career_type 不能小于 0")
        if int(config["work"].get("job_sub_event", 0)) < 0:
            raise ValueError("work.job_sub_event 不能小于 0")
        if config["work"].get("strategy", "highest_total") != "highest_total":
            raise ValueError("work.strategy 目前仅支持 highest_total")
        if not isinstance(config["adventure"].get("option_name", ""), str):
            raise ValueError("adventure.option_name 必须是字符串")
        hours, minutes = map(int, str(config["adventure"]["start_time"]).split(":"))
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            raise ValueError("adventure.start_time 必须是 HH:MM")
        pk_hours, pk_minutes = map(int, str(config["pk"]["start_time"]).split(":"))
        if not (0 <= pk_hours <= 23 and 0 <= pk_minutes <= 59):
            raise ValueError("pk.start_time 必须是 HH:MM")
        if int(config["pk"]["max_per_day"]) < 0:
            raise ValueError("pk.max_per_day 不能小于 0")
        if int(config["pk"].get("per_friend_limit", 3)) <= 0:
            raise ValueError("pk.per_friend_limit 必须大于 0")
        if config["pk"].get("opponent_mode", "fixed") not in {
            "all_friends",
            "fixed",
        }:
            raise ValueError("pk.opponent_mode 必须是 all_friends/fixed")
        if bool(config["pk"]["opponent_uin"]) != bool(config["pk"]["opponent_pet_id"]):
            raise ValueError("pk.opponent_uin 与 pk.opponent_pet_id 必须同时填写")
        for key in (
            "opponent_power",
            "minimum_hunger",
            "minimum_clean",
            "wait_seconds",
            "retry_cooldown_seconds",
            "friend_refresh_seconds",
        ):
            if float(config["pk"][key]) < 0:
                raise ValueError(f"pk.{key} 不能小于 0")
        if float(config["pk"]["wait_seconds"]) < 8:
            raise ValueError("pk.wait_seconds 不能小于 8 秒")
        visit_hours, visit_minutes = map(
            int, str(config["friend_visits"]["start_time"]).split(":")
        )
        if not (0 <= visit_hours <= 23 and 0 <= visit_minutes <= 59):
            raise ValueError("friend_visits.start_time 必须是 HH:MM")
        if int(config["friend_visits"]["max_per_day"]) < 0:
            raise ValueError("friend_visits.max_per_day 不能小于 0")
        minimum = float(config["friend_visits"]["interval_min_seconds"])
        maximum = float(config["friend_visits"]["interval_max_seconds"])
        if minimum < 1 or maximum < minimum:
            raise ValueError("好友访问间隔必须至少 1 秒，且最大值不能小于最小值")
        for section, key in (
            ("scheduler", "interval_seconds"),
            ("care", "hunger_threshold"),
            ("care", "clean_threshold"),
            ("care", "food_purchase_count"),
            ("care", "soap_purchase_count"),
            ("care", "verify_delay_seconds"),
            ("care", "failure_cooldown_seconds"),
            ("story", "settle_retry_seconds"),
        ):
            if float(config[section][key]) < 0:
                raise ValueError(f"{section}.{key} 不能小于 0")
        if int(config["care"]["food_purchase_count"]) <= 0:
            raise ValueError("care.food_purchase_count 必须大于 0")
        if int(config["care"]["soap_purchase_count"]) <= 0:
            raise ValueError("care.soap_purchase_count 必须大于 0")

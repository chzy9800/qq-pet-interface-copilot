from __future__ import annotations

import copy
import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .client import PKOpponent, QQFriend


VISIT_STATUSES = ("success", "no_pet", "already_visited", "failed")


def parse_uin_list(value: str | Iterable[str]) -> set[str]:
    if isinstance(value, str):
        values = value.replace("，", ",").replace("\n", ",").split(",")
    else:
        values = value
    return {str(item).strip() for item in values if str(item).strip()}


def eligible_friends(
    friends: Iterable[QQFriend],
    own_uin: str,
    whitelist: str | Iterable[str] = "",
    exclude: str | Iterable[str] = "",
) -> tuple[QQFriend, ...]:
    allowed = parse_uin_list(whitelist)
    blocked = parse_uin_list(exclude)
    result = []
    seen = set()
    for friend in friends:
        uin = friend.user_id
        if not uin or uin == str(own_uin) or uin in blocked or uin in seen:
            continue
        if allowed and uin not in allowed:
            continue
        seen.add(uin)
        result.append(friend)
    return tuple(result)


def current_pet_friends(
    friends: Iterable[QQFriend],
    live_pets: Iterable[PKOpponent],
    captured_pets: Iterable[PKOpponent] = (),
) -> dict[str, PKOpponent]:
    """Merge pet IDs only for UINs that still exist in the current QQ list."""
    current_uins = {friend.user_id for friend in friends if friend.user_id}
    merged = {
        pet.user_id: pet
        for pet in captured_pets
        if pet.user_id in current_uins and pet.pet_id
    }
    merged.update(
        {
            pet.user_id: pet
            for pet in live_pets
            if pet.user_id in current_uins and pet.pet_id
        }
    )
    return merged


class FriendVisitProgress:
    """One private, resumable progress file per local calendar day."""

    def __init__(self, directory: str | Path, today: str | None = None) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._date = today or date.today().isoformat()
        self.path = self.directory / f"friend-visits-{self._date}.json"
        self._state = self._load()

    def _empty(self) -> dict[str, Any]:
        return {
            "date": self._date,
            "scan": {"total": 0, "eligible": 0, "at": ""},
            "friends": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            broken = self.path.with_suffix(
                f".broken-{datetime.now():%Y%m%d-%H%M%S}.json"
            )
            self.path.replace(broken)
            return self._empty()
        loaded.setdefault("date", self._date)
        loaded.setdefault("scan", {"total": 0, "eligible": 0, "at": ""})
        loaded.setdefault("friends", {})
        return loaded

    def _save(self) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.path)

    def record_scan(self, total: int, eligible: int) -> None:
        with self._lock:
            self._state["scan"] = {
                "total": int(total),
                "eligible": int(eligible),
                "at": datetime.now().astimezone().isoformat(),
            }
            self._save()

    def scanned(self) -> bool:
        return bool(self.snapshot().get("scan", {}).get("at"))

    def mark(
        self,
        user_id: str,
        status: str,
        *,
        pet_id: str = "",
        detail: str = "",
        poked: bool = False,
    ) -> None:
        if status not in VISIT_STATUSES:
            raise ValueError(f"未知好友访问状态：{status}")
        with self._lock:
            self._state["friends"][str(user_id)] = {
                "status": status,
                "pet_id": pet_id,
                "detail": detail,
                "poked": bool(poked),
                "updated_at": datetime.now().astimezone().isoformat(),
            }
            self._save()

    def completed(self, user_id: str) -> bool:
        record = self.snapshot()["friends"].get(str(user_id), {})
        return record.get("status") in {"success", "no_pet", "already_visited"}

    def attempted(self, user_id: str) -> bool:
        return str(user_id) in self.snapshot()["friends"]

    def summary(self) -> dict[str, int]:
        records = self.snapshot()["friends"].values()
        counts = {status: 0 for status in VISIT_STATUSES}
        for record in records:
            status = record.get("status")
            if status in counts:
                counts[status] += 1
        return counts

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

from __future__ import annotations

import copy
import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any


EMPTY_COUNTS = {
    "school": 0,
    "work": 0,
    "adventure": 0,
    "employed": 0,
    "feed": 0,
    "wash": 0,
    "pk": 0,
    "friend_feed": 0,
    "friend_wash": 0,
}


class DailyProgress:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._state = self._load()
        self.rollover()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "date": date.today().isoformat(),
                "counts": dict(EMPTY_COUNTS),
                "history": [],
                "pending": None,
                "care_blocks": {},
                "settled_story_ids": [],
                "encouraged_story_ids": [],
                "optimizer": {"active_minutes": 0, "opening_gold": None},
                "economy_profile": {},
                "work_hire_unavailable_uins": [],
                "rotation": {
                    "last_school_attribute": None,
                    "last_work_job_sub_event": None,
                },
            }
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            broken = self.path.with_suffix(f".broken-{datetime.now():%Y%m%d-%H%M%S}.json")
            self.path.replace(broken)
            return {
                "date": date.today().isoformat(),
                "counts": dict(EMPTY_COUNTS),
                "history": [],
                "pending": None,
                "care_blocks": {},
                "settled_story_ids": [],
                "encouraged_story_ids": [],
                "optimizer": {"active_minutes": 0, "opening_gold": None},
                "economy_profile": {},
                "work_hire_unavailable_uins": [],
                "rotation": {
                    "last_school_attribute": None,
                    "last_work_job_sub_event": None,
                },
            }
        loaded.setdefault("history", [])
        loaded.setdefault("pending", None)
        loaded.setdefault("care_blocks", {})
        loaded.setdefault("settled_story_ids", [])
        loaded.setdefault("encouraged_story_ids", [])
        loaded.setdefault("optimizer", {"active_minutes": 0, "opening_gold": None})
        loaded.setdefault("economy_profile", {})
        loaded.setdefault("work_hire_unavailable_uins", [])
        loaded.setdefault("rotation", {"last_school_attribute": None})
        rotation = loaded.get("rotation")
        if not isinstance(rotation, dict):
            loaded["rotation"] = {"last_school_attribute": None}
        loaded["rotation"].setdefault("last_school_attribute", None)
        loaded["rotation"].setdefault("last_work_job_sub_event", None)
        loaded["counts"] = {**EMPTY_COUNTS, **loaded.get("counts", {})}
        return loaded

    def _save(self) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)

    def rollover(self, today: str | None = None) -> bool:
        with self._lock:
            today = today or date.today().isoformat()
            if self._state.get("date") == today:
                return False
            old_date = self._state.get("date")
            if old_date:
                self._state["history"].append(
                    {
                        "date": old_date,
                        "counts": copy.deepcopy(self._state.get("counts", {})),
                        "pending": copy.deepcopy(self._state.get("pending")),
                    }
                )
            self._state.update(
                {
                    "date": today,
                    "counts": dict(EMPTY_COUNTS),
                    "pending": None,
                    "care_blocks": {},
                    "settled_story_ids": [],
                    "encouraged_story_ids": [],
                    "optimizer": {"active_minutes": 0, "opening_gold": None},
                    "work_hire_unavailable_uins": [],
                    "rotation": {
                        "last_school_attribute": None,
                        "last_work_job_sub_event": None,
                    },
                }
            )
            self._save()
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self.rollover()
            return copy.deepcopy(self._state)

    def count(self, kind: str) -> int:
        return int(self.snapshot()["counts"].get(kind, 0))

    def increment(self, kind: str, amount: int = 1) -> int:
        with self._lock:
            self.rollover()
            self._state["counts"][kind] = int(self._state["counts"].get(kind, 0)) + amount
            self._save()
            return self._state["counts"][kind]

    def optimizer_state(self, current_gold: float) -> dict[str, Any]:
        """Return today's persisted optimization inputs."""
        with self._lock:
            self.rollover()
            optimizer = self._state.setdefault(
                "optimizer", {"active_minutes": 0, "opening_gold": None}
            )
            if optimizer.get("opening_gold") is None:
                optimizer["opening_gold"] = float(current_gold)
                self._save()
            return copy.deepcopy(optimizer)

    def economy_profile(self) -> dict[str, Any]:
        """Return cross-day economics learned from verified server changes."""
        with self._lock:
            return copy.deepcopy(self._state.setdefault("economy_profile", {}))

    def record_supply_observation(
        self, kind: str, *, price: float | None = None, restore: float | None = None
    ) -> None:
        if kind not in {"food", "bath"}:
            return
        with self._lock:
            profile = self._state.setdefault("economy_profile", {}).setdefault(kind, {})
            if price is not None and price > 0:
                profile["price"] = float(price)
            if restore is not None and restore > 0:
                profile["restore"] = float(restore)
            profile["verified_at"] = datetime.now().astimezone().isoformat()
            self._save()

    def record_activity_minutes(self, kind: str, minutes: int) -> int:
        if kind not in {"school", "work"} or minutes <= 0:
            return int(self.snapshot().get("optimizer", {}).get("active_minutes", 0))
        with self._lock:
            self.rollover()
            optimizer = self._state.setdefault(
                "optimizer", {"active_minutes": 0, "opening_gold": None}
            )
            optimizer["active_minutes"] = int(optimizer.get("active_minutes", 0)) + int(minutes)
            self._save()
            return int(optimizer["active_minutes"])

    def work_hire_unavailable_uins(self) -> set[str]:
        return {
            str(value)
            for value in self.snapshot().get("work_hire_unavailable_uins", [])
            if str(value)
        }

    def last_school_attribute(self) -> str | None:
        value = self.snapshot().get("rotation", {}).get("last_school_attribute")
        return str(value) if value else None

    def record_last_school_attribute(self, attribute: str) -> None:
        if not attribute:
            return
        with self._lock:
            self.rollover()
            rotation = self._state.setdefault(
                "rotation", {"last_school_attribute": None}
            )
            rotation["last_school_attribute"] = str(attribute)
            self._save()

    def last_work_job_sub_event(self) -> int:
        value = self.snapshot().get("rotation", {}).get("last_work_job_sub_event")
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def record_last_work_job_sub_event(self, sub_event: int) -> None:
        value = int(sub_event or 0)
        if value <= 0:
            return
        with self._lock:
            self.rollover()
            rotation = self._state.setdefault(
                "rotation", {"last_school_attribute": None}
            )
            rotation["last_work_job_sub_event"] = value
            self._save()

    def mark_work_hire_unavailable(self, uin: str) -> None:
        value = str(uin).strip()
        if not value:
            return
        with self._lock:
            self.rollover()
            blocked = self._state.setdefault("work_hire_unavailable_uins", [])
            if value not in blocked:
                blocked.append(value)
                self._save()

    def set_pending(
        self,
        kind: str,
        confirmed: bool = False,
        story_id: str = "",
        **details: Any,
    ) -> None:
        with self._lock:
            self._state["pending"] = {
                "kind": kind,
                "created_at": datetime.now().astimezone().isoformat(),
                "confirmed": confirmed,
                "story_id": story_id,
                **details,
            }
            self._save()

    def confirm_pending(self, story_id: str) -> None:
        with self._lock:
            pending = self._state.get("pending")
            if pending:
                pending["confirmed"] = True
                pending["story_id"] = story_id
                self._save()

    def clear_pending(self) -> dict[str, Any] | None:
        with self._lock:
            pending = self._state.get("pending")
            self._state["pending"] = None
            self._save()
            return copy.deepcopy(pending)

    def story_was_settled(self, story_id: str) -> bool:
        return story_id in self.snapshot().get("settled_story_ids", [])

    def mark_story_settled(self, story_id: str) -> None:
        with self._lock:
            self.rollover()
            settled = self._state.setdefault("settled_story_ids", [])
            if story_id not in settled:
                settled.append(story_id)
                # Only today's recent IDs are needed to suppress stale status.
                del settled[:-100]
                self._save()

    def story_was_encouraged(self, story_id: str) -> bool:
        return story_id in self.snapshot().get("encouraged_story_ids", [])

    def mark_story_encouraged(self, story_id: str) -> None:
        with self._lock:
            self.rollover()
            encouraged = self._state.setdefault("encouraged_story_ids", [])
            if story_id not in encouraged:
                encouraged.append(story_id)
                del encouraged[:-100]
                self._save()

    def set_care_block(self, kind: str, reason: str, seconds: float) -> None:
        with self._lock:
            self.rollover()
            until = datetime.now().astimezone().timestamp() + max(0.0, seconds)
            self._state["care_blocks"][kind] = {"reason": reason, "until": until}
            self._save()

    def active_care_block(self, kind: str) -> dict[str, Any] | None:
        with self._lock:
            self.rollover()
            block = self._state.get("care_blocks", {}).get(kind)
            if not block:
                return None
            if float(block.get("until", 0)) <= datetime.now().astimezone().timestamp():
                self._state["care_blocks"].pop(kind, None)
                self._save()
                return None
            return copy.deepcopy(block)

    def clear_care_block(self, kind: str) -> None:
        with self._lock:
            if self._state.get("care_blocks", {}).pop(kind, None) is not None:
                self._save()

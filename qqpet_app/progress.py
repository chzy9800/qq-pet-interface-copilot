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
            }
        loaded.setdefault("history", [])
        loaded.setdefault("pending", None)
        loaded.setdefault("care_blocks", {})
        loaded.setdefault("settled_story_ids", [])
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

    def set_pending(self, kind: str, confirmed: bool = False, story_id: str = "") -> None:
        with self._lock:
            self._state["pending"] = {
                "kind": kind,
                "created_at": datetime.now().astimezone().isoformat(),
                "confirmed": confirmed,
                "story_id": story_id,
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

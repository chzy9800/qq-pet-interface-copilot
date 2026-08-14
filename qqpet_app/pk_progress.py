from __future__ import annotations

import copy
import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .client import PKResult


class PKProgress:
    """One append-style JSON record per day for resumable automatic PK."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self.directory / f"pk-{date.today().isoformat()}.json"

    def _load(self) -> dict[str, Any]:
        path = self.path
        if not path.exists():
            return {
                "date": date.today().isoformat(),
                "success": 0,
                "failed": 0,
                "gold_earned": 0.0,
                "records": [],
                "retry_after": 0.0,
                "daily_run_started": False,
                "daily_run_started_at": "",
                "daily_run_completed": False,
                "daily_run_completed_at": "",
                "daily_run_reason": "",
            }
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            broken = path.with_suffix(f".broken-{datetime.now():%H%M%S}.json")
            path.replace(broken)
            return self._load()
        loaded.setdefault("success", 0)
        loaded.setdefault("failed", 0)
        loaded.setdefault("gold_earned", 0.0)
        loaded.setdefault("records", [])
        loaded.setdefault("retry_after", 0.0)
        loaded.setdefault("daily_run_started", False)
        loaded.setdefault("daily_run_started_at", "")
        loaded.setdefault("daily_run_completed", False)
        loaded.setdefault("daily_run_completed_at", "")
        loaded.setdefault("daily_run_reason", "")
        return loaded

    def _save(self, state: dict[str, Any]) -> None:
        path = self.path
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._load())

    def succeeded(self) -> int:
        return int(self.snapshot()["success"])

    def daily_run_completed(self) -> bool:
        return bool(self.snapshot().get("daily_run_completed", False))

    def daily_run_started(self) -> bool:
        return bool(self.snapshot().get("daily_run_started", False))

    def mark_daily_run_started(self) -> None:
        with self._lock:
            state = self._load()
            if state.get("daily_run_started"):
                return
            state["daily_run_started"] = True
            state["daily_run_started_at"] = datetime.now().astimezone().isoformat()
            self._save(state)

    def mark_daily_run_completed(self, reason: str) -> None:
        with self._lock:
            state = self._load()
            state["daily_run_completed"] = True
            state["daily_run_completed_at"] = datetime.now().astimezone().isoformat()
            state["daily_run_reason"] = str(reason)
            self._save(state)

    def retry_blocked(self) -> bool:
        return float(self.snapshot().get("retry_after", 0)) > datetime.now().timestamp()

    def opponent_retry_blocked(self, opponent_uin: str, opponent_pet_id: str) -> bool:
        now = datetime.now().timestamp()
        for record in reversed(self.snapshot().get("records", [])):
            if (
                record.get("status") == "failed"
                and record.get("opponent_uin") == opponent_uin
                and record.get("opponent_pet_id") == opponent_pet_id
            ):
                return float(record.get("retry_until", 0)) > now
        return False

    def opponent_success_counts(self) -> dict[tuple[str, str], int]:
        counts: dict[tuple[str, str], int] = {}
        for record in self.snapshot().get("records", []):
            if record.get("status") != "success":
                continue
            key = (
                str(record.get("opponent_uin") or ""),
                str(record.get("opponent_pet_id") or ""),
            )
            counts[key] = counts.get(key, 0) + 1
        return counts

    def record_success(
        self,
        result: PKResult,
        opponent_name: str = "",
        self_power: int = 0,
        opponent_power: int = 0,
    ) -> None:
        with self._lock:
            state = self._load()
            state["success"] = int(state["success"]) + 1
            state["gold_earned"] = float(state["gold_earned"]) + result.gold_delta
            state["retry_after"] = 0.0
            state["records"].append(
                {
                    "time": datetime.now().astimezone().isoformat(),
                    "status": "success",
                    "opponent_uin": result.opponent_uin,
                    "opponent_pet_id": result.opponent_pet_id,
                    "opponent_name": opponent_name,
                    "story_id": result.story_id,
                    "self_power": self_power,
                    "opponent_power": opponent_power,
                    "gold_delta": result.gold_delta,
                    "mood_delta": result.mood_delta,
                    "hunger_cost": result.hunger_cost,
                    "clean_cost": result.clean_cost,
                    "settlement_bytes": len(result.settlement.body),
                    "verified": result.verified,
                }
            )
            self._save(state)

    def record_failure(
        self,
        opponent_uin: str,
        opponent_pet_id: str,
        reason: str,
        cooldown_seconds: float,
        block_all: bool = True,
    ) -> None:
        with self._lock:
            state = self._load()
            state["failed"] = int(state["failed"]) + 1
            retry_until = datetime.now().timestamp() + max(
                0.0, float(cooldown_seconds)
            )
            state["retry_after"] = retry_until if block_all else 0.0
            state["records"].append(
                {
                    "time": datetime.now().astimezone().isoformat(),
                    "status": "failed",
                    "opponent_uin": opponent_uin,
                    "opponent_pet_id": opponent_pet_id,
                    "reason": reason,
                    "retry_until": retry_until,
                }
            )
            self._save(state)

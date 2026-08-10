from __future__ import annotations

import json
import re
from pathlib import Path

from .client import PKOpponent


CAPTURE_NAME = re.compile(r"^friend-pets-\d{4}-\d{2}-\d{2}\.json$")


def load_latest_friend_pet_capture(directory: Path) -> tuple[PKOpponent, ...]:
    """Load verified pet-owning friends from the newest mobile capture."""
    files = sorted(
        (path for path in directory.glob("friend-pets-*.json") if CAPTURE_NAME.match(path.name)),
        # ISO dates in the verified capture filename sort chronologically and
        # remain deterministic even when files share the same modification time.
        key=lambda path: path.name,
        reverse=True,
    )
    if not files:
        return ()
    try:
        payload = json.loads(files[0].read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ()
    opponents: list[PKOpponent] = []
    seen: set[str] = set()
    for row in payload.get("friends", []):
        if not isinstance(row, dict) or not row.get("has_pet"):
            continue
        uin = str(row.get("uin", "")).strip()
        pet_id = str(row.get("pet_id", "")).strip()
        if not uin.isdigit() or not pet_id or uin in seen:
            continue
        seen.add(uin)
        opponents.append(
            PKOpponent(
                user_id=uin,
                pet_id=pet_id,
                nickname=str(row.get("remark") or row.get("nickname") or ""),
                pet_name=str(row.get("pet_name") or ""),
                power=int(row.get("power") or 0),
            )
        )
    return tuple(opponents)

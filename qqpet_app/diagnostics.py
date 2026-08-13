"""Privacy-preserving diagnostics for user-submitted bug reports."""

from __future__ import annotations

import json
import platform
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .mobile_protocol import MobileProtocolReader


_DIAGNOSTIC_MARKERS = (
    "接口错误", "返回错误", "空响应", "超时", "失败", "异常", "崩溃", "闪退",
    "连接", "重连", "登录会话", "调度器已", "更新",
)
_SECRET_KEY_PARTS = (
    "password", "passwd", "token", "secret", "cookie", "authorization",
    "sendkey", "device_key", "webhook",
)
_IDENTITY_KEYS = {
    "uin", "pet_id", "petid", "user_id", "friend_uin", "opponent_uin",
    "hire_friend_uin", "hire_friend_pet_id", "smtp_user", "smtp_from", "smtp_to",
}


def _is_private_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _IDENTITY_KEYS or any(part in lowered for part in _SECRET_KEY_PARTS)


def _private_values(value: Any, key: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            child_name = str(child_key)
            if _is_private_key(child_name):
                if isinstance(child_value, (str, int)) and len(str(child_value).strip()) >= 4:
                    found.add(str(child_value).strip())
            else:
                found.update(_private_values(child_value, child_name))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_private_values(child, key))
    return found


def sanitize_text(text: str, config: Mapping[str, Any] | None = None) -> str:
    """Remove identifiers and credentials while preserving useful errors."""
    cleaned = str(text)
    for value in sorted(_private_values(config or {}), key=len, reverse=True):
        cleaned = cleaned.replace(value, "<已脱敏>")
    cleaned = re.sub(
        r"(?i)(QQ(?:号|账号)?\s*[:：]?\s*)\d{5,12}", r"\1<已脱敏>", cleaned
    )
    cleaned = re.sub(
        r"(?i)((?:pet_?id|uin|user_?id)\s*[=:：]\s*[\"']?)[A-Za-z0-9_-]+",
        r"\1<已脱敏>", cleaned,
    )
    cleaned = re.sub(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "<邮箱已脱敏>", cleaned,
    )
    cleaned = re.sub(
        r"(?i)\bC:\\Users\\[^\\\s]+", r"C:\\Users\\<已脱敏>", cleaned
    )
    return cleaned


def _settings_summary(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return an allow-listed configuration summary, never the raw config."""
    def section(name: str, keys: tuple[str, ...]) -> dict[str, Any]:
        source = config.get(name, {})
        if not isinstance(source, Mapping):
            return {}
        result: dict[str, Any] = {}
        for key in keys:
            value = source.get(key)
            if isinstance(value, (bool, int, float)):
                result[key] = value
            elif key in {"mode", "strategy", "attribute", "hire_mode"} and isinstance(value, str):
                result[key] = value[:40]
        return result

    return {
        "mobile_protocol": section(
            "mobile_protocol", ("enabled", "auto_reconnect", "reconnect_initial_seconds")
        ),
        "scheduler": section("scheduler", ("interval_seconds",)),
        "optimization": section(
            "optimization",
            ("enabled", "daily_active_minutes", "safety_floor", "preserve_opening_gold"),
        ),
        "safety": section("safety", ("safe_mode", "allow_real_actions")),
        "school": section("school", ("enabled", "attribute", "strategy")),
        "work": section("work", ("enabled", "strategy", "employ_friend", "hire_mode")),
        "adventure": section("adventure", ("enabled", "times_per_day")),
        "pk": section("pk", ("enabled", "max_per_day")),
        "friend_visits": section("friend_visits", ("enabled", "max_friends", "poke")),
        "friend_care": section("friend_care", ("enabled",)),
        "notifications": section("notifications", ("enabled",)),
    }


def _interface_catalog() -> dict[str, list[dict[str, Any]]]:
    def convert(specs: frozenset[tuple[str, int, int]]) -> list[dict[str, Any]]:
        return [
            {"name": name, "command": command, "sub_command": sub_command}
            for name, command, sub_command in sorted(specs)
        ]
    return {
        "read": convert(MobileProtocolReader.READ_ALLOWLIST),
        "write": convert(MobileProtocolReader.WRITE_ALLOWLIST),
    }


def _diagnostic_lines(
    log_dir: Path,
    config: Mapping[str, Any],
    *,
    max_files: int = 3,
    max_lines: int = 800,
) -> list[str]:
    if not log_dir.exists():
        return []
    files = sorted(
        (path for path in log_dir.glob("*.log") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:max_files]
    selected: list[str] = []
    for path in reversed(files):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if any(marker in line for marker in _DIAGNOSTIC_MARKERS):
                if "好友" in line:
                    selected.append(
                        f"[{path.name}] 好友相关诊断事件（详细内容已因隐私省略）"
                    )
                    continue
                selected.append(f"[{path.name}] {sanitize_text(line, config)}")
    return selected[-max_lines:]


def _error_summary(lines: list[str]) -> list[dict[str, Any]]:
    normalized: Counter[str] = Counter()
    for line in lines:
        if not any(marker in line for marker in ("错误", "失败", "空响应", "超时", "异常")):
            continue
        message = re.sub(r"^\[[^]]+\]\s*", "", line)
        message = re.sub(r"\[\d{2}:\d{2}:\d{2}\]\s*", "", message)
        normalized[message[:300]] += 1
    return [
        {"message": message, "count": count}
        for message, count in normalized.most_common(50)
    ]


def create_diagnostic_bundle(
    project_root: str | Path,
    config: Mapping[str, Any],
    destination: str | Path,
    *,
    log_dir: str | Path | None = None,
) -> Path:
    """Create a ZIP safe enough to inspect before a user chooses to upload it."""
    root = Path(project_root).resolve()
    output = Path(destination).expanduser().resolve()
    if output.suffix.lower() != ".zip":
        output = output.with_suffix(".zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_lines = _diagnostic_lines(
        Path(log_dir).resolve() if log_dir else root / "runs" / "logs", config
    )
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "app_version": __version__,
        "platform": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "packaged_executable": bool(getattr(sys, "frozen", False)),
        "diagnostic_log_lines": len(diagnostic_lines),
        "privacy": "账号、宠物ID、好友资料、凭据、原始封包和完整配置均不收集",
    }
    readme = (
        "QQ 宠物助手脱敏诊断包\n"
        "本文件由用户主动导出，不会自动上传。\n"
        "内容：运行环境、非敏感设置摘要、接口目录、筛选并脱敏的错误日志。\n"
        "不含：QQ 会话、QQ 号、petId、好友列表、Token、密码、Cookie、原始封包。\n"
    )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", readme)
        archive.writestr("report.json", json.dumps(report, ensure_ascii=False, indent=2))
        archive.writestr(
            "settings-summary.json",
            json.dumps(_settings_summary(config), ensure_ascii=False, indent=2),
        )
        archive.writestr(
            "interface-catalog.json",
            json.dumps(_interface_catalog(), ensure_ascii=False, indent=2),
        )
        archive.writestr(
            "interface-errors.json",
            json.dumps(_error_summary(diagnostic_lines), ensure_ascii=False, indent=2),
        )
        archive.writestr(
            "diagnostic.log",
            ("\n".join(diagnostic_lines) + "\n") if diagnostic_lines else "没有筛选到诊断日志。\n",
        )
    return output

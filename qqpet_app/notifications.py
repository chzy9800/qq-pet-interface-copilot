from __future__ import annotations

import base64
import json
import os
import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class DeliveryResult:
    channel: str
    succeeded: bool
    detail: str = ""


def _onepush(provider: str, **kwargs):
    try:
        from onepush import notify
    except ImportError as exc:
        raise RuntimeError("未安装 OnePush，远程推送不可用") from exc
    response = notify(provider, **kwargs)
    if response is None:
        raise RuntimeError("OnePush 未返回响应")
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    return response


def windows_toast(title: str, content: str) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows Toast 仅支持 Windows")
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_content = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    script = f'''[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>{safe_title}</text><text>{safe_content}</text></binding></visual></toast>')
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('QQ宠物助手').Show($toast)'''
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        creationflags=flags,
        timeout=15,
    )
    if completed.returncode != 0:
        raise RuntimeError("Windows Toast 发送失败")


class NotificationManager:
    def __init__(
        self,
        config: dict,
        onepush_sender: Callable[..., object] | None = None,
        toast_sender: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config = config
        self.onepush_sender = onepush_sender or _onepush
        self.toast_sender = toast_sender or windows_toast

    def send(self, title: str, content: str, event: str = "failure") -> tuple[DeliveryResult, ...]:
        cfg = self.config.get("notifications", {})
        if not bool(cfg.get("enabled", False)):
            return ()
        deliveries: list[tuple[str, Callable[[], object]]] = []
        if cfg.get("windows_toast", True):
            deliveries.append(("windows_toast", lambda: self.toast_sender(title, content)))
        bark = cfg.get("bark", {})
        if bark.get("enabled") and bark.get("device_key"):
            deliveries.append(("bark", lambda: self.onepush_sender(
                "bark", key=bark["device_key"], title=title, content=content,
                base_url=bark.get("base_url") or "https://api.day.app", group="QQPetCopilot"
            )))
        pushplus = cfg.get("pushplus", {})
        if pushplus.get("enabled") and pushplus.get("token"):
            deliveries.append(("pushplus", lambda: self.onepush_sender(
                "pushplus", token=pushplus["token"], title=title, content=content,
                topic=pushplus.get("topic") or None
            )))
        serverchan = cfg.get("serverchan", {})
        if serverchan.get("enabled") and serverchan.get("sendkey"):
            deliveries.append(("serverchan", lambda: self.onepush_sender(
                "serverchanturbo", sctkey=serverchan["sendkey"], title=title, content=content
            )))
        smtp = cfg.get("smtp", {})
        if smtp.get("enabled") and smtp.get("host") and smtp.get("user") and smtp.get("password"):
            deliveries.append(("smtp", lambda: self.onepush_sender(
                "smtp", host=smtp["host"], port=int(smtp.get("port") or 0),
                user=smtp["user"], password=smtp["password"], ssl=bool(smtp.get("ssl", True)),
                starttls=bool(smtp.get("starttls", False)), title=title, content=content,
                From=smtp.get("from") or smtp["user"], To=smtp.get("to") or smtp["user"]
            )))
        webhook = cfg.get("webhook", {})
        if webhook.get("enabled") and webhook.get("url"):
            data = {"title": title, "content": content, "event": event, "source": "qq-pet-copilot"}
            deliveries.append(("webhook", lambda: self.onepush_sender(
                "custom", url=webhook["url"], method="post", datatype="json", data=data
            )))
        results: list[DeliveryResult] = []
        for channel, sender in deliveries:
            try:
                sender()
            except Exception as exc:
                results.append(DeliveryResult(channel, False, str(exc)))
            else:
                results.append(DeliveryResult(channel, True, "ok"))
        return tuple(results)

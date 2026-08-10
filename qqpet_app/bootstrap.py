from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


NAPCAT_DESKTOP_RELEASE_API = (
    "https://api.github.com/repos/NapNeko/NapCatQQ-Desktop/releases/latest"
)
NAPCAT_CONFIG_RELATIVE = Path("runtime") / "NapCatQQ" / "config"
DEFAULT_ONEBOT_PORT = 6201


@dataclass(frozen=True)
class OneBotEndpoint:
    url: str
    token: str = ""
    uin_hint: str = ""
    config_path: Path | None = None


@dataclass(frozen=True)
class LoginSession:
    endpoint: OneBotEndpoint
    uin: str
    nickname: str = ""


@dataclass(frozen=True)
class DependencyState:
    napcat_root: Path | None
    qq_path: Path | None
    sessions: tuple[LoginSession, ...]


def _is_loopback(host: str) -> bool:
    return host.lower() in {"127.0.0.1", "localhost", "::1"}


def napcat_roots() -> tuple[Path, ...]:
    candidates: list[Path] = []
    program_data = os.environ.get("PROGRAMDATA")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if program_data:
        candidates.append(Path(program_data) / "NapCatQQ Desktop")
    if local_app_data:
        candidates.extend(
            (
                Path(local_app_data) / "NapCatQQ Desktop",
                Path(local_app_data) / "Programs" / "NapCatQQ Desktop",
            )
        )
    unique: list[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return tuple(unique)


def find_napcat_root() -> Path | None:
    for root in napcat_roots():
        if (root / NAPCAT_CONFIG_RELATIVE).is_dir():
            return root
    return None


def _uin_from_filename(path: Path) -> str:
    match = re.search(r"onebot11_(\d+)\.json$", path.name, re.IGNORECASE)
    return match.group(1) if match else ""


def endpoints_from_config(path: Path) -> tuple[OneBotEndpoint, ...]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return ()
    network = data.get("network") if isinstance(data, dict) else None
    servers = network.get("httpServers") if isinstance(network, dict) else None
    if not isinstance(servers, list):
        return ()
    result: list[OneBotEndpoint] = []
    for server in servers:
        if not isinstance(server, dict) or not server.get("enable", False):
            continue
        host = str(server.get("host") or "127.0.0.1")
        if not _is_loopback(host):
            continue
        try:
            port = int(server.get("port") or 0)
        except (TypeError, ValueError):
            continue
        if not (1 <= port <= 65535):
            continue
        result.append(
            OneBotEndpoint(
                url=f"http://127.0.0.1:{port}",
                token=str(server.get("token") or ""),
                uin_hint=_uin_from_filename(path),
                config_path=path,
            )
        )
    return tuple(result)


def discover_endpoints(
    configured_url: str = "", configured_token: str = ""
) -> tuple[OneBotEndpoint, ...]:
    candidates: list[OneBotEndpoint] = []
    if configured_url.startswith(("http://127.0.0.1", "http://localhost")):
        candidates.append(OneBotEndpoint(configured_url.rstrip("/"), configured_token))
    for root in napcat_roots():
        config_dir = root / NAPCAT_CONFIG_RELATIVE
        if not config_dir.is_dir():
            continue
        for path in sorted(config_dir.glob("onebot11_*.json")):
            candidates.extend(endpoints_from_config(path))
    unique: list[OneBotEndpoint] = []
    keys: set[tuple[str, str]] = set()
    for endpoint in candidates:
        key = (endpoint.url, endpoint.token)
        if key not in keys:
            unique.append(endpoint)
            keys.add(key)
    return tuple(unique)


def onebot_action(
    endpoint: OneBotEndpoint, action: str, params: dict | None = None, timeout: float = 3
) -> dict:
    headers = {"Content-Type": "application/json"}
    if endpoint.token:
        headers["Authorization"] = f"Bearer {endpoint.token}"
    request = urllib.request.Request(
        f"{endpoint.url.rstrip('/')}/{action}",
        data=json.dumps(params or {}).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError("NapCat 返回了无法识别的数据")
    return result


def probe_login(endpoint: OneBotEndpoint, timeout: float = 3) -> LoginSession | None:
    try:
        result = onebot_action(endpoint, "get_login_info", timeout=timeout)
    except (OSError, TimeoutError, TypeError, urllib.error.URLError, ValueError, RuntimeError):
        return None
    if result.get("status") != "ok" or int(result.get("retcode", -1)) != 0:
        return None
    data = result.get("data") or {}
    uin = str(data.get("user_id") or endpoint.uin_hint or "")
    if not uin.isdigit():
        return None
    return LoginSession(endpoint=endpoint, uin=uin, nickname=str(data.get("nickname") or ""))


def active_sessions(
    configured_url: str = "", configured_token: str = ""
) -> tuple[LoginSession, ...]:
    result: list[LoginSession] = []
    seen: set[tuple[str, str]] = set()
    for endpoint in discover_endpoints(configured_url, configured_token):
        session = probe_login(endpoint)
        if session and (session.uin, endpoint.url) not in seen:
            result.append(session)
            seen.add((session.uin, endpoint.url))
    return tuple(result)


def _registry_qq_paths() -> Iterable[Path]:
    if os.name != "nt":
        return ()
    try:
        import winreg
    except ImportError:
        return ()
    roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    keys = (
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ",
    )
    found: list[Path] = []
    for root in roots:
        for key_name in keys:
            try:
                with winreg.OpenKey(root, key_name) as key:
                    value, _ = winreg.QueryValueEx(key, "UninstallString")
            except OSError:
                continue
            raw = str(value).strip().strip('"')
            parent = Path(raw).parent
            found.extend((parent / "QQ.exe", parent.parent / "QQ.exe"))
    return tuple(found)


def find_qq_path() -> Path | None:
    candidates = list(_registry_qq_paths())
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            candidates.extend(
                (
                    Path(base) / "Tencent" / "QQNT" / "QQ.exe",
                    Path(base) / "Tencent" / "QQ" / "QQ.exe",
                )
            )
    for path in candidates:
        if path.is_file():
            return path.resolve()
    return None


def dependency_state(configured_url: str = "", configured_token: str = "") -> DependencyState:
    return DependencyState(
        napcat_root=find_napcat_root(),
        qq_path=find_qq_path(),
        sessions=active_sessions(configured_url, configured_token),
    )


def configure_local_onebot(config_path: Path, port: int = DEFAULT_ONEBOT_PORT) -> OneBotEndpoint:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            pass
    network = data.setdefault("network", {})
    servers = network.setdefault("httpServers", [])
    for server in servers:
        if (
            isinstance(server, dict)
            and server.get("enable", False)
            and _is_loopback(str(server.get("host") or "127.0.0.1"))
        ):
            return OneBotEndpoint(
                f"http://127.0.0.1:{int(server['port'])}",
                str(server.get("token") or ""),
                _uin_from_filename(config_path),
                config_path,
            )
    token = secrets.token_urlsafe(32)
    servers.append(
        {
            "enable": True,
            "name": "QQPetInterfaceCopilot",
            "port": int(port),
            "host": "127.0.0.1",
            "enableCors": False,
            "enableWebsocket": False,
            "messagePostFormat": "array",
            "token": token,
            "debug": False,
        }
    )
    for key in ("httpSseServers", "httpClients", "websocketServers", "websocketClients", "plugins"):
        network.setdefault(key, [])
    config_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return OneBotEndpoint(
        f"http://127.0.0.1:{port}", token, _uin_from_filename(config_path), config_path
    )


def _free_local_port(preferred: int = DEFAULT_ONEBOT_PORT) -> int:
    for port in range(preferred, preferred + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("找不到可用的本机端口")


def start_napcat(uin: str = "", qq_path: Path | None = None) -> subprocess.Popen:
    root = find_napcat_root()
    if not root:
        raise RuntimeError("尚未安装 NapCatQQ Desktop")
    runtime = root / "runtime" / "NapCatQQ"
    boot = runtime / "NapCatWinBootMain.exe"
    hook = runtime / "NapCatWinBootHook.dll"
    qq = qq_path or find_qq_path()
    if not boot.is_file() or not hook.is_file():
        raise RuntimeError("NapCat 运行文件不完整，请重新安装")
    if not qq or not qq.is_file():
        raise RuntimeError("没有找到电脑版 QQ，请先安装 QQ")
    command = [str(boot), str(qq), str(hook)]
    if uin.isdigit():
        command.append(uin)
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(command, cwd=runtime, creationflags=creationflags)


def latest_desktop_installer() -> tuple[str, str, str]:
    request = urllib.request.Request(
        NAPCAT_DESKTOP_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "QQPetInterfaceCopilot"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        release = json.loads(response.read().decode("utf-8"))
    assets = release.get("assets") or []
    msi = next(
        (
            asset
            for asset in assets
            if str(asset.get("name", "")).lower() == "napcatqq-desktop-x64.msi"
        ),
        None,
    )
    sums = next((asset for asset in assets if asset.get("name") == "SHA256SUMS"), None)
    if not msi or not sums:
        raise RuntimeError("官方发布中没有找到 Windows x64 安装包或校验文件")
    return str(msi["browser_download_url"]), str(sums["browser_download_url"]), str(msi["name"])


def download_desktop_installer(target_dir: Path, progress: Callable[[str], None] | None = None) -> Path:
    notify = progress or (lambda _message: None)
    target_dir.mkdir(parents=True, exist_ok=True)
    msi_url, sums_url, name = latest_desktop_installer()
    target = target_dir / name
    notify("正在从 NapCat 官方 GitHub 下载 Windows 安装包……")
    urllib.request.urlretrieve(msi_url, target)
    with urllib.request.urlopen(sums_url, timeout=20) as response:
        sums_text = response.read().decode("utf-8", errors="replace")
    expected = ""
    for line in sums_text.splitlines():
        if name.lower() in line.lower():
            expected = line.split()[0].lower()
            break
    actual = hashlib.sha256(target.read_bytes()).hexdigest().lower()
    if not expected or not secrets.compare_digest(actual, expected):
        target.unlink(missing_ok=True)
        raise RuntimeError("NapCat 安装包校验失败，已删除下载文件")
    notify("官方安装包校验通过。")
    return target


def install_desktop(msi_path: Path) -> subprocess.Popen:
    if os.name != "nt":
        raise RuntimeError("一键安装目前仅支持 Windows")
    return subprocess.Popen(
        ["msiexec.exe", "/i", str(msi_path), "/passive", "/norestart"],
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def wait_for_session(
    configured_url: str = "",
    configured_token: str = "",
    preferred_uin: str = "",
    timeout: float = 120,
    interval: float = 2,
) -> LoginSession | None:
    deadline = time.monotonic() + timeout
    configured_for: set[Path] = set()
    while time.monotonic() < deadline:
        sessions = active_sessions(configured_url, configured_token)
        if preferred_uin:
            match = next((item for item in sessions if item.uin == preferred_uin), None)
            if match:
                return match
        elif sessions:
            return sessions[0]
        # A fresh QR login creates onebot11_<uin>.json before an HTTP server
        # exists. Add one loopback-only server; current NapCat releases reload
        # this file automatically. Existing enabled servers are never replaced.
        root = find_napcat_root()
        if root:
            config_dir = root / NAPCAT_CONFIG_RELATIVE
            paths = sorted(
                config_dir.glob("onebot11_*.json"),
                key=lambda item: item.stat().st_mtime_ns,
                reverse=True,
            )
            for path in paths:
                if path in configured_for or endpoints_from_config(path):
                    continue
                file_uin = _uin_from_filename(path)
                if preferred_uin and file_uin != preferred_uin:
                    continue
                configure_local_onebot(path, _free_local_port())
                configured_for.add(path)
                break
        time.sleep(interval)
    return None


def ensure_onebot_for_uin(uin: str) -> OneBotEndpoint:
    root = find_napcat_root()
    if not root:
        raise RuntimeError("尚未安装 NapCatQQ Desktop")
    config_path = root / NAPCAT_CONFIG_RELATIVE / f"onebot11_{uin}.json"
    return configure_local_onebot(config_path, _free_local_port())

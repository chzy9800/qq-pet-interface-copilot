from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


REPOSITORY = "chzy9800/qq-pet-interface-copilot"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET_NAME = "QQ-Pet-Interface-Copilot-Windows-x64.zip"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    tag: str
    name: str
    release_url: str
    asset_url: str
    digest: str
    size: int


def version_tuple(value: str) -> tuple[int, ...]:
    text = str(value).strip().lstrip("vV").split("-", 1)[0]
    try:
        parts = tuple(int(item) for item in text.split("."))
    except ValueError as exc:
        raise UpdateError(f"无法识别版本号：{value}") from exc
    if not parts or any(item < 0 for item in parts):
        raise UpdateError(f"无法识别版本号：{value}")
    return parts


def is_newer(latest: str, current: str) -> bool:
    left = version_tuple(latest)
    right = version_tuple(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def fetch_latest(timeout: float = 15) -> UpdateInfo:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "QQPetInterfaceCopilot-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise UpdateError(f"无法连接 GitHub 检查更新：{exc}") from exc
    if payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("GitHub 最新版本不是正式版，已拒绝自动安装")
    tag = str(payload.get("tag_name") or "").strip()
    version_tuple(tag)
    asset = next(
        (item for item in payload.get("assets", []) if item.get("name") == ASSET_NAME),
        None,
    )
    if not asset:
        raise UpdateError(f"正式版缺少更新文件：{ASSET_NAME}")
    digest = str(asset.get("digest") or "").lower()
    digest_hex = digest.split(":", 1)[1] if digest.startswith("sha256:") else ""
    if len(digest_hex) != 64 or any(item not in "0123456789abcdef" for item in digest_hex):
        raise UpdateError("正式版未提供有效的 SHA-256，已拒绝自动安装")
    size = int(asset.get("size") or 0)
    if size <= 0 or size > MAX_DOWNLOAD_BYTES:
        raise UpdateError("正式版文件大小异常，已拒绝自动安装")
    asset_url = str(asset.get("browser_download_url") or "")
    expected_prefix = f"https://github.com/{REPOSITORY}/releases/download/"
    if not asset_url.startswith(expected_prefix):
        raise UpdateError("正式版下载地址不属于本项目，已拒绝自动安装")
    return UpdateInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        name=str(payload.get("name") or tag),
        release_url=str(payload.get("html_url") or ""),
        asset_url=asset_url,
        digest=digest_hex,
        size=size,
    )


def download_update(
    info: UpdateInfo,
    destination_root: str | Path,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    root = Path(destination_root) / info.tag
    root.mkdir(parents=True, exist_ok=True)
    target = root / ASSET_NAME
    temporary = target.with_suffix(".zip.part")
    request = urllib.request.Request(
        info.asset_url,
        headers={"User-Agent": "QQPetInterfaceCopilot-Updater"},
    )
    digest = hashlib.sha256()
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                received += len(block)
                if received > MAX_DOWNLOAD_BYTES:
                    raise UpdateError("更新包超过 200 MB，已停止下载")
                digest.update(block)
                output.write(block)
                if progress:
                    progress(received, info.size)
    except UpdateError:
        temporary.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"更新包下载失败：{exc}") from exc
    if received != info.size:
        temporary.unlink(missing_ok=True)
        raise UpdateError(f"更新包大小不一致：应为 {info.size}，实际 {received}")
    if digest.hexdigest().lower() != info.digest.lower():
        temporary.unlink(missing_ok=True)
        raise UpdateError("更新包 SHA-256 校验失败，已删除可疑文件")
    temporary.replace(target)
    return target


def extract_executable(archive: str | Path, destination_root: str | Path) -> Path:
    archive_path = Path(archive)
    destination = Path(destination_root) / "staged"
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "QQ宠物助手.exe"
    try:
        with zipfile.ZipFile(archive_path) as package:
            matches = [item for item in package.infolist() if Path(item.filename).name == output.name]
            if len(matches) != 1 or matches[0].is_dir():
                raise UpdateError("更新包中没有唯一的 QQ宠物助手.exe")
            if matches[0].file_size <= 0 or matches[0].file_size > MAX_DOWNLOAD_BYTES:
                raise UpdateError("更新程序大小异常")
            with package.open(matches[0]) as source, output.open("wb") as target:
                shutil.copyfileobj(source, target)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpdateError(f"更新包无法解压：{exc}") from exc
    return output


def schedule_windows_install(staged_executable: str | Path) -> None:
    if not getattr(sys, "frozen", False):
        raise UpdateError("源码运行模式不会自动覆盖文件，请从 Release 下载新版")
    target = Path(sys.executable).resolve()
    staged = Path(staged_executable).resolve()
    if not staged.is_file() or staged.name != "QQ宠物助手.exe":
        raise UpdateError("待安装程序不存在或文件名不正确")
    helper_dir = Path(tempfile.gettempdir()) / "QQPetInterfaceCopilotUpdater"
    helper_dir.mkdir(parents=True, exist_ok=True)
    script = helper_dir / "install-update.ps1"
    log = helper_dir / "update.log"
    script.write_text(
        """param([int]$ParentPid,[string]$Source,[string]$Target,[string]$Log)\n"
        "$ErrorActionPreference='Stop'\n"
        "try {\n"
        "  Wait-Process -Id $ParentPid -ErrorAction SilentlyContinue\n"
        "  Start-Sleep -Milliseconds 800\n"
        "  $backup=$Target+'.previous.exe'\n"
        "  if (Test-Path -LiteralPath $Target) { Copy-Item -LiteralPath $Target -Destination $backup -Force }\n"
        "  Copy-Item -LiteralPath $Source -Destination $Target -Force\n"
        "  Start-Process -FilePath $Target\n"
        "  Set-Content -LiteralPath $Log -Value '更新成功' -Encoding UTF8\n"
        "} catch {\n"
        "  Set-Content -LiteralPath $Log -Value $_.Exception.ToString() -Encoding UTF8\n"
        "  $backup=$Target+'.previous.exe'\n"
        "  if (Test-Path -LiteralPath $backup) { Copy-Item -LiteralPath $backup -Destination $Target -Force }\n"
        "}\n""",
        encoding="utf-8-sig",
    )
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        raise UpdateError("Windows PowerShell 不可用，无法自动替换程序")
    subprocess.Popen(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-ParentPid",
            str(os.getpid()),
            "-Source",
            str(staged),
            "-Target",
            str(target),
            "-Log",
            str(log),
        ],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

"""Safely summarize and publish substantial, reviewable project updates.

This deliberately excludes machine-specific state.  It never stages config.yaml,
runs/, artifacts/, tools/, references/, or any ignored file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "runs"
REPORT = STATE_DIR / "github-sync-latest.md"
ALLOWED_PREFIXES = ("automation/", "docs/", "qqpet_app/", "tests/", ".github/")
ALLOWED_FILES = {"main.py", "README.md", "LICENSE", "config.example.yaml", "launcher.py", "QQPetInterfaceCopilot.spec"}
EXCLUDED_PREFIXES = ("runs/", "artifacts/", "analysis/", "tools/", "references/", "github-chinese/")


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def allowed(path: str) -> bool:
    return (path in ALLOWED_FILES or path.startswith(ALLOWED_PREFIXES)) and not path.startswith(EXCLUDED_PREFIXES)


def changed_files() -> list[str]:
    raw = git("status", "--porcelain=v1", "--untracked-files=all")
    paths = []
    for row in raw.splitlines():
        path = row[3:]
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if allowed(path):
            paths.append(path)
    return sorted(set(paths))


def change_lines(paths: list[str]) -> int:
    """Count tracked diffs plus new text files so major changes are not missed."""
    total = sum(int(part) for row in git("diff", "--numstat").splitlines() for part in row.split("\t")[:2] if part.isdigit())
    for path in paths:
        if git("ls-files", "--error-unmatch", "--", path, check=False):
            continue
        try:
            total += len((ROOT / path).read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            pass
    return total


def summarize(paths: list[str]) -> str:
    areas = []
    if any(p == "main.py" or p.startswith("qqpet_app/") for p in paths): areas.append("核心宠物功能或界面")
    if any(p.startswith("tests/") or p.startswith(".github/") for p in paths): areas.append("自动测试与兼容性")
    if any(p.startswith("docs/") or p == "README.md" for p in paths): areas.append("使用说明")
    if any(p.startswith("automation/") for p in paths): areas.append("自动发布流程")
    return "、".join(areas) or "项目文件"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", help="commit and push only when the update is substantial")
    parser.add_argument("--min-files", type=int, default=5)
    parser.add_argument("--min-lines", type=int, default=150)
    args = parser.parse_args()

    try:
        git("fetch", "origin", "--prune")
        branch = git("branch", "--show-current")
        paths = changed_files()
        lines = change_lines(paths)
        substantial = len(paths) >= args.min_files or lines >= args.min_lines
        now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        report = ["# PetCatCopilot GitHub 自动巡检报告", "", f"- 时间：{now}", f"- 分支：`{branch}`", f"- 可发布的改动：{len(paths)} 个文件，{lines} 行增删", f"- 判定：{'重大更新，可自动上传' if substantial else '普通更新，仅生成报告'}", "", "## 更新说明", "", f"本次改动涉及：{summarize(paths)}。", "", "## 补丁范围", ""]
        report += [f"- `{p}`" for p in paths] or ["- 没有发现允许自动上传的项目改动。"]
        patch_stat = git("diff", "--stat", "--", *paths) if paths else ""
        if patch_stat:
            report += ["", "## 已跟踪文件补丁统计", "", "```text", patch_stat, "```"]
        patch = git("diff", "--", *paths) if paths else ""
        if patch:
            report += ["", "## 已跟踪文件补丁", "", "```diff", patch, "```"]
        if paths:
            report += ["", "## 审核提示", "", "已排除本机配置、账号信息、运行日志、抓包资料、工具目录和未受版本控制的旁边项目。"]
        STATE_DIR.mkdir(exist_ok=True)
        REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(REPORT)
        if not args.publish or not substantial:
            return 0
        git("add", "--", *paths)
        if not git("diff", "--cached", "--quiet", check=False):
            git("commit", "-m", f"chore: publish substantial update ({len(paths)} files, {lines} lines)")
            git("push", "-u", "origin", branch)
        return 0
    except Exception as exc:
        print(f"Auto GitHub sync failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

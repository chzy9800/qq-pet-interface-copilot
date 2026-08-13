# PetCatCopilot GitHub 自动巡检与发布

`Run-AutoGitHubSync.ps1` 会获取远端状态，并把本机允许发布的变更写为 `runs/github-sync-latest.md` 的中文说明与补丁范围。

达到以下任一条件才会自动提交并推送当前分支：至少 5 个允许发布的文件，或至少 150 行增删。允许范围为核心源码、测试、文档、工作流及本自动化目录；`config.yaml`、运行记录、抓包/工具资料和 `github-chinese` 一律不会上传。

安装每两小时执行一次的任务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\automation\Install-AutoGitHubSync.ps1
```

手动只生成报告：`powershell -File .\automation\Run-AutoGitHubSync.ps1`。

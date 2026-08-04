from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from qqpet_app.config import ConfigStore
from qqpet_app.scheduler import Scheduler


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
PROGRESS_PATH = ROOT / "runs" / "daily_progress.json"
LOG_DIR = ROOT / "runs" / "logs"


SETTING_FIELDS = [
    ("napcat.url", "本机接口地址", str),
    ("napcat.token", "本机接口令牌", str),
    ("account.uin", "QQ 号", str),
    ("account.pet_id", "宠物 ID", str),
    ("scheduler.interval_seconds", "轮询间隔（秒）", int),
    ("scheduler.coin_threshold", "学习金币阈值", float),
    ("school.enabled", "启用学习", bool),
    ("school.attribute", "学习属性 culture/physical/art", str),
    ("work.enabled", "启用打工", bool),
    ("work.attribute", "打工属性 culture/physical/art", str),
    ("work.times_per_day", "每日打工上限（0 不限）", int),
    ("work.prefer_highest_reward", "最高收益偏好（接口字段待确认）", bool),
    ("work.employ_friend", "雇佣好友偏好（接口字段待确认）", bool),
    ("adventure.enabled", "启用冒险", bool),
    ("adventure.option", "冒险类型 encounter/coins/skill/climate", str),
    ("adventure.start_time", "冒险开始时间 HH:MM", str),
    ("adventure.times_per_day", "每日冒险上限", int),
    ("care.enabled", "启用状态照顾", bool),
    ("care.hunger_threshold", "体力喂食阈值", float),
    ("care.clean_threshold", "清洁洗澡阈值", float),
    ("care.auto_buy_supplies", "道具不足时自动用金币购买", bool),
    ("care.food_purchase_count", "每次购买饼干数量", int),
    ("care.soap_purchase_count", "每次购买香皂片数量", int),
    ("care.verify_delay_seconds", "照顾后验证等待（秒）", float),
    ("care.failure_cooldown_seconds", "照顾失败重试间隔（秒）", float),
    ("safety.safe_mode", "安全模式（只读）", bool),
    ("safety.allow_experimental_scene_actions", "允许真实学习/打工/冒险", bool),
]


def deep_get(data: dict, path: str) -> Any:
    value: Any = data
    for key in path.split("."):
        value = value[key]
    return value


def deep_set(data: dict, path: str, value: Any) -> None:
    target = data
    keys = path.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value


class MainWindow(tk.Tk):
    def __init__(self, auto_start: bool = False) -> None:
        super().__init__()
        self.title("QQ 宠物接口助手")
        self.geometry("1120x720")
        self.minsize(920, 620)
        self.config_store = ConfigStore(CONFIG_PATH)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.scheduler: Scheduler | None = None
        self.scheduler_thread: threading.Thread | None = None
        self.setting_vars: dict[str, tuple[tk.Variable, type]] = {}
        self.status_vars = {
            key: tk.StringVar(value="--")
            for key in ("connection", "gold", "food", "mood", "hunger", "clean", "total", "story", "counts")
        }
        self._build_ui()
        self._load_settings()
        self.after(100, self._drain_events)
        if auto_start:
            self.after(250, self._start)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.configure("Value.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 14, "bold"))

        shell = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        shell.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        left = ttk.Frame(shell, padding=18)
        right = ttk.Frame(shell)
        shell.add(left, weight=2)
        shell.add(right, weight=3)

        ttk.Label(left, text="宠物实时状态", style="Title.TLabel").pack(anchor="w", pady=(0, 14))
        self._status_row(left, "接口", "connection")
        self._status_row(left, "金币", "gold")
        self._status_row(left, "食物", "food")
        self._status_row(left, "心情", "mood")
        self._status_row(left, "体力", "hunger")
        self._status_row(left, "清洁", "clean")
        self._status_row(left, "综合", "total")
        self._status_row(left, "当前任务", "story", small=True)
        self._status_row(left, "今日次数", "counts", small=True)

        buttons = ttk.Frame(left)
        buttons.pack(fill=tk.X, pady=(22, 0))
        self.start_button = ttk.Button(buttons, text="开始自动调度", command=self._start)
        self.start_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        self.stop_button = ttk.Button(buttons, text="停止", command=self._stop, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0))
        ttk.Button(left, text="立即检查一轮", command=self._check_once).pack(fill=tk.X, pady=(10, 0))

        note = (
            "接口版不需要 scrcpy、OCR 或手机坐标。\n"
            "食物库存来自服务器：饼干/虾仁；洗澡会核对清洁值。\n"
            "金币兑换的写接口仍须抓到真实命令后才会启用。"
        )
        ttk.Label(left, text=note, foreground="#666", justify=tk.LEFT).pack(anchor="w", pady=(18, 0))

        notebook = ttk.Notebook(right)
        notebook.pack(fill=tk.BOTH, expand=True)
        log_page = ttk.Frame(notebook, padding=10)
        settings_page = ttk.Frame(notebook, padding=10)
        notebook.add(log_page, text="运行日志")
        notebook.add(settings_page, text="设置")

        self.log_text = tk.Text(log_page, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 10))
        log_scroll = ttk.Scrollbar(log_page, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        canvas = tk.Canvas(settings_page, highlightthickness=0)
        scroll = ttk.Scrollbar(settings_page, orient=tk.VERTICAL, command=canvas.yview)
        form = ttk.Frame(canvas)
        form.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=form, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        for row, (path, label, value_type) in enumerate(SETTING_FIELDS):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=5)
            if value_type is bool:
                variable: tk.Variable = tk.BooleanVar()
                ttk.Checkbutton(form, variable=variable).grid(row=row, column=1, sticky="w", padx=6, pady=5)
            else:
                variable = tk.StringVar()
                ttk.Entry(form, textvariable=variable, width=46).grid(row=row, column=1, sticky="ew", padx=6, pady=5)
            self.setting_vars[path] = (variable, value_type)
        form.columnconfigure(1, weight=1)
        ttk.Button(form, text="保存并立即生效", command=self._save_settings).grid(
            row=len(SETTING_FIELDS), column=0, columnspan=2, sticky="ew", padx=6, pady=14
        )

    def _status_row(self, parent: ttk.Frame, title: str, key: str, small: bool = False) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=6)
        ttk.Label(frame, text=title, width=10).pack(side=tk.LEFT)
        style = "Title.TLabel" if small else "Value.TLabel"
        ttk.Label(frame, textvariable=self.status_vars[key], style=style).pack(side=tk.RIGHT)

    def _load_settings(self) -> None:
        config = self.config_store.data
        for path, (variable, _value_type) in self.setting_vars.items():
            variable.set(deep_get(config, path))

    def _save_settings(self) -> None:
        config = self.config_store.data
        try:
            for path, (variable, value_type) in self.setting_vars.items():
                raw = variable.get()
                value = raw if value_type is bool else value_type(raw)
                deep_set(config, path, value)
            self.config_store.save(config)
        except Exception as exc:
            messagebox.showerror("设置无效", str(exc), parent=self)
            return
        self._append_log(f"[{datetime.now():%H:%M:%S}] 设置已保存，下一轮立即生效")

    def _new_scheduler(self) -> Scheduler:
        return Scheduler(
            CONFIG_PATH,
            PROGRESS_PATH,
            log=lambda text: self.events.put(("log", text)),
            status_callback=lambda values, story, state: self.events.put(("status", (values, story, state))),
            activity_callback=lambda text: self.events.put(("activity", text)),
        )

    def _start(self) -> None:
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            return
        self.scheduler = self._new_scheduler()
        self.scheduler_thread = threading.Thread(target=self.scheduler.run_forever, daemon=True)
        self.scheduler_thread.start()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)

    def _stop(self) -> None:
        if self.scheduler:
            self.scheduler.stop()
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)

    def _check_once(self) -> None:
        def worker() -> None:
            scheduler = self._new_scheduler()
            try:
                scheduler.run_once()
            except Exception as exc:
                self.events.put(("log", f"[{datetime.now():%H:%M:%S}] 检查失败：{exc}"))
                self.events.put(("connection", "连接失败"))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    values, story, state = payload
                    self.status_vars["connection"].set("已连接")
                    self.status_vars["gold"].set(f"{values.gold:.2f}")
                    inventory = state.get("food_inventory", {})
                    self.status_vars["food"].set(
                        f"饼干 {inventory.get('biscuits', '--')} / 虾仁 {inventory.get('shrimp', '--')}"
                    )
                    self.status_vars["mood"].set(f"{values.feel:.1f}/100")
                    self.status_vars["hunger"].set(f"{values.hunger:.1f}/100")
                    self.status_vars["clean"].set(f"{values.clean:.1f}/100")
                    self.status_vars["total"].set(f"{values.total:.1f}")
                    counts = state["counts"]
                    self.status_vars["counts"].set(
                        f"学{counts['school']} 工{counts['work']} 冒{counts['adventure']}"
                    )
                elif kind == "connection":
                    self.status_vars["connection"].set(str(payload))
                elif kind == "activity":
                    self.status_vars["story"].set(str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / f"{datetime.now():%Y-%m-%d}.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    def _close(self) -> None:
        self._stop()
        self.destroy()


if __name__ == "__main__":
    MainWindow(auto_start="--autostart" in sys.argv[1:]).mainloop()

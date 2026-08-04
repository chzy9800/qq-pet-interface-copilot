from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from qqpet_app.client import NapCatClient
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
    ("work.times_per_day", "每日打工上限（0 不限）", int),
    ("work.employ_friend", "有可用好友时优先雇佣", bool),
    ("adventure.enabled", "启用冒险", bool),
    ("adventure.start_time", "冒险开始时间 HH:MM", str),
    ("adventure.times_per_day", "每日冒险上限", int),
    ("care.enabled", "启用状态照顾", bool),
    ("care.hunger_threshold", "体力喂食阈值", float),
    ("care.clean_threshold", "清洁洗澡阈值", float),
    ("care.auto_buy_supplies", "道具不足时自动用金币购买", bool),
    ("care.food_purchase_count", "每次购买食物数量", int),
    ("care.soap_purchase_count", "每次购买沐浴球数量", int),
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
        self.course_var = tk.StringVar(value="自动选择当前属性最高收益")
        self.course_options: dict[str, int] = {"自动选择当前属性最高收益": 0}
        self.job_var = tk.StringVar(value="自动选择开放职业中总收益最高岗位")
        self.job_options: dict[str, tuple[int, int]] = {
            "自动选择开放职业中总收益最高岗位": (0, 0)
        }
        self.adventure_var = tk.StringVar(value="自动选择服务器当前可用冒险")
        self.adventure_options: dict[str, str] = {
            "自动选择服务器当前可用冒险": ""
        }
        self.status_vars = {
            key: tk.StringVar(value="--")
            for key in (
                "connection", "gold", "food", "bath", "mood", "hunger",
                "clean", "total", "story", "counts",
            )
        }
        self._build_ui()
        self._load_settings()
        self.after(500, self._refresh_school_courses)
        self.after(700, self._refresh_work_jobs)
        self.after(900, self._refresh_adventure_options)
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
        self._status_row(left, "洗护", "bath")
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
            "食物和洗护库存均来自服务器；洗澡会核对清洁值。\n"
            "学习和打工选项均从服务器实时读取，不使用固定坐标。"
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
        course_row = len(SETTING_FIELDS)
        ttk.Label(form, text="当前阶段课程").grid(
            row=course_row, column=0, sticky="w", padx=6, pady=5
        )
        course_box = ttk.Frame(form)
        course_box.grid(row=course_row, column=1, sticky="ew", padx=6, pady=5)
        self.course_combo = ttk.Combobox(
            course_box,
            textvariable=self.course_var,
            state="readonly",
            width=41,
            values=tuple(self.course_options),
        )
        self.course_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.course_refresh_button = ttk.Button(
            course_box,
            text="刷新",
            command=self._refresh_school_courses,
        )
        self.course_refresh_button.pack(side=tk.LEFT, padx=(6, 0))
        self.course_stage_label = ttk.Label(form, text="将从服务器读取当前学园阶段", foreground="#666")
        self.course_stage_label.grid(
            row=course_row + 1, column=1, sticky="w", padx=6, pady=(0, 5)
        )
        job_row = course_row + 2
        ttk.Label(form, text="开放职业岗位").grid(
            row=job_row, column=0, sticky="w", padx=6, pady=5
        )
        job_box = ttk.Frame(form)
        job_box.grid(row=job_row, column=1, sticky="ew", padx=6, pady=5)
        self.job_combo = ttk.Combobox(
            job_box,
            textvariable=self.job_var,
            state="readonly",
            width=41,
            values=tuple(self.job_options),
        )
        self.job_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.job_refresh_button = ttk.Button(
            job_box,
            text="刷新",
            command=self._refresh_work_jobs,
        )
        self.job_refresh_button.pack(side=tk.LEFT, padx=(6, 0))
        self.job_status_label = ttk.Label(
            form, text="将从服务器读取已开放职业和岗位", foreground="#666"
        )
        self.job_status_label.grid(
            row=job_row + 1, column=1, sticky="w", padx=6, pady=(0, 5)
        )
        adventure_row = job_row + 2
        ttk.Label(form, text="服务器冒险选项").grid(
            row=adventure_row, column=0, sticky="w", padx=6, pady=5
        )
        adventure_box = ttk.Frame(form)
        adventure_box.grid(row=adventure_row, column=1, sticky="ew", padx=6, pady=5)
        self.adventure_combo = ttk.Combobox(
            adventure_box,
            textvariable=self.adventure_var,
            state="readonly",
            width=41,
            values=tuple(self.adventure_options),
        )
        self.adventure_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.adventure_refresh_button = ttk.Button(
            adventure_box,
            text="刷新",
            command=self._refresh_adventure_options,
        )
        self.adventure_refresh_button.pack(side=tk.LEFT, padx=(6, 0))
        self.adventure_status_label = ttk.Label(
            form, text="将从服务器读取真实冒险目录", foreground="#666"
        )
        self.adventure_status_label.grid(
            row=adventure_row + 1, column=1, sticky="w", padx=6, pady=(0, 5)
        )
        form.columnconfigure(1, weight=1)
        ttk.Button(form, text="保存并立即生效", command=self._save_settings).grid(
            row=adventure_row + 2, column=0, columnspan=2, sticky="ew", padx=6, pady=14
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
        selected = int(config["school"].get("course_sub_event", 0))
        if selected:
            label = f"已保存课程编号 {selected}（刷新后显示名称）"
            self.course_options[label] = selected
            self.course_combo.configure(values=tuple(self.course_options))
            self.course_var.set(label)
        else:
            self.course_var.set("自动选择当前属性最高收益")
        career_type = int(config["work"].get("career_type", 0))
        job_sub_event = int(config["work"].get("job_sub_event", 0))
        if job_sub_event:
            label = f"已保存岗位编号 {job_sub_event}（刷新后显示名称）"
            self.job_options[label] = (career_type, job_sub_event)
            self.job_combo.configure(values=tuple(self.job_options))
            self.job_var.set(label)
        else:
            self.job_var.set("自动选择开放职业中总收益最高岗位")
        adventure_name = str(config["adventure"].get("option_name", ""))
        if adventure_name:
            label = f"已保存冒险“{adventure_name}”（刷新后显示详情）"
            self.adventure_options[label] = adventure_name
            self.adventure_combo.configure(values=tuple(self.adventure_options))
            self.adventure_var.set(label)
        else:
            self.adventure_var.set("自动选择服务器当前可用冒险")

    def _refresh_school_courses(self) -> None:
        self.course_refresh_button.configure(state=tk.DISABLED)
        self.course_stage_label.configure(text="正在读取服务器课程目录…")

        def worker() -> None:
            try:
                config = self.config_store.data
                client = NapCatClient(
                    config["napcat"]["url"],
                    config["napcat"]["token"],
                    config["account"]["pet_id"],
                    float(config["napcat"]["timeout_seconds"]),
                )
                stage = client.query_school_stage()
                courses = client.query_school_courses(stage)
                self.events.put(("school_courses", (stage, courses)))
            except Exception as exc:
                self.events.put(("school_courses_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_work_jobs(self) -> None:
        self.job_refresh_button.configure(state=tk.DISABLED)
        self.job_status_label.configure(text="正在读取服务器职业和岗位目录…")

        def worker() -> None:
            try:
                config = self.config_store.data
                client = NapCatClient(
                    config["napcat"]["url"],
                    config["napcat"]["token"],
                    config["account"]["pet_id"],
                    float(config["napcat"]["timeout_seconds"]),
                )
                overview = client.query_work_overview()
                jobs = tuple(
                    job
                    for career in overview.careers
                    if career.available
                    for job in client.query_work_jobs(career.career_type)
                )
                self.events.put(("work_jobs", (overview, jobs)))
            except Exception as exc:
                self.events.put(("work_jobs_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_adventure_options(self) -> None:
        self.adventure_refresh_button.configure(state=tk.DISABLED)
        self.adventure_status_label.configure(text="正在读取服务器冒险目录…")

        def worker() -> None:
            try:
                config = self.config_store.data
                client = NapCatClient(
                    config["napcat"]["url"],
                    config["napcat"]["token"],
                    config["account"]["pet_id"],
                    float(config["napcat"]["timeout_seconds"]),
                )
                options = client.query_adventure_options()
                self.events.put(("adventure_options", options))
            except Exception as exc:
                self.events.put(("adventure_options_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _save_settings(self) -> None:
        config = self.config_store.data
        try:
            for path, (variable, value_type) in self.setting_vars.items():
                raw = variable.get()
                value = raw if value_type is bool else value_type(raw)
                deep_set(config, path, value)
            selected_label = self.course_var.get()
            if selected_label not in self.course_options:
                raise ValueError("请刷新并重新选择课程")
            config["school"]["course_sub_event"] = self.course_options[selected_label]
            selected_job = self.job_var.get()
            if selected_job not in self.job_options:
                raise ValueError("请刷新并重新选择打工岗位")
            career_type, job_sub_event = self.job_options[selected_job]
            config["work"]["career_type"] = career_type
            config["work"]["job_sub_event"] = job_sub_event
            config["work"]["strategy"] = "highest_total"
            selected_adventure = self.adventure_var.get()
            if selected_adventure not in self.adventure_options:
                raise ValueError("请刷新并重新选择冒险")
            config["adventure"]["option_name"] = self.adventure_options[selected_adventure]
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
                        f"饼干 {inventory.get('biscuits', '--')} / "
                        f"虾仁 {inventory.get('shrimp_status', '仅手机端可见')}"
                    )
                    bath_inventory = state.get("bath_inventory", {})
                    self.status_vars["bath"].set(
                        f"香皂片 {bath_inventory.get('soap', '--')} / "
                        f"沐浴球 {bath_inventory.get('bath_ball', '--')}"
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
                elif kind == "school_courses":
                    stage, courses = payload
                    previous = int(self.config_store.data["school"].get("course_sub_event", 0))
                    options = {"自动选择当前属性最高收益": 0}
                    selected_label = "自动选择当前属性最高收益"
                    for course in courses:
                        label = f"{course.name}｜{course.duration}｜{course.reward}"
                        options[label] = course.sub_event_type
                        if course.sub_event_type == previous:
                            selected_label = label
                    self.course_options = options
                    self.course_combo.configure(values=tuple(options))
                    self.course_var.set(selected_label)
                    stage_name = {
                        0: "学前辅导",
                        1: "初级学园",
                        2: "中级学园",
                        3: "高级学园",
                        4: "进修学院",
                    }.get(stage, f"阶段 {stage}")
                    self.course_stage_label.configure(
                        text=f"服务器当前阶段：{stage_name}；共 {len(courses)} 门课程"
                    )
                    self.course_refresh_button.configure(state=tk.NORMAL)
                elif kind == "school_courses_error":
                    self.course_stage_label.configure(text=f"课程读取失败：{payload}")
                    self.course_refresh_button.configure(state=tk.NORMAL)
                elif kind == "work_jobs":
                    overview, jobs = payload
                    config = self.config_store.data
                    previous = int(config["work"].get("job_sub_event", 0))
                    automatic = "自动选择开放职业中总收益最高岗位"
                    options = {automatic: (0, 0)}
                    selected_label = automatic
                    for job in jobs:
                        if not job.can_do:
                            continue
                        label = (
                            f"{job.career_name}｜{job.name}｜"
                            f"{job.duration}｜收益 {job.reward}"
                        )
                        options[label] = (job.career_type, job.sub_event_type)
                        if job.sub_event_type == previous:
                            selected_label = label
                    self.job_options = options
                    self.job_combo.configure(values=tuple(options))
                    self.job_var.set(selected_label)
                    open_count = sum(1 for career in overview.careers if career.available)
                    self.job_status_label.configure(
                        text=f"服务器开放 {open_count} 个职业；共 {len(jobs)} 个岗位"
                    )
                    self.job_refresh_button.configure(state=tk.NORMAL)
                elif kind == "work_jobs_error":
                    self.job_status_label.configure(text=f"岗位读取失败：{payload}")
                    self.job_refresh_button.configure(state=tk.NORMAL)
                elif kind == "adventure_options":
                    previous = str(
                        self.config_store.data["adventure"].get("option_name", "")
                    )
                    automatic = "自动选择服务器当前可用冒险"
                    options = {automatic: ""}
                    selected_label = automatic
                    available_count = 0
                    for option in payload:
                        status = "可执行" if option.can_do else "暂不可用"
                        label = (
                            f"{option.name}｜{option.duration}｜"
                            f"{option.cost}｜{status}"
                        )
                        options[label] = option.name
                        if option.can_do:
                            available_count += 1
                        if option.name == previous:
                            selected_label = label
                    self.adventure_options = options
                    self.adventure_combo.configure(values=tuple(options))
                    self.adventure_var.set(selected_label)
                    self.adventure_status_label.configure(
                        text=f"服务器返回 {len(payload)} 项；当前可执行 {available_count} 项"
                    )
                    self.adventure_refresh_button.configure(state=tk.NORMAL)
                elif kind == "adventure_options_error":
                    self.adventure_status_label.configure(text=f"冒险读取失败：{payload}")
                    self.adventure_refresh_button.configure(state=tk.NORMAL)
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

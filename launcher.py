from __future__ import annotations

import queue
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from qqpet_app.bootstrap import ensure_vc_runtime
from qqpet_app.config import ConfigStore
from qqpet_app.mobile_protocol import reader_from_config
from qqpet_app.scheduler import Scheduler


ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
CONFIG_PATH = ROOT / "config.yaml"
DOWNLOAD_DIR = (
    Path(os.environ.get("LOCALAPPDATA") or ROOT)
    / "QQPetInterfaceCopilot"
    / "downloads"
)
def console_process_spec(frozen: bool | None = None) -> tuple[list[str], dict[str, str]]:
    """Return an independent console command and environment for this build."""
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    command = [sys.executable, str(ROOT / "main.py")]
    child_env = os.environ.copy()
    if is_frozen:
        command = [sys.executable, "--console"]
        # A one-file PyInstaller child must unpack independently. Otherwise it
        # inherits the launcher's temporary _MEI directory, which is deleted
        # when this window exits.
        child_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return command, child_env


def _configured_identity(store: ConfigStore) -> tuple[str, str]:
    config = store.data
    uin = str(config["account"].get("uin") or "")
    pet_id = str(config["account"].get("pet_id") or "")
    if uin == "YOUR_QQ_UIN":
        uin = ""
    if pet_id == "YOUR_PET_ID":
        pet_id = ""
    return uin, pet_id


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("QQ 宠物助手 · 一键启动")
        self.geometry("620x460")
        self.minsize(560, 420)
        self.store = ConfigStore(CONFIG_PATH)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.running = False
        self._build()
        self.after(100, self._drain)
        self.after(350, self.connect)

    def _build(self) -> None:
        body = ttk.Frame(self, padding=24)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="QQ 宠物助手", font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        ttk.Label(
            body,
            text="连接电脑中的安卓模拟器和手机 QQ，成功后直接打开控制台。",
            foreground="#666",
        ).pack(anchor="w", pady=(6, 18))
        self.state_var = tk.StringVar(value="准备检查……")
        ttk.Label(body, textvariable=self.state_var, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        self.progress = ttk.Progressbar(body, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(10, 14))
        self.log = tk.Text(body, height=11, state=tk.DISABLED, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True)
        actions = ttk.Frame(body)
        actions.pack(fill=tk.X, pady=(14, 0))
        self.connect_button = ttk.Button(actions, text="一键安装并打开", command=self.connect)
        self.connect_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.install_button = ttk.Button(actions, text="检查手机协议环境", command=self.install_mobile_runtime)
        self.install_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))

    def _append(self, message: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, message + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _run(self, target) -> None:
        if self.running:
            return
        self.running = True
        self.progress.start(12)
        self.connect_button.configure(state=tk.DISABLED)
        self.install_button.configure(state=tk.DISABLED)
        threading.Thread(target=target, daemon=True).start()

    def connect(self) -> None:
        self._run(self._connect_worker)

    def install_mobile_runtime(self) -> None:
        self._run(self._install_worker)

    def _connect_worker(self) -> None:
        try:
            preferred_uin, pet_id = _configured_identity(self.store)
            self.events.put(("log", "正在检查软件必备运行环境……"))
            ensure_vc_runtime(
                DOWNLOAD_DIR, lambda message: self.events.put(("log", message))
            )
            self.events.put(("log", "正在连接安卓模拟器中的手机 QQ 协议……"))
            config = self.store.data
            config["mobile_protocol"]["enabled"] = True
            reader = reader_from_config(config)
            if reader is None:
                raise RuntimeError("手机协议未启用")
            serial = reader.prepare_runtime(
                DOWNLOAD_DIR, lambda message: self.events.put(("log", message))
            )
            config["mobile_protocol"]["adb_path"] = str(reader.adb_path or "")
            config["mobile_protocol"]["adb_serial"] = serial
            client = Scheduler._make_client(config)
            logged_in_uin = client.check_connection()
            self.events.put(("log", f"已连接模拟器手机 QQ {logged_in_uin}。"))
            if not pet_id or preferred_uin != logged_in_uin:
                self.events.put(("log", "正在从 QQ 宠物服务器一键读取宠物 ID……"))
                profile = client.query_own_pet_profile(logged_in_uin)
                pet_id = profile.pet_id
                client.pet_id = pet_id
                pet_label = f"“{profile.pet_name}”" if profile.pet_name else ""
                self.events.put(("log", f"已读取宠物{pet_label}，宠物 ID 已自动保存。"))
            config["account"]["uin"] = logged_in_uin
            config["account"]["pet_id"] = pet_id
            self.store.save(config)
            values = client.query_values()
            self.events.put(("log", f"手机协议验证成功，当前金币 {values.gold:.0f}。"))
            self.events.put(("launch", None))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _install_worker(self) -> None:
        try:
            ensure_vc_runtime(
                DOWNLOAD_DIR, lambda message: self.events.put(("log", message))
            )
            config = self.store.data
            config["mobile_protocol"]["enabled"] = True
            reader = reader_from_config(config)
            if reader is None:
                raise RuntimeError("手机协议未启用")
            serial = reader.prepare_runtime(
                DOWNLOAD_DIR, lambda message: self.events.put(("log", message))
            )
            config["mobile_protocol"]["adb_path"] = str(reader.adb_path or "")
            config["mobile_protocol"]["adb_serial"] = serial
            self.store.save(config)
            self.events.put(("log", "基础运行环境和 MuMu 手机协议均已准备完成。"))
            self.events.put(("retry", None))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _finish_busy(self) -> None:
        self.running = False
        self.progress.stop()
        self.connect_button.configure(state=tk.NORMAL)
        self.install_button.configure(state=tk.NORMAL)

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.state_var.set(str(payload))
                    self._append(str(payload))
                elif kind == "launch":
                    self.state_var.set("连接成功，正在打开控制台……")
                    self.progress.stop()
                    command, child_env = console_process_spec()
                    subprocess.Popen(command, cwd=ROOT, env=child_env)
                    self.after(300, self.destroy)
                elif kind == "retry":
                    self._finish_busy()
                    self.after(500, self.connect)
                elif kind == "error":
                    self._finish_busy()
                    self.state_var.set("需要处理")
                    self._append(f"失败：{payload}")
                    messagebox.showerror("一键启动未完成", str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain)


if __name__ == "__main__":
    if "--console" in sys.argv:
        from main import MainWindow

        MainWindow().mainloop()
    else:
        Launcher().mainloop()

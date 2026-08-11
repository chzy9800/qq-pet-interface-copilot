from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .client import PetValues, QQFriend, QQPetError
from .proto import (
    field_bytes,
    field_string,
    field_varint,
    first_bytes,
    first_float,
    first_string,
    first_varint,
    parse_message,
)


_READER_CACHE: dict[tuple[str, ...], "MobileProtocolReader"] = {}
_READER_CACHE_LOCK = threading.Lock()


class MobileProtocolUnavailable(QQPetError):
    """The local Android QQ read bridge is unavailable."""


class MobileProtocolReader:
    """Read QQ Pet state through an authenticated Android QQ process.

    The injected JavaScript has a strict read-only command allow-list.  This
    class never sends task-changing requests, so an uncertain response cannot
    duplicate feeding, work, school, adventure or PK operations.
    """

    STATE = ("OidbSvcTrpcTcp.0x95e1_0", 38369, 0)
    DISPLAY = ("OidbSvcTrpcTcp.0x96f2_1", 38642, 1)
    FOOD_INVENTORY = ("OidbSvcTrpcTcp.0x9949_1", 39241, 1)
    BATH_ITEM_CONFIG = ("OidbSvcTrpcTcp.0x9bf1_1", 39921, 1)
    BATH_INVENTORY = ("OidbSvcTrpcTcp.0x9bf2_1", 39922, 1)
    SCENE_OVERVIEW = ("OidbSvcTrpcTcp.0x9b60_1", 39776, 1)
    SCENE_OPTIONS = ("OidbSvcTrpcTcp.0x9ab2_1", 39602, 1)
    STORY_STATUS = ("OidbSvcTrpcTcp.0x975a_1", 38746, 1)
    OWN_PROFILE = ("OidbSvcTrpcTcp.0x99f2_1", 39410, 1)
    PAGE_RULES = ("OidbSvcTrpcTcp.0x96a4_1", 38564, 1)
    FRIEND_PROFILE = ("OidbSvcTrpcTcp.0x976c_0", 38764, 0)
    PK_POWER = ("OidbSvcTrpcTcp.0x9ad4_1", 39636, 1)
    PK_FRIEND_LIST = ("OidbSvcTrpcTcp.0x985d_0", 39005, 0)
    PK_STATUS = ("OidbSvcTrpcTcp.0x975f_1", 38751, 1)
    STORY_START = ("OidbSvcTrpcTcp.0x975e_1", 38750, 1)
    FEED = ("OidbSvcTrpcTcp.0x992d_1", 39213, 1)
    BUY_FOOD = ("OidbSvcTrpcTcp.0x99df_1", 39391, 1)
    DO_BATH = ("OidbSvcTrpcTcp.0x9bf3_1", 39923, 1)
    BUY_BATH_ITEM = ("OidbSvcTrpcTcp.0x9bd0_0", 39888, 0)
    REPORT_EVENT = ("OidbSvcTrpcTcp.0x96a6_1", 38566, 1)
    FRIEND_POKE = ("OidbSvcTrpcTcp.0x985b_0", 39003, 0)
    STORY_SETTLE = ("OidbSvcTrpcTcp.0x9760_1", 38752, 1)
    READ_ALLOWLIST = frozenset(
        (
            STATE,
            DISPLAY,
            FOOD_INVENTORY,
            BATH_ITEM_CONFIG,
            BATH_INVENTORY,
            SCENE_OVERVIEW,
            SCENE_OPTIONS,
            STORY_STATUS,
            OWN_PROFILE,
            PAGE_RULES,
            FRIEND_PROFILE,
            PK_POWER,
            PK_FRIEND_LIST,
            PK_STATUS,
        )
    )
    WRITE_ALLOWLIST = frozenset(
        (
            STORY_START,
            FEED,
            BUY_FOOD,
            DO_BATH,
            BUY_BATH_ITEM,
            REPORT_EVENT,
            FRIEND_POKE,
            STORY_SETTLE,
        )
    )

    def __init__(
        self,
        project_root: str | Path,
        endpoint: str = "127.0.0.1:27042",
        process_name: str = "com.tencent.mobileqq",
        adb_path: str | Path = "",
        adb_serial: str = "127.0.0.1:16416",
    ) -> None:
        self.project_root = Path(project_root)
        self.endpoint = endpoint
        self.process_name = process_name
        self.adb_path = Path(adb_path) if adb_path else None
        self.adb_serial = adb_serial
        self._lock = threading.RLock()
        self._session: Any = None
        self._script: Any = None

    def _load_frida(self):
        try:
            import frida  # type: ignore

            return frida
        except ImportError:
            bundled = self.project_root / "tools" / "py312frida"
            if bundled.is_dir() and str(bundled) not in sys.path:
                sys.path.insert(0, str(bundled))
            try:
                import frida  # type: ignore

                return frida
            except ImportError as exc:
                raise MobileProtocolUnavailable("本机缺少手机协议桥接组件 Frida") from exc

    def _ensure_forward(self) -> None:
        if not self.adb_path or not self.adb_path.is_file():
            return
        try:
            subprocess.run(
                [
                    str(self.adb_path),
                    "-s",
                    self.adb_serial,
                    "forward",
                    "tcp:27042",
                    "tcp:27042",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MobileProtocolUnavailable("无法建立模拟器手机协议通道") from exc

    def _disconnect(self) -> None:
        script, session = self._script, self._session
        self._script = None
        self._session = None
        try:
            if script is not None:
                script.unload()
        except Exception:
            pass
        try:
            if session is not None:
                session.detach()
        except Exception:
            pass

    def _connect(self) -> None:
        if self._script is not None:
            try:
                self._script.exports_sync.ping()
                return
            except Exception:
                self._disconnect()

        self._ensure_forward()
        frida = self._load_frida()
        agent_path = self.project_root / "hooks" / "qqpet_mobile_read_agent.js"
        if not agent_path.is_file():
            raise MobileProtocolUnavailable("手机协议读取脚本不存在")
        try:
            manager = frida.get_device_manager()
            device = manager.add_remote_device(self.endpoint)
            process = next(
                (item for item in device.enumerate_processes() if item.name == self.process_name),
                None,
            )
            if process is None:
                raise MobileProtocolUnavailable("模拟器 QQ 尚未启动或未登录")
            session = device.attach(process.pid)
            agent_source = agent_path.read_text(encoding="utf-8")
            # Frida 17 moved language bridges out of the core runtime.  The
            # command-line REPL prepends this bridge automatically; direct
            # Python sessions have to do it explicitly.
            bridge_path = self.project_root / "tools" / "py312frida" / "frida_tools" / "bridges" / "java.js"
            if bridge_path.is_file():
                agent_source = (
                    bridge_path.read_text(encoding="utf-8")
                    + "\nvar Java = bridge;\n"
                    + agent_source
                )
            script = session.create_script(agent_source)
            script.load()
            script.exports_sync.ping()
            self._session = session
            self._script = script
        except MobileProtocolUnavailable:
            raise
        except Exception as exc:
            self._disconnect()
            raise MobileProtocolUnavailable(f"手机协议连接失败：{exc}") from exc

    def _send_read(self, spec: tuple[str, int, int], body: bytes) -> bytes:
        with self._lock:
            last_error: Exception | None = None
            for _ in range(2):
                try:
                    self._connect()
                    assert self._script is not None
                    result = self._script.exports_sync.send_oidb_read(
                        spec[0], spec[1], spec[2], body.hex()
                    )
                    code = int(result.get("code", -1))
                    raw = bytes.fromhex(str(result.get("data_hex") or ""))
                    if code != 0:
                        detail = str(result.get("message") or "")
                        raise MobileProtocolUnavailable(
                            f"手机 QQ 返回错误 {code}" + (f"：{detail}" if detail else "")
                        )
                    if not raw:
                        raise MobileProtocolUnavailable("手机 QQ 返回空响应")
                    return raw
                except Exception as exc:
                    last_error = exc
                    self._disconnect()
            if isinstance(last_error, MobileProtocolUnavailable):
                raise last_error
            raise MobileProtocolUnavailable(f"手机协议读取失败：{last_error}")

    def supports(self, command_name: str, command: int, sub_command: int) -> bool:
        return (command_name, int(command), int(sub_command)) in self.READ_ALLOWLIST

    def send_oidb_read(
        self, command_name: str, command: int, sub_command: int, body: bytes
    ) -> bytes:
        spec = (command_name, int(command), int(sub_command))
        if spec not in self.READ_ALLOWLIST:
            raise MobileProtocolUnavailable(f"{command_name} 未开放手机只读通道")
        return self._send_read(spec, body)

    def send_oidb_write_once(
        self, command_name: str, command: int, sub_command: int, body: bytes
    ) -> bytes:
        spec = (command_name, int(command), int(sub_command))
        if spec not in self.WRITE_ALLOWLIST:
            raise MobileProtocolUnavailable(f"{command_name} 未开放手机单次写入通道")
        with self._lock:
            try:
                self._connect()
                assert self._script is not None
                result = self._script.exports_sync.send_oidb_write_once(
                    command_name, command, sub_command, body.hex()
                )
            except Exception as exc:
                self._disconnect()
                raise MobileProtocolUnavailable(f"手机协议单次写入失败：{exc}") from exc
            code = int(result.get("code", -1))
            if code != 0:
                detail = str(result.get("message") or "")
                raise MobileProtocolUnavailable(
                    f"手机 QQ 写入返回错误 {code}" + (f"：{detail}" if detail else "")
                )
            return bytes.fromhex(str(result.get("data_hex") or ""))

    def get_self_uin(self) -> str:
        with self._lock:
            try:
                self._connect()
                assert self._script is not None
                uin = str(self._script.exports_sync.get_self_uin() or "").strip()
            except Exception as exc:
                self._disconnect()
                raise MobileProtocolUnavailable(f"无法读取手机 QQ 登录账号：{exc}") from exc
            if not uin.isdigit():
                raise MobileProtocolUnavailable("手机 QQ 尚未取得有效登录账号")
            return uin

    def query_friend_list(self, max_pages: int = 20) -> tuple[QQFriend, ...]:
        cursor = ""
        friends: list[QQFriend] = []
        seen: set[str] = set()
        for _ in range(max(1, int(max_pages))):
            body = field_varint(2, 6)
            if cursor:
                body = field_string(1, cursor) + body
            root = parse_message(self._send_read(self.PK_FRIEND_LIST, body))
            for value in root.get(1, []):
                if value.wire_type != 2:
                    continue
                friend = parse_message(bytes(value.value))
                user_raw = first_bytes(friend, 2)
                if not user_raw:
                    continue
                user = parse_message(user_raw)
                user_id = str(first_varint(user, 1) or "")
                if not user_id or user_id in seen:
                    continue
                seen.add(user_id)
                friends.append(
                    QQFriend(
                        user_id=user_id,
                        nickname=first_string(user, 2),
                    )
                )
            next_cursor = first_string(root, 2)
            if not first_varint(root, 3) or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return tuple(friends)

    @staticmethod
    def _current(display: dict, field: int) -> float:
        return first_float(parse_message(first_bytes(display, field)), 3)

    def query_values(self, pet_id: str) -> PetValues:
        if not pet_id:
            raise MobileProtocolUnavailable("宠物 ID 为空，无法走手机协议读取")

        state_body = self._send_read(self.STATE, field_string(1, pet_id))
        pet = parse_message(first_bytes(parse_message(state_body), 1))
        personal = parse_message(first_bytes(pet, 5))
        # jj5.r.c is encoded as protobuf field 4 in the current mobile build.
        display_raw = first_bytes(personal, 4)
        if not display_raw:
            raise MobileProtocolUnavailable("手机 QQ 状态响应缺少数值字段")
        display = parse_message(display_raw)

        gold_request = field_string(1, pet_id) + field_bytes(2, b"\x06")
        gold_body = self._send_read(self.DISPLAY, gold_request)
        gold_root = parse_message(first_bytes(parse_message(gold_body), 1))
        gold = self._current(gold_root, 5)

        return PetValues(
            feel=self._current(display, 1),
            hunger=self._current(display, 2),
            clean=self._current(display, 3),
            total=self._current(display, 4),
            gold=gold,
        )


def reader_from_config(config: dict, project_root: str | Path | None = None) -> MobileProtocolReader | None:
    settings = config.get("mobile_protocol") or {}
    if not bool(settings.get("enabled", False)):
        return None
    root = Path(project_root or Path(__file__).resolve().parent.parent)
    adb_setting = str(settings.get("adb_path") or "").strip()
    if adb_setting:
        adb_path = Path(adb_setting)
    else:
        candidates = (
            root / "tools" / "platform-tools" / "adb.exe",
            root / "references" / "qq-pet-copilot" / "scrcpy-win64" / "adb.exe",
        )
        adb_path = next((item for item in candidates if item.is_file()), Path())
    key = (
        str(root),
        str(settings.get("endpoint", "127.0.0.1:27042")),
        str(settings.get("process_name", "com.tencent.mobileqq")),
        str(adb_path),
        str(settings.get("adb_serial", "127.0.0.1:16416")),
    )
    with _READER_CACHE_LOCK:
        reader = _READER_CACHE.get(key)
        if reader is None:
            reader = MobileProtocolReader(
                root,
                endpoint=key[1],
                process_name=key[2],
                adb_path=key[3],
                adb_serial=key[4],
            )
            _READER_CACHE[key] = reader
        return reader

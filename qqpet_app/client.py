from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .proto import (
    field_bytes,
    field_string,
    field_varint,
    first_bytes,
    first_float,
    first_string,
    first_varint,
    oidb_request,
    parse_message,
)


class QQPetError(RuntimeError):
    pass


@dataclass(frozen=True)
class OidbResponse:
    command: int
    sub_command: int
    error_code: int
    body: bytes
    raw: bytes


@dataclass(frozen=True)
class PetValues:
    feel: float = 0.0
    hunger: float = 0.0
    clean: float = 0.0
    total: float = 0.0
    gold: float = 0.0


@dataclass(frozen=True)
class FoodInventory:
    """Food counts returned by PetFeed_GetFeedTimesInfo.

    Android QQ 9.3.25's food exchange sheet confirms field 1 is biscuits and
    field 2 is shrimp.  Older code incorrectly labelled these as a remaining
    allowance and a recovery value.
    """

    biscuits: int = 0
    shrimp: int = 0

    @property
    def total(self) -> int:
        return self.biscuits + self.shrimp


@dataclass(frozen=True)
class BathItem:
    item_id: str
    name: str
    gold_price: int
    clean_gain: int
    mood_gain: int
    description: str = ""
    default_count: int = 1
    step: int = 1
    minimum: int = 1
    maximum: int = 99


@dataclass(frozen=True)
class BathInventory:
    counts: tuple[tuple[str, int], ...] = ()

    def count(self, item_id: str) -> int:
        return next((count for key, count in self.counts if key == item_id), 0)

    @property
    def soap(self) -> int:
        return self.count("1")

    @property
    def bath_ball(self) -> int:
        return self.count("2")


@dataclass(frozen=True)
class FoodPurchaseResult:
    balance: int = 0
    gold: int = 0
    bought: int = 0
    cost_gold: int = 0


@dataclass(frozen=True)
class BathPurchaseResult:
    result: int = 0
    order_id: str = ""

    @property
    def succeeded(self) -> bool:
        return self.result == 0 and bool(self.order_id)


@dataclass(frozen=True)
class WashResult:
    clean: int = 0
    mood: int = 0
    remaining: int = 0
    completed: bool = False
    extra_1: int = 0
    extra_2: int = 0


@dataclass(frozen=True)
class StoryStatus:
    story_id: str = ""
    state_code: int = 0
    remaining_seconds: int = 0
    duration_seconds: int = 0
    started_at: int = 0
    recallable: bool = False
    raw_body: bytes = b""

    @property
    def finished(self) -> bool:
        return bool(
            self.story_id
            and self.duration_seconds > 0
            and self.remaining_seconds <= 0
        )

    @property
    def elapsed_seconds(self) -> int:
        return max(0, self.duration_seconds - self.remaining_seconds)


@dataclass(frozen=True)
class PageRules:
    trace: str = ""
    paths: tuple[tuple[int, int, int], ...] = ()
    declared_count: int | None = None

    def allows(self, path: tuple[int, int, int]) -> bool:
        return path in self.paths


Transport = Callable[[str, str], dict]


class NapCatClient:
    DISPLAY = ("OidbSvcTrpcTcp.0x96f2_1", 38642, 1)
    FEED = ("OidbSvcTrpcTcp.0x992d_1", 39213, 1)
    FEED_TIMES = ("OidbSvcTrpcTcp.0x9949_1", 39241, 1)
    BUY_FOOD = ("OidbSvcTrpcTcp.0x99df_1", 39391, 1)
    BATH_ITEM_CONFIG = ("OidbSvcTrpcTcp.0x9bf1_1", 39921, 1)
    BATH_INVENTORY = ("OidbSvcTrpcTcp.0x9bf2_1", 39922, 1)
    DO_BATH = ("OidbSvcTrpcTcp.0x9bf3_1", 39923, 1)
    BUY_BATH_ITEM = ("OidbSvcTrpcTcp.0x9bd0_0", 39888, 0)
    REPORT_EVENT = ("OidbSvcTrpcTcp.0x96a6_1", 38566, 1)
    PAGE_RULES = ("OidbSvcTrpcTcp.0x96a4_1", 38564, 1)
    STORY_STATUS = ("OidbSvcTrpcTcp.0x975a_1", 38746, 1)
    STORY_SETTLE = ("OidbSvcTrpcTcp.0x9760_1", 38752, 1)

    SCENES = {
        "school": {
            "culture": (6000, 6100, 6101),
            "physical": (6000, 6100, 6201),
            "art": (6000, 6100, 6301),
        },
        "work": {
            "culture": (6000, 6400, 6401),
            "physical": (6000, 6400, 6501),
            "art": (6000, 6400, 6601),
        },
        "adventure": {
            "encounter": (6000, 6700, 6701),
            "coins": (6000, 6700, 6711),
            "skill": (6000, 6700, 6721),
            "climate": (6000, 6700, 6731),
        },
    }

    def __init__(
        self,
        base_url: str,
        token: str,
        pet_id: str,
        timeout: float = 15.0,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.pet_id = pet_id
        self.timeout = timeout
        self._transport = transport

    def _http_transport(self, command: str, data_hex: str) -> dict:
        body = json.dumps({"cmd": command, "data": data_hex}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/send_packet",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise QQPetError(f"无法连接本机 NapCat：{exc}") from exc

    def send_oidb(self, command_name: str, command: int, sub_command: int, body: bytes) -> OidbResponse:
        request = oidb_request(command, sub_command, body)
        result = (self._transport or self._http_transport)(command_name, request.hex())
        if result.get("retcode") != 0 or result.get("status") != "ok":
            raise QQPetError(f"NapCat 请求失败：{result}")
        data_hex = result.get("data") or ""
        if not data_hex:
            raise QQPetError(f"{command_name} 返回空响应")
        try:
            raw = bytes.fromhex(data_hex)
            outer = parse_message(raw)
        except (ValueError, TypeError) as exc:
            raise QQPetError(f"{command_name} 响应无法解析") from exc
        error_code = first_varint(outer, 3)
        response_body = first_bytes(outer, 4)
        if error_code != 0:
            raise QQPetError(f"{command_name} OIDB errorCode={error_code}")
        return OidbResponse(command, sub_command, error_code, response_body, raw)

    def query_values(self) -> PetValues:
        # 类型 1~5 均返回完整四项状态；类型 6 单独返回金币。
        common_request = field_string(1, self.pet_id) + field_bytes(2, b"\x01")
        common = self.send_oidb(*self.DISPLAY, common_request).body
        common_root = parse_message(first_bytes(parse_message(common), 1))

        def current(field: int) -> float:
            return first_float(parse_message(first_bytes(common_root, field)), 3)

        gold_request = field_string(1, self.pet_id) + field_bytes(2, b"\x06")
        gold = self.send_oidb(*self.DISPLAY, gold_request).body
        gold_root = parse_message(first_bytes(parse_message(gold), 1))
        gold_value = first_float(parse_message(first_bytes(gold_root, 5)), 3)
        return PetValues(current(1), current(2), current(3), current(4), gold_value)

    def feed(self) -> OidbResponse:
        return self.send_oidb(*self.FEED, field_string(4, self.pet_id))

    def query_food_inventory(self) -> FoodInventory:
        # 手机 QQ 9.3.25 的“兑换食物/背包”页已实测：字段 1=饼干，字段 2=虾仁。
        response = self.send_oidb(*self.FEED_TIMES, b"").body
        root = parse_message(response)
        return FoodInventory(
            biscuits=first_varint(root, 1),
            shrimp=first_varint(root, 2),
        )

    def query_feed_allowance(self) -> FoodInventory:
        """Compatibility wrapper; use query_food_inventory in new code."""
        return self.query_food_inventory()

    def buy_food(self, count: int) -> FoodPurchaseResult:
        """Buy biscuits with gold through PetFeed_BuyFood (0x99df_1)."""
        if count <= 0:
            raise QQPetError("购买饼干数量必须大于 0")
        response = self.send_oidb(*self.BUY_FOOD, field_varint(1, count)).body
        root = parse_message(response)
        return FoodPurchaseResult(
            balance=first_varint(root, 1),
            gold=first_varint(root, 2),
            bought=first_varint(root, 3),
            cost_gold=first_varint(root, 4),
        )

    def query_bath_items(self) -> tuple[BathItem, ...]:
        # itemType=1 requests the complete wash-item shop configuration.
        response = self.send_oidb(*self.BATH_ITEM_CONFIG, field_varint(1, 1)).body
        root = parse_message(response)
        items: list[BathItem] = []
        for value in root.get(1, []):
            if value.wire_type != 2:
                continue
            item = parse_message(bytes(value.value))
            items.append(
                BathItem(
                    item_id=first_string(item, 2),
                    name=first_string(item, 1),
                    gold_price=first_varint(item, 5),
                    clean_gain=first_varint(item, 6),
                    mood_gain=first_varint(item, 12),
                    description=first_string(item, 7),
                    default_count=first_varint(item, 8),
                    step=first_varint(item, 9),
                    minimum=first_varint(item, 10),
                    maximum=first_varint(item, 11),
                )
            )
        return tuple(items)

    def query_bath_inventory(self) -> BathInventory:
        response = self.send_oidb(*self.BATH_INVENTORY, field_varint(1, 1)).body
        root = parse_message(response)
        info_raw = first_bytes(root, 1)
        info = parse_message(info_raw) if info_raw else {}
        counts: list[tuple[str, int]] = []
        for value in info.get(1, []):
            if value.wire_type != 2:
                continue
            item = parse_message(bytes(value.value))
            counts.append((first_string(item, 1), first_varint(item, 2)))
        return BathInventory(tuple(counts))

    def buy_bath_item(self, item_id: str, count: int) -> BathPurchaseResult:
        """Buy soap (1) or bath balls (2) through common-pay 0x9bd0_0."""
        if item_id not in {"1", "2"}:
            raise QQPetError(f"未知洗护道具：{item_id}")
        if count <= 0:
            raise QQPetError("购买洗护道具数量必须大于 0")

        # Android QQ uses platform=1/accessAppId=1001. Mall category 355 and
        # purchase scene 21 are fixed by ai_pet_home 6530's buyBathItem flow.
        user = field_varint(1, 1) + field_varint(2, 1001) + field_string(3, self.pet_id)
        mall_item = (
            field_varint(1, 355)
            + field_varint(2, int(item_id))
            + field_varint(3, count)
        )
        body = (
            field_bytes(1, user)
            + field_varint(2, 1001)
            + field_bytes(3, mall_item)
            + field_varint(4, 21)
        )
        response = self.send_oidb(*self.BUY_BATH_ITEM, body).body
        root = parse_message(response)
        return BathPurchaseResult(
            result=first_varint(root, 1),
            order_id=first_string(root, 2),
        )

    def use_bath_item(self, item_id: str, pet_uin: str = "") -> WashResult:
        if item_id not in {"1", "2"}:
            raise QQPetError(f"未知洗护道具：{item_id}")
        body = (
            field_string(1, self.pet_id)
            + field_string(2, item_id)
            + field_varint(3, 1)
            + field_string(4, pet_uin)
        )
        response = self.send_oidb(*self.DO_BATH, body).body
        root = parse_message(response)
        return WashResult(
            clean=first_varint(root, 1),
            mood=first_varint(root, 2),
            remaining=first_varint(root, 3),
            completed=bool(first_varint(root, 4)),
            extra_1=first_varint(root, 5),
            extra_2=first_varint(root, 6),
        )

    def report_event(self, page: int, event_type: int, sub_event: int, business_ext: bytes = b"") -> OidbResponse:
        path = field_varint(1, page) + field_varint(2, event_type) + field_varint(3, sub_event)
        body = field_string(1, self.pet_id) + field_bytes(3, path) + field_bytes(4, b"")
        if business_ext:
            body += field_bytes(5, business_ext)
        return self.send_oidb(*self.REPORT_EVENT, body)

    def query_page_rules(self, page: int = 6000) -> PageRules:
        # si5.m: trace(1), petId(2), page(3), executeExtInfo(4), from(10), platform(99)
        body = field_string(2, self.pet_id) + field_varint(3, page) + field_bytes(4, b"")
        response = self.send_oidb(*self.PAGE_RULES, body).body
        root = parse_message(response)
        trace = first_string(root, 1)
        declared_count: int | None = None
        if trace:
            try:
                trace_data = json.loads(trace)
                if isinstance(trace_data, dict) and isinstance(trace_data.get("count"), int):
                    declared_count = int(trace_data["count"])
            except json.JSONDecodeError:
                pass

        paths: list[tuple[int, int, int]] = []
        for item in root.get(2, []):
            if item.wire_type != 2:
                continue
            event = parse_message(bytes(item.value))
            path_raw = first_bytes(event, 1)
            if not path_raw:
                continue
            path = parse_message(path_raw)
            paths.append(
                (
                    first_varint(path, 1),
                    first_varint(path, 2),
                    first_varint(path, 3),
                )
            )
        return PageRules(trace=trace, paths=tuple(paths), declared_count=declared_count)

    def wash(self, target_clean: int = 100) -> OidbResponse:
        return self.report_event(5000, 500, 501, field_varint(5, target_clean))

    def scene_path(self, scene: str, option: str) -> tuple[int, int, int]:
        try:
            return self.SCENES[scene][option]
        except KeyError as exc:
            raise QQPetError(f"未知场景选项：{scene}.{option}") from exc

    def start_scene(self, scene: str, option: str, rules: PageRules | None = None) -> OidbResponse:
        page, event_type, sub_event = self.scene_path(scene, option)
        if rules is not None and not rules.allows((page, event_type, sub_event)):
            raise QQPetError(f"服务器当前未开放场景选项：{scene}.{option}")
        return self.report_event(page, event_type, sub_event)

    def query_story(self) -> StoryStatus:
        # PetOutDoorVM queries the same protocol version used by settlement.
        # Without field 100 the server can keep returning a stale completed
        # story even after DoAfterStoryInfo has already produced its result.
        request = field_string(1, self.pet_id) + field_varint(100, 2)
        response = self.send_oidb(*self.STORY_STATUS, request).body
        root = parse_message(response)
        detail_raw = first_bytes(root, 1)
        detail = parse_message(detail_raw) if detail_raw else {}
        return StoryStatus(
            story_id=first_string(root, 2),
            state_code=first_varint(detail, 1),
            remaining_seconds=first_varint(detail, 2),
            duration_seconds=first_varint(detail, 3),
            started_at=first_varint(detail, 4),
            recallable=bool(first_varint(detail, 5)),
            raw_body=response,
        )

    def settle_story(self, story_id: str, outdoor_version: int = 2) -> OidbResponse:
        # QQ 9.3.25's PetOutDoorVM writes OutdoorVersion to protobuf field 100.
        # Omitting it makes the OIDB request look successful while leaving the
        # story untouched, which in turn causes an endless settlement loop.
        body = (
            field_string(1, story_id)
            + field_varint(2, 1000)
            + field_string(3, self.pet_id)
            + field_varint(100, outdoor_version)
        )
        return self.send_oidb(*self.STORY_SETTLE, body)

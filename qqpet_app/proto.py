from __future__ import annotations

import struct
from collections import defaultdict
from dataclasses import dataclass


def varint(value: int) -> bytes:
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while True:
        part = value & 0x7F
        value >>= 7
        out.append(part | (0x80 if value else 0))
        if not value:
            return bytes(out)


def field_varint(number: int, value: int) -> bytes:
    return varint(number << 3) + varint(value)


def field_bytes(number: int, value: bytes) -> bytes:
    return varint((number << 3) | 2) + varint(len(value)) + value


def field_string(number: int, value: str) -> bytes:
    return field_bytes(number, value.encode("utf-8"))


def field_fixed32(number: int, value: float) -> bytes:
    return varint((number << 3) | 5) + struct.pack("<f", value)


@dataclass(frozen=True)
class WireValue:
    wire_type: int
    value: int | bytes


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data):
        part = data[offset]
        offset += 1
        value |= (part & 0x7F) << shift
        if not part & 0x80:
            return value, offset
        shift += 7
        if shift > 70:
            raise ValueError("protobuf varint 太长")
    raise ValueError("protobuf varint 被截断")


def parse_message(data: bytes) -> dict[int, list[WireValue]]:
    result: dict[int, list[WireValue]] = defaultdict(list)
    offset = 0
    while offset < len(data):
        tag, offset = _read_varint(data, offset)
        number, wire_type = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("protobuf 字段号为 0")
        if wire_type == 0:
            value, offset = _read_varint(data, offset)
        elif wire_type == 1:
            if offset + 8 > len(data):
                raise ValueError("fixed64 被截断")
            value = data[offset : offset + 8]
            offset += 8
        elif wire_type == 2:
            size, offset = _read_varint(data, offset)
            if offset + size > len(data):
                raise ValueError("length-delimited 字段被截断")
            value = data[offset : offset + size]
            offset += size
        elif wire_type == 5:
            if offset + 4 > len(data):
                raise ValueError("fixed32 被截断")
            value = data[offset : offset + 4]
            offset += 4
        else:
            raise ValueError(f"暂不支持 protobuf wire type {wire_type}")
        result[number].append(WireValue(wire_type, value))
    return dict(result)


def first_varint(message: dict[int, list[WireValue]], field: int, default: int = 0) -> int:
    values = message.get(field, [])
    return int(values[0].value) if values and values[0].wire_type == 0 else default


def first_bytes(message: dict[int, list[WireValue]], field: int, default: bytes = b"") -> bytes:
    values = message.get(field, [])
    return bytes(values[0].value) if values and values[0].wire_type == 2 else default


def first_string(message: dict[int, list[WireValue]], field: int, default: str = "") -> str:
    raw = first_bytes(message, field)
    return raw.decode("utf-8", errors="replace") if raw else default


def first_float(message: dict[int, list[WireValue]], field: int, default: float = 0.0) -> float:
    values = message.get(field, [])
    if values and values[0].wire_type == 5:
        return float(struct.unpack("<f", bytes(values[0].value))[0])
    return default


def oidb_request(command: int, sub_command: int, body: bytes) -> bytes:
    return (
        field_varint(1, command)
        + field_varint(2, sub_command)
        + field_bytes(4, body)
        + field_varint(12, 1)
    )

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MachOError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MachOSection:
    segment: str
    name: str
    address: int
    offset: int
    size: int
    flags: int


class MachOFile:
    def __init__(self, data: bytes | bytearray | memoryview) -> None:
        self._data = _thin_slice(memoryview(data))
        self.sections = self._parse()

    @classmethod
    def from_path(cls, path: str | Path) -> MachOFile:
        return cls(Path(path).read_bytes())

    def section_data(self, segment: str, section: str) -> memoryview:
        for item in self.sections:
            if item.segment == segment and item.name == section:
                return self._slice(item.offset, item.size)
        raise KeyError((segment, section))

    def protobuf_regions(self) -> tuple[memoryview, ...]:
        wanted = {("__TEXT", "__const"), ("__TEXT", "__cstring"), ("__DATA", "__const")}
        return tuple(
            self._slice(item.offset, item.size)
            for item in self.sections
            if (item.segment, item.name) in wanted
        )

    def _parse(self) -> tuple[MachOSection, ...]:
        if len(self._data) < 4:
            raise MachOError("truncated Mach-O file")
        magic = bytes(self._data[:4])
        configs = {
            b"\xce\xfa\xed\xfe": ("<", False),
            b"\xfe\xed\xfa\xce": (">", False),
            b"\xcf\xfa\xed\xfe": ("<", True),
            b"\xfe\xed\xfa\xcf": (">", True),
        }
        if magic not in configs:
            raise MachOError("not a Mach-O file")
        prefix, is_64 = configs[magic]
        header_fmt = prefix + ("IiiIIIII" if is_64 else "IiiIIII")
        header = self._unpack(header_fmt, 0)
        commands, command_bytes = int(header[4]), int(header[5])
        offset, limit = (
            struct.calcsize(header_fmt),
            struct.calcsize(header_fmt) + command_bytes,
        )
        if limit > len(self._data):
            raise MachOError("truncated Mach-O load commands")
        sections: list[MachOSection] = []
        segment_command = 0x19 if is_64 else 0x1
        for _ in range(commands):
            command, size = self._unpack(prefix + "II", offset)
            if size < 8 or offset + size > limit:
                raise MachOError("invalid Mach-O load command")
            if command == segment_command:
                sections.extend(self._parse_segment(offset, int(size), prefix, is_64))
            offset += int(size)
        return tuple(sections)

    def _parse_segment(
        self, offset: int, command_size: int, prefix: str, is_64: bool
    ) -> list[MachOSection]:
        segment_fmt = prefix + ("II16sQQQQiiII" if is_64 else "II16sIIIIiiII")
        raw = self._unpack(segment_fmt, offset)
        segment_name, count = _name(raw[2]), int(raw[-2])
        section_fmt = prefix + ("16s16sQQIIIIIIII" if is_64 else "16s16sIIIIIIIII")
        section_size = struct.calcsize(section_fmt)
        cursor = offset + struct.calcsize(segment_fmt)
        if cursor + count * section_size > offset + command_size:
            raise MachOError("sections exceed their load command")
        result: list[MachOSection] = []
        for _ in range(count):
            item = self._unpack(section_fmt, cursor)
            address, size, file_offset, flags = (
                int(item[2]),
                int(item[3]),
                int(item[4]),
                int(item[8]),
            )
            self._slice(file_offset, size)
            result.append(
                MachOSection(
                    _name(item[1]) or segment_name,
                    _name(item[0]),
                    address,
                    file_offset,
                    size,
                    flags,
                )
            )
            cursor += section_size
        return result

    def _unpack(self, fmt: str, offset: int) -> tuple[Any, ...]:
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(self._data):
            raise MachOError("truncated Mach-O structure")
        return struct.unpack_from(fmt, self._data, offset)

    def _slice(self, offset: int, size: int) -> memoryview:
        if offset < 0 or size < 0 or offset + size > len(self._data):
            raise MachOError("Mach-O range lies outside the file")
        return self._data[offset : offset + size]


def _name(raw: bytes) -> str:
    return bytes(raw).split(b"\x00", 1)[0].decode("ascii", errors="replace")


def _thin_slice(data: memoryview) -> memoryview:
    if len(data) < 8:
        return data
    magic = bytes(data[:4])
    formats = {
        b"\xca\xfe\xba\xbe": (">", False),
        b"\xbe\xba\xfe\xca": ("<", False),
        b"\xca\xfe\xba\xbf": (">", True),
        b"\xbf\xba\xfe\xca": ("<", True),
    }
    if magic not in formats:
        return data
    prefix, is_64 = formats[magic]
    count = struct.unpack_from(prefix + "I", data, 4)[0]
    if count == 0:
        raise MachOError("fat Mach-O contains no architectures")
    fmt = prefix + ("iiQQII" if is_64 else "iiIII")
    size = struct.calcsize(fmt)
    if 8 + count * size > len(data):
        raise MachOError("truncated fat Mach-O table")
    arch = struct.unpack_from(fmt, data, 8)
    offset, length = int(arch[2]), int(arch[3])
    if offset < 0 or length <= 0 or offset + length > len(data):
        raise MachOError("fat Mach-O architecture lies outside the file")
    return data[offset : offset + length]

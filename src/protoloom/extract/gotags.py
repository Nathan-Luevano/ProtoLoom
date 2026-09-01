import re
from dataclasses import dataclass
from typing import Literal, Protocol

from protoloom.container.elf import ElfSection

_PROTOBUF_TAG = re.compile(r'(?:^| )protobuf:"([^"]+)"(?: |$)')


@dataclass(frozen=True, slots=True)
class GoProtobufTag:
    encoding: str
    number: int
    label: str
    name: str
    packed: bool
    proto3: bool


class GoElf(Protocol):
    bits: int
    endian: Literal["little", "big"]
    sections: tuple[ElfSection, ...]

    def get_section(self, name: str) -> ElfSection: ...

    def section_data(self, section: str | ElfSection) -> memoryview: ...


class _Memory:
    def __init__(self, elf: GoElf) -> None:
        self.elf = elf
        self.rodata = elf.get_section(".rodata")

    def bytes(self, address: int, size: int) -> bytes:
        for section in self.elf.sections:
            if section.address <= address and address + size <= (
                section.address + section.size
            ):
                offset = address - section.address
                return bytes(self.elf.section_data(section)[offset : offset + size])
        raise ValueError(f"Go metadata address is outside file sections: 0x{address:x}")

    def uint(self, address: int, size: int) -> int:
        return int.from_bytes(self.bytes(address, size), self.elf.endian)

    def name(self, address: int) -> tuple[str, str]:
        flags = self.uint(address, 1)
        name_size, used = self.varint(address + 1)
        start = address + 1 + used
        name = self.bytes(start, name_size).decode("utf-8")
        if not flags & 2:
            return name, ""
        tag_size, used = self.varint(start + name_size)
        tag_start = start + name_size + used
        return name, self.bytes(tag_start, tag_size).decode("utf-8")

    def varint(self, address: int) -> tuple[int, int]:
        value = 0
        for index in range(5):
            byte = self.uint(address + index, 1)
            value |= (byte & 0x7F) << (7 * index)
            if byte < 0x80:
                return value, index + 1
        raise ValueError("oversized Go name length")


def parse_protobuf_tag(value: str) -> GoProtobufTag | None:
    match = _PROTOBUF_TAG.search(value)
    if match is None:
        return None
    parts = match.group(1).split(",")
    if len(parts) < 3 or parts[2] not in {"opt", "req", "rep"}:
        return None
    try:
        number = int(parts[1])
    except ValueError:
        return None
    options = {item.split("=", 1)[0]: item for item in parts[3:]}
    name = options.get("name", "name=").removeprefix("name=")
    if not name or not 1 <= number < 2**29:
        return None
    return GoProtobufTag(
        parts[0],
        number,
        {"opt": "optional", "req": "required", "rep": "repeated"}[parts[2]],
        name,
        "packed" in options,
        "proto3" in options,
    )

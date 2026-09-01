import re
import struct
from dataclasses import dataclass
from typing import Literal, Protocol

from protoloom.container.elf import ElfSection
from protoloom.model import Confidence, Evidence, Field, Message, RecoveredSchema

_PROTOBUF_TAG = re.compile(r'(?:^| )protobuf:"([^"]+)"(?: |$)')


@dataclass(frozen=True, slots=True)
class GoProtobufTag:
    encoding: str
    number: int
    label: str
    name: str
    packed: bool
    proto3: bool
    enum_name: str | None


@dataclass(frozen=True, slots=True)
class GoTagExtraction:
    schemas: tuple[RecoveredSchema, ...]
    bailouts: tuple[str, ...]


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

    def pointer(self, address: int) -> int:
        return self.uint(address, 8)

    def type_name(self, address: int) -> str:
        relative = int.from_bytes(
            self.bytes(address + 40, 4), self.elf.endian, signed=True
        )
        name, _ = self.name(self.rodata.address + relative)
        if self.uint(address + 20, 1) & 2 and name.startswith("*"):
            return name[1:]
        return name

    def kind(self, address: int) -> int:
        return self.uint(address + 23, 1) & 0x1F

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
        options.get("enum", "enum=").removeprefix("enum=") or None,
    )


def _protobuf_type(memory: _Memory, address: int, tag: GoProtobufTag) -> str | None:
    kind = memory.kind(address)
    if tag.label == "repeated":
        if kind != 23:
            return None
        address = memory.pointer(address + 48)
        kind = memory.kind(address)
    if tag.enum_name is not None and tag.encoding == "varint":
        return tag.enum_name.rsplit(".", 1)[-1]
    scalar = {
        ("varint", 1): "bool",
        ("varint", 10): "uint32",
        ("varint", 11): "uint64",
        ("zigzag32", 5): "sint32",
        ("zigzag64", 6): "sint64",
        ("fixed32", 10): "fixed32",
        ("fixed32", 14): "float",
        ("fixed64", 11): "fixed64",
        ("fixed64", 15): "double",
    }.get((tag.encoding, kind))
    if scalar is not None:
        return scalar
    if tag.encoding == "bytes" and kind == 24:
        return "string"
    if tag.encoding == "bytes" and kind == 23:
        element = memory.pointer(address + 48)
        return "bytes" if memory.kind(element) == 8 else None
    if tag.encoding == "bytes" and kind == 22:
        message = memory.pointer(address + 48)
        return memory.type_name(message).rsplit(".", 1)[-1]
    return None


def _tagged_field(
    memory: _Memory, entry: int, source: str, owner: str
) -> tuple[Field | None, str | None, bool]:
    go_name, raw_tag = memory.name(memory.pointer(entry))
    tag = parse_protobuf_tag(raw_tag)
    if tag is None:
        return None, None, True
    result_type = _protobuf_type(memory, memory.pointer(entry + 8), tag)
    if result_type is None:
        return None, f"{owner}.{go_name}: unsupported tag/type: {raw_tag}", tag.proto3
    return (
        Field(
            tag.name,
            tag.number,
            result_type,
            Confidence.CERTAIN,
            [Evidence(source, owner, raw_tag)],
            label=tag.label,
            packed=tag.packed if tag.label == "repeated" else None,
        ),
        None,
        tag.proto3,
    )


def _struct_schema(
    memory: _Memory, address: int, source: str
) -> tuple[RecoveredSchema | None, list[str]]:
    type_name = memory.type_name(address)
    fields_address = memory.pointer(address + 56)
    field_count = memory.uint(address + 64, 8)
    if field_count > 10_000:
        raise ValueError(f"implausible Go struct field count: {field_count}")
    fields: list[Field] = []
    bailouts: list[str] = []
    proto3 = True
    for index in range(field_count):
        entry = fields_address + index * 24
        field, bailout, is_proto3 = _tagged_field(memory, entry, source, type_name)
        proto3 &= is_proto3
        if field is not None:
            fields.append(field)
        if bailout is not None:
            bailouts.append(bailout)
    if not fields or bailouts:
        return None, bailouts
    message_name = type_name.rsplit(".", 1)[-1]
    evidence = [Evidence(source, type_name, "Go runtime struct metadata")]
    message = Message(
        message_name, fields, confidence=Confidence.CERTAIN, evidence=evidence
    )
    return (
        RecoveredSchema(
            f"{message_name}.proto",
            syntax="proto3" if proto3 else "proto2",
            messages=[message],
            evidence=evidence,
        ),
        [],
    )


def scan_go_struct_tags(elf: GoElf, source: str) -> GoTagExtraction:
    if elf.bits != 64 or elf.endian != "little":
        return GoTagExtraction((), ("unsupported Go ELF architecture",))
    try:
        build_info = bytes(elf.section_data(".go.buildinfo"))
        typelinks = bytes(elf.section_data(".typelink"))
        memory = _Memory(elf)
    except KeyError:
        return GoTagExtraction((), ())
    if b"go1.24." not in build_info:
        return GoTagExtraction((), ("unsupported Go metadata version",))
    if len(typelinks) % 4:
        return GoTagExtraction((), ("malformed Go type-link table",))
    schemas: list[RecoveredSchema] = []
    bailouts: list[str] = []
    for (relative,) in struct.iter_unpack("<i", typelinks):
        try:
            pointer = memory.rodata.address + relative
            if memory.kind(pointer) != 22:
                continue
            target = memory.pointer(pointer + 48)
            if memory.kind(target) != 25:
                continue
            schema, reasons = _struct_schema(memory, target, source)
        except (UnicodeDecodeError, ValueError):
            continue
        if schema is not None:
            schemas.append(schema)
        bailouts.extend(reasons)
    return GoTagExtraction(tuple(schemas), tuple(bailouts))

from typing import Literal

import pytest

from protoloom.container.elf import ElfSection
from protoloom.extract.gotags import (
    GoProtobufTag,
    _Memory,
    _protobuf_type,
    _tagged_field,
    parse_protobuf_tag,
    scan_go_struct_tags,
)


class FakeElf:
    bits = 64
    endian: Literal["little", "big"] = "little"

    def __init__(self, data: bytes) -> None:
        self.payloads = {".rodata": data}
        self.sections: tuple[ElfSection, ...] = (
            ElfSection(".rodata", 0, len(data), 0x1000, 2, 1),
        )

    def get_section(self, name: str) -> ElfSection:
        return next(section for section in self.sections if section.name == name)

    def section_data(self, section: str | ElfSection) -> memoryview:
        name = section if isinstance(section, str) else section.name
        return memoryview(self.payloads[name])


def test_parses_go_protobuf_struct_tag() -> None:
    tag = parse_protobuf_tag(
        'protobuf:"zigzag32,3,rep,packed,name=samples,proto3" json:"samples,omitempty"'
    )
    assert tag == GoProtobufTag("zigzag32", 3, "repeated", "samples", True, True, None)


def test_rejects_missing_or_malformed_protobuf_tag() -> None:
    assert parse_protobuf_tag('json:"value"') is None
    assert parse_protobuf_tag('protobuf:"varint,nope,opt,name=value"') is None
    assert parse_protobuf_tag('protobuf:"varint,1,unknown,name=value"') is None
    assert parse_protobuf_tag('protobuf:"varint,0,opt,name=value"') is None


def test_reads_go_runtime_name_and_tag() -> None:
    name = b"Samples"
    tag = b'protobuf:"zigzag32,3,rep,packed,name=samples,proto3"'
    encoded = bytes((3, len(name))) + name + bytes((len(tag),)) + tag
    memory = _Memory(FakeElf(encoded))

    assert memory.name(0x1000) == ("Samples", tag.decode())


def test_reads_go_runtime_type_name_and_kind() -> None:
    data = bytearray(128)
    name = b"*main.Record"
    data[32 : 34 + len(name)] = bytes((1, len(name))) + name
    data[64 + 20] = 2
    data[64 + 23] = 25
    data[64 + 40 : 64 + 44] = (32).to_bytes(4, "little", signed=True)
    memory = _Memory(FakeElf(bytes(data)))

    assert memory.type_name(0x1040) == "main.Record"
    assert memory.kind(0x1040) == 25


def test_resolves_repeated_zigzag_field_type() -> None:
    data = bytearray(192)
    data[64 + 23] = 23
    data[64 + 48 : 64 + 56] = (0x1080).to_bytes(8, "little")
    data[128 + 23] = 5
    memory = _Memory(FakeElf(bytes(data)))
    tag = parse_protobuf_tag('protobuf:"zigzag32,3,rep,packed,name=samples,proto3"')

    assert tag is not None
    assert _protobuf_type(memory, 0x1040, tag) == "sint32"


def test_builds_field_from_linked_go_metadata() -> None:
    data = bytearray(256)
    name = b"Id"
    tag = b'protobuf:"varint,1,opt,name=id,proto3"'
    encoded = bytes((3, len(name))) + name + bytes((len(tag),)) + tag
    data[128 : 128 + len(encoded)] = encoded
    data[32:40] = (0x1080).to_bytes(8, "little")
    data[40:48] = (0x1040).to_bytes(8, "little")
    data[64 + 23] = 11

    field, bailout, proto3 = _tagged_field(
        _Memory(FakeElf(bytes(data))), 0x1020, "fixture", "main.Record"
    )

    assert bailout is None and proto3
    assert field is not None
    assert (field.name, field.number, field.type_name) == ("id", 1, "uint64")


def test_scans_linked_go_struct_metadata() -> None:
    data = bytearray(1024)
    type_name = b"*main.Record"
    data[32 : 34 + len(type_name)] = bytes((1, len(type_name))) + type_name
    data[64 + 23] = 22
    data[64 + 48 : 64 + 56] = (0x1100).to_bytes(8, "little")
    data[256 + 20] = 2
    data[256 + 23] = 25
    data[256 + 40 : 256 + 44] = (32).to_bytes(4, "little", signed=True)
    data[256 + 56 : 256 + 64] = (0x1200).to_bytes(8, "little")
    data[256 + 64 : 256 + 72] = (1).to_bytes(8, "little")
    data[384 + 23] = 11
    field_name = b"Id"
    field_tag = b'protobuf:"varint,1,opt,name=id,proto3"'
    encoded = (
        bytes((3, len(field_name))) + field_name + bytes((len(field_tag),)) + field_tag
    )
    data[768 : 768 + len(encoded)] = encoded
    data[512:520] = (0x1300).to_bytes(8, "little")
    data[520:528] = (0x1180).to_bytes(8, "little")
    elf = FakeElf(bytes(data))
    elf.payloads.update(
        {".typelink": (64).to_bytes(4, "little"), ".go.buildinfo": b"go1.24.0"}
    )
    elf.sections += (
        ElfSection(".typelink", 0, 4, 0x2000, 2, 1),
        ElfSection(".go.buildinfo", 0, 8, 0x3000, 2, 1),
    )

    result = scan_go_struct_tags(elf, "fixture")

    assert result.bailouts == ()
    fields = result.schemas[0].messages[0].fields
    assert [(field.name, field.type_name) for field in fields] == [("id", "uint64")]


def test_rejects_unsupported_go_metadata_version() -> None:
    elf = FakeElf(b"")
    elf.payloads.update({".typelink": b"", ".go.buildinfo": b"go1.23.0"})
    elf.sections += (
        ElfSection(".typelink", 0, 0, 0x2000, 2, 1),
        ElfSection(".go.buildinfo", 0, 8, 0x3000, 2, 1),
    )

    result = scan_go_struct_tags(elf, "fixture")

    assert result.bailouts == ("unsupported Go metadata version",)


def test_memory_bytes_rejects_address_outside_sections() -> None:
    memory = _Memory(FakeElf(b"1234"))
    with pytest.raises(ValueError, match="outside file sections"):
        memory.bytes(0x5000, 1)


def test_type_name_unstripped_without_pointer_flag() -> None:
    data = bytearray(128)
    name = b"main.Record"
    data[32 : 34 + len(name)] = bytes((1, len(name))) + name
    data[64 + 20] = 0
    data[64 + 40 : 64 + 44] = (32).to_bytes(4, "little", signed=True)
    memory = _Memory(FakeElf(bytes(data)))

    assert memory.type_name(0x1040) == "main.Record"


def test_varint_rejects_oversized_encoding() -> None:
    memory = _Memory(FakeElf(bytes([0x80] * 5)))
    with pytest.raises(ValueError, match="oversized Go name length"):
        memory.varint(0x1000)


def test_protobuf_type_rejects_repeated_field_with_wrong_kind() -> None:
    data = bytearray(64)
    data[23] = 5
    memory = _Memory(FakeElf(bytes(data)))
    tag = parse_protobuf_tag('protobuf:"varint,1,rep,name=x"')

    assert tag is not None
    assert _protobuf_type(memory, 0x1000, tag) is None


def test_protobuf_type_resolves_enum_by_short_name() -> None:
    memory = _Memory(FakeElf(bytes(64)))
    tag = parse_protobuf_tag('protobuf:"varint,1,opt,name=x,enum=pkg.Color"')

    assert tag is not None
    assert _protobuf_type(memory, 0x1000, tag) == "Color"


def test_protobuf_type_resolves_string_and_bytes() -> None:
    string_data = bytearray(64)
    string_data[23] = 24
    string_memory = _Memory(FakeElf(bytes(string_data)))
    string_tag = parse_protobuf_tag('protobuf:"bytes,1,opt,name=x"')
    assert string_tag is not None
    assert _protobuf_type(string_memory, 0x1000, string_tag) == "string"

    bytes_data = bytearray(128)
    bytes_data[23] = 23
    bytes_data[48:56] = (0x1040).to_bytes(8, "little")
    bytes_data[64 + 23] = 8
    bytes_memory = _Memory(FakeElf(bytes(bytes_data)))
    assert _protobuf_type(bytes_memory, 0x1000, string_tag) == "bytes"

    non_byte_data = bytearray(bytes_data)
    non_byte_data[64 + 23] = 99
    non_byte_memory = _Memory(FakeElf(bytes(non_byte_data)))
    assert _protobuf_type(non_byte_memory, 0x1000, string_tag) is None


def test_protobuf_type_resolves_embedded_message_by_short_name() -> None:
    data = bytearray(256)
    data[23] = 22
    data[48:56] = (0x1080).to_bytes(8, "little")
    name = b"pkg.Msg"
    data[0] = 1
    data[1] = len(name)
    data[2 : 2 + len(name)] = name
    data[128 + 40 : 128 + 44] = (0).to_bytes(4, "little", signed=True)
    memory = _Memory(FakeElf(bytes(data)))
    tag = parse_protobuf_tag('protobuf:"bytes,1,opt,name=x"')

    assert tag is not None
    assert _protobuf_type(memory, 0x1000, tag) == "Msg"


def test_rejects_malformed_go_type_links() -> None:
    elf = FakeElf(b"")
    elf.payloads.update({".typelink": b"x", ".go.buildinfo": b"go1.24.0"})
    elf.sections += (
        ElfSection(".typelink", 0, 1, 0x2000, 2, 1),
        ElfSection(".go.buildinfo", 0, 8, 0x3000, 2, 1),
    )

    result = scan_go_struct_tags(elf, "fixture")

    assert result.bailouts == ("malformed Go type-link table",)

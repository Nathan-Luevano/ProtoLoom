from typing import Literal

from protoloom.container.elf import ElfSection
from protoloom.extract.gotags import GoProtobufTag, _Memory, parse_protobuf_tag


class FakeElf:
    bits = 64
    endian: Literal["little", "big"] = "little"

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.sections: tuple[ElfSection, ...] = (
            ElfSection(".rodata", 0, len(data), 0x1000, 2, 1),
        )

    def get_section(self, name: str) -> ElfSection:
        assert name == ".rodata"
        return self.sections[0]

    def section_data(self, section: str | ElfSection) -> memoryview:
        return memoryview(self.data)


def test_parses_go_protobuf_struct_tag() -> None:
    tag = parse_protobuf_tag(
        'protobuf:"zigzag32,3,rep,packed,name=samples,proto3" json:"samples,omitempty"'
    )
    assert tag == GoProtobufTag("zigzag32", 3, "repeated", "samples", True, True)


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

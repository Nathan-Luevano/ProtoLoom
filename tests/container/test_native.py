import struct
from pathlib import Path

from protoloom.container.elf import ElfFile
from protoloom.container.macho import MachOFile


def test_parses_host_elf() -> None:
    path = next(
        path for path in (Path("/bin/sh"), Path("/usr/bin/env")) if path.exists()
    )
    elf = ElfFile.from_path(path)
    assert elf.bits in {32, 64}
    assert elf.sections
    assert elf.segments
    assert len(elf.section_data(elf.sections[0])) == elf.sections[0].size


def _macho_with_const_section() -> bytes:
    header_size = struct.calcsize("<IiiIIIII")
    segment_size = struct.calcsize("<II16sQQQQiiII")
    section_size = struct.calcsize("<16s16sQQIIIIIIII")
    command_size = segment_size + section_size
    data_offset = header_size + command_size
    header = struct.pack("<IiiIIIII", 0xFEEDFACF, 0, 0, 2, 1, command_size, 0, 0)
    segment = struct.pack(
        "<II16sQQQQiiII",
        0x19,
        command_size,
        b"__TEXT",
        0,
        4,
        data_offset,
        4,
        0,
        0,
        1,
        0,
    )
    section = struct.pack(
        "<16s16sQQIIIIIIII",
        b"__const",
        b"__TEXT",
        0,
        4,
        data_offset,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    return header + segment + section + b"data"


def test_parses_macho_64_section() -> None:
    macho = MachOFile(_macho_with_const_section())
    assert bytes(macho.section_data("__TEXT", "__const")) == b"data"
    assert [bytes(region) for region in macho.protobuf_regions()] == [b"data"]


def test_selects_first_fat_macho_architecture() -> None:
    thin = _macho_with_const_section()
    offset = 8 + struct.calcsize(">iiIII")
    header = struct.pack(">IIiiIII", 0xCAFEBABE, 1, 0, 0, offset, len(thin), 0)
    macho = MachOFile(header + thin)
    assert bytes(macho.section_data("__TEXT", "__const")) == b"data"

import struct
from pathlib import Path

import pytest

from protoloom.container.elf import ElfError, ElfFile
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


def _host_elf_with_extended_sections(section_count: int | None = None) -> bytes:
    path = next(
        path for path in (Path("/bin/sh"), Path("/usr/bin/env")) if path.exists()
    )
    raw = bytearray(path.read_bytes())
    prefix = "<" if raw[5] == 1 else ">"
    if raw[4] == 2:
        section_offset = struct.unpack_from(prefix + "Q", raw, 40)[0]
        count_offset, index_offset = 60, 62
        size_offset, link_offset = section_offset + 32, section_offset + 40
        size_format = "Q"
    else:
        section_offset = struct.unpack_from(prefix + "I", raw, 32)[0]
        count_offset, index_offset = 48, 50
        size_offset, link_offset = section_offset + 20, section_offset + 24
        size_format = "I"
    original_count = struct.unpack_from(prefix + "H", raw, count_offset)[0]
    original_index = struct.unpack_from(prefix + "H", raw, index_offset)[0]
    struct.pack_into(prefix + "H", raw, count_offset, 0)
    struct.pack_into(prefix + "H", raw, index_offset, 0xFFFF)
    struct.pack_into(
        prefix + size_format, raw, size_offset, section_count or original_count
    )
    struct.pack_into(prefix + "I", raw, link_offset, original_index)
    return bytes(raw)


def test_parses_extended_elf_section_numbering() -> None:
    regular = ElfFile.from_path("/bin/sh")
    extended = ElfFile(_host_elf_with_extended_sections())
    assert len(extended.sections) == len(regular.sections)
    assert extended.sections[1:] == regular.sections[1:]


def test_rejects_extended_elf_table_outside_file() -> None:
    with pytest.raises(ElfError, match="section table"):
        ElfFile(_host_elf_with_extended_sections(0x100000))


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

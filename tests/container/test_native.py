import struct
from pathlib import Path

import pytest

from protoloom.container.elf import ElfError, ElfFile
from protoloom.container.macho import MachOError, MachOFile


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


def _host_elf_with_malformed_section(
    *, name_offset: int | None = None, file_offset: int | None = None
) -> bytes:
    path = next(
        path for path in (Path("/bin/sh"), Path("/usr/bin/env")) if path.exists()
    )
    raw = bytearray(path.read_bytes())
    prefix = "<" if raw[5] == 1 else ">"
    if raw[4] == 2:
        section_table = struct.unpack_from(prefix + "Q", raw, 40)[0]
        entry_size = struct.unpack_from(prefix + "H", raw, 58)[0]
        offset_field, size_field = 24, 32
        word_format = "Q"
    else:
        section_table = struct.unpack_from(prefix + "I", raw, 32)[0]
        entry_size = struct.unpack_from(prefix + "H", raw, 46)[0]
        offset_field, size_field = 16, 20
        word_format = "I"
    section = section_table + entry_size
    if name_offset is not None:
        struct.pack_into(prefix + "I", raw, section, name_offset)
    if file_offset is not None:
        struct.pack_into(prefix + word_format, raw, section + offset_field, file_offset)
        struct.pack_into(prefix + word_format, raw, section + size_field, 1)
    return bytes(raw)


def test_rejects_elf_section_outside_file() -> None:
    path_size = len(_host_elf_with_malformed_section())
    malformed = _host_elf_with_malformed_section(file_offset=path_size)
    with pytest.raises(ElfError, match="outside the file"):
        ElfFile(malformed)


def test_rejects_elf_section_name_outside_string_table() -> None:
    malformed = _host_elf_with_malformed_section(name_offset=0xFFFFFFFF)
    with pytest.raises(ElfError, match="section name"):
        ElfFile(malformed)


def _elf64_header(
    *,
    shoff: int,
    shnum: int,
    shentsize: int,
    shstrndx: int,
    phoff: int,
    phnum: int,
    phentsize: int,
) -> bytes:
    ident = bytes([0x7F, 0x45, 0x4C, 0x46, 2, 1]) + b"\x00" * 10
    body = struct.pack(
        "<HHIQQQIHHHHHH",
        0,
        0,
        0,
        0,
        phoff,
        shoff,
        0,
        64,
        phentsize,
        phnum,
        shentsize,
        shnum,
        shstrndx,
    )
    return ident + body


def test_rejects_elf_with_bad_magic() -> None:
    with pytest.raises(ElfError, match="not an ELF file"):
        ElfFile(b"not-elf-data")


def test_rejects_elf_with_unsupported_identification() -> None:
    ident = bytes([0x7F, 0x45, 0x4C, 0x46, 9, 1]) + b"\x00" * 10
    with pytest.raises(ElfError, match="unsupported ELF identification"):
        ElfFile(ident)


def test_rejects_truncated_elf_header() -> None:
    with pytest.raises(ElfError, match="truncated ELF structure"):
        ElfFile(bytes([0x7F, 0x45, 0x4C, 0x46, 2, 1]) + b"\x00" * 10)


def test_minimal_elf_has_no_sections_or_segments() -> None:
    elf = ElfFile(
        _elf64_header(
            shoff=0, shnum=0, shentsize=0, shstrndx=0, phoff=0, phnum=0, phentsize=0
        )
    )
    assert elf.sections == () and elf.segments == ()
    assert elf.is_go_binary is False


def _section64(
    *,
    name: int = 0,
    type_: int = 0,
    flags: int = 0,
    addr: int = 0,
    offset: int = 0,
    size: int = 0,
    link: int = 0,
    info: int = 0,
    align: int = 0,
    entsize: int = 0,
) -> bytes:
    return struct.pack(
        "<IIQQQQIIQQ",
        name,
        type_,
        flags,
        addr,
        offset,
        size,
        link,
        info,
        align,
        entsize,
    )


def test_is_go_binary_detects_buildinfo_section() -> None:
    strtab = b"\x00.go.buildinfo\x00"
    header = _elf64_header(
        shoff=64, shnum=1, shentsize=64, shstrndx=0, phoff=0, phnum=0, phentsize=0
    )
    section = _section64(name=1, type_=1, offset=128, size=len(strtab))
    elf = ElfFile(header + section + strtab)

    assert elf.is_go_binary is True
    assert elf.get_section(".go.buildinfo").name == ".go.buildinfo"
    with pytest.raises(KeyError):
        elf.get_section(".missing")


def test_rejects_section_name_table_index_out_of_range() -> None:
    header = _elf64_header(
        shoff=64, shnum=1, shentsize=64, shstrndx=5, phoff=0, phnum=0, phentsize=0
    )
    with pytest.raises(ElfError, match="section name table index"):
        ElfFile(header + _section64())


def test_rejects_invalid_section_header_size() -> None:
    header = _elf64_header(
        shoff=0, shnum=1, shentsize=8, shstrndx=0, phoff=0, phnum=0, phentsize=0
    )
    with pytest.raises(ElfError, match="invalid section header size"):
        ElfFile(header)


def test_rejects_invalid_program_header_size() -> None:
    header = _elf64_header(
        shoff=0, shnum=0, shentsize=0, shstrndx=0, phoff=0, phnum=1, phentsize=8
    )
    with pytest.raises(ElfError, match="invalid program header size"):
        ElfFile(header)


def test_rejects_extended_phnum_without_section_table() -> None:
    header = _elf64_header(
        shoff=0, shnum=0, shentsize=0, shstrndx=0, phoff=0, phnum=0xFFFF, phentsize=0
    )
    with pytest.raises(ElfError, match="extended program header count"):
        ElfFile(header)


def test_reads_extended_phnum_from_first_section() -> None:
    seg_size = struct.calcsize("<IIQQQQQQ")
    ph_off = 64 + 64
    header = _elf64_header(
        shoff=64,
        shnum=1,
        shentsize=64,
        shstrndx=0,
        phoff=ph_off,
        phnum=0xFFFF,
        phentsize=seg_size,
    )
    section = _section64(name=0, info=1)
    segment = struct.pack("<IIQQQQQQ", 0, 0, 0, 0, 0, 0, 0, 0)
    elf = ElfFile(header + section + segment)

    assert len(elf.segments) == 1


def test_empty_string_table_yields_empty_section_name() -> None:
    header = _elf64_header(
        shoff=64, shnum=1, shentsize=64, shstrndx=0, phoff=0, phnum=0, phentsize=0
    )
    section = _section64(name=0, type_=3, offset=0, size=0)
    elf = ElfFile(header + section)

    assert elf.sections[0].name == ""


def test_rejects_unterminated_section_name() -> None:
    header = _elf64_header(
        shoff=64, shnum=1, shentsize=64, shstrndx=0, phoff=0, phnum=0, phentsize=0
    )
    section = _section64(name=0, type_=3, offset=128, size=3)
    with pytest.raises(ElfError, match="unterminated ELF section name"):
        ElfFile(header + section + b"abc")


def test_segment_data_reads_program_header_bytes() -> None:
    seg_size = struct.calcsize("<IIQQQQQQ")
    ph_off = 64
    header = _elf64_header(
        shoff=0,
        shnum=0,
        shentsize=0,
        shstrndx=0,
        phoff=ph_off,
        phnum=1,
        phentsize=seg_size,
    )
    segment = struct.pack("<IIQQQQQQ", 1, 0, ph_off + seg_size, 0, 0, 4, 4, 0)
    elf = ElfFile(header + segment + b"data")

    assert bytes(elf.segment_data(elf.segments[0])) == b"data"


def test_parses_32_bit_segment_fields() -> None:
    ident = bytes([0x7F, 0x45, 0x4C, 0x46, 1, 1]) + b"\x00" * 10
    ph_size = struct.calcsize("<IIIIIIII")
    ph_off = 52
    body = struct.pack(
        "<HHIIIIIHHHHHH", 0, 0, 0, 0, ph_off, 0, 0, 52, ph_size, 1, 0, 0, 0
    )
    segment = struct.pack("<IIIIIIII", 7, 0, 0, 0, 0, 0, 5, 0)
    elf = ElfFile(ident + body + segment)

    assert elf.bits == 32
    assert elf.segments[0].segment_type == 7
    assert elf.segments[0].flags == 5


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


def test_rejects_inconsistent_macho_load_command_count() -> None:
    malformed = bytearray(_macho_with_const_section())
    struct.pack_into("<I", malformed, 16, 0)
    with pytest.raises(MachOError, match="do not fill"):
        MachOFile(malformed)


def test_rejects_truncated_macho_segment_command() -> None:
    malformed = bytearray(_macho_with_const_section())
    header_size = struct.calcsize("<IiiIIIII")
    struct.pack_into("<I", malformed, header_size + 4, 8)
    with pytest.raises(MachOError, match="segment command"):
        MachOFile(malformed)


def test_accepts_macho_zero_fill_section_without_file_data() -> None:
    raw = bytearray(_macho_with_const_section())
    section_offset = struct.calcsize("<IiiIIIII") + struct.calcsize("<II16sQQQQiiII")
    raw[section_offset : section_offset + 16] = b"__bss\x00".ljust(16, b"\x00")
    struct.pack_into("<Q", raw, section_offset + 40, len(raw) * 2)
    struct.pack_into("<I", raw, section_offset + 48, 0)
    struct.pack_into("<I", raw, section_offset + 64, 1)
    macho = MachOFile(raw)
    assert macho.sections[0].name == "__bss"
    assert macho.sections[0].size == len(raw) * 2


def test_selects_first_fat_macho_architecture() -> None:
    thin = _macho_with_const_section()
    offset = 8 + struct.calcsize(">iiIII")
    header = struct.pack(">IIiiIII", 0xCAFEBABE, 1, 0, 0, offset, len(thin), 0)
    macho = MachOFile(header + thin)
    assert bytes(macho.section_data("__TEXT", "__const")) == b"data"


def test_rejects_misaligned_fat_macho_architecture() -> None:
    thin = _macho_with_const_section()
    offset = 8 + struct.calcsize(">iiIII")
    header = struct.pack(">IIiiIII", 0xCAFEBABE, 1, 0, 0, offset, len(thin), 4)
    with pytest.raises(MachOError, match="architecture"):
        MachOFile(header + thin)

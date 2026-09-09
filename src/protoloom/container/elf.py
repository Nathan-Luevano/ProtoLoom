from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from protoloom.container.read import read_limited


class ElfError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ElfSection:
    name: str
    offset: int
    size: int
    address: int
    flags: int
    section_type: int


@dataclass(frozen=True, slots=True)
class ElfSegment:
    segment_type: int
    offset: int
    file_size: int
    virtual_address: int
    memory_size: int
    flags: int


class ElfFile:
    def __init__(self, data: bytes | bytearray | memoryview) -> None:
        self._data = memoryview(data)
        if len(self._data) < 16 or bytes(self._data[:4]) != b"\x7fELF":
            raise ElfError("not an ELF file")
        elf_class, byte_order = self._data[4], self._data[5]
        if elf_class not in {1, 2} or byte_order not in {1, 2}:
            raise ElfError("unsupported ELF identification")
        self.bits = 32 if elf_class == 1 else 64
        self.endian: Literal["little", "big"] = "little" if byte_order == 1 else "big"
        self._prefix = "<" if byte_order == 1 else ">"
        self.sections, self.segments = self._parse_tables()

    @classmethod
    def from_path(cls, path: str | Path) -> ElfFile:
        return cls(read_limited(path))

    @property
    def is_go_binary(self) -> bool:
        return any(
            section.name in {".gopclntab", ".go.buildinfo"} for section in self.sections
        )

    def section_data(self, section: str | ElfSection) -> memoryview:
        item = self.get_section(section) if isinstance(section, str) else section
        return self._slice(item.offset, item.size)

    def segment_data(self, segment: ElfSegment) -> memoryview:
        return self._slice(segment.offset, segment.file_size)

    def get_section(self, name: str) -> ElfSection:
        for section in self.sections:
            if section.name == name:
                return section
        raise KeyError(name)

    def _parse_tables(self) -> tuple[tuple[ElfSection, ...], tuple[ElfSegment, ...]]:
        if self.bits == 32:
            header_fmt = self._prefix + "HHIIIIIHHHHHH"
        else:
            header_fmt = self._prefix + "HHIQQQIHHHHHH"
        header = self._unpack(header_fmt, 16)
        phoff, shoff = int(header[4]), int(header[5])
        phentsize, phnum = int(header[8]), int(header[9])
        shentsize, shnum, shstrndx = int(header[10]), int(header[11]), int(header[12])
        initial_section_count = 1 if shnum == 0 and shoff else shnum
        raw_sections = self._raw_sections(shoff, shentsize, initial_section_count)
        if shnum == 0 and raw_sections:
            shnum = int(raw_sections[0][5])
            raw_sections = self._raw_sections(shoff, shentsize, shnum)
        if shstrndx == 0xFFFF and raw_sections:
            shstrndx = int(raw_sections[0][6])
        if phnum == 0xFFFF:
            if not raw_sections:
                raise ElfError("extended program header count has no section table")
            phnum = int(raw_sections[0][7])
        names = b""
        if raw_sections:
            if shstrndx >= len(raw_sections):
                raise ElfError("section name table index is out of range")
            names = bytes(
                self._slice(
                    int(raw_sections[shstrndx][4]),
                    int(raw_sections[shstrndx][5]),
                )
            )
        sections: list[ElfSection] = []
        for raw in raw_sections:
            section_type = int(raw[1])
            file_offset, size = int(raw[4]), int(raw[5])
            if size and section_type not in {0, 8}:
                self._slice(file_offset, size)
            sections.append(
                ElfSection(
                    _cstring(names, int(raw[0])),
                    file_offset,
                    size,
                    int(raw[3]),
                    int(raw[2]),
                    section_type,
                )
            )
        segments = self._segments(phoff, phentsize, phnum)
        return tuple(sections), segments

    def _raw_sections(
        self, offset: int, entry_size: int, count: int
    ) -> list[tuple[int, ...]]:
        fmt = self._prefix + ("IIIIIIIIII" if self.bits == 32 else "IIQQQQIIQQ")
        expected = struct.calcsize(fmt)
        if count and entry_size < expected:
            raise ElfError("invalid section header size")
        self._validate_table(offset, entry_size, count, "section")
        return [
            tuple(
                int(value) for value in self._unpack(fmt, offset + index * entry_size)
            )
            for index in range(count)
        ]

    def _segments(
        self, offset: int, entry_size: int, count: int
    ) -> tuple[ElfSegment, ...]:
        fmt = self._prefix + ("IIIIIIII" if self.bits == 32 else "IIQQQQQQ")
        expected = struct.calcsize(fmt)
        if count and entry_size < expected:
            raise ElfError("invalid program header size")
        self._validate_table(offset, entry_size, count, "program header")
        result: list[ElfSegment] = []
        for index in range(count):
            raw = self._unpack(fmt, offset + index * entry_size)
            if self.bits == 32:
                kind, file_offset, vaddr, _, file_size, mem_size, flags, _ = raw
            else:
                kind, flags, file_offset, vaddr, _, file_size, mem_size, _ = raw
            self._slice(int(file_offset), int(file_size))
            result.append(
                ElfSegment(
                    int(kind),
                    int(file_offset),
                    int(file_size),
                    int(vaddr),
                    int(mem_size),
                    int(flags),
                )
            )
        return tuple(result)

    def _validate_table(
        self, offset: int, entry_size: int, count: int, name: str
    ) -> None:
        if count and (entry_size <= 0 or offset + entry_size * count > len(self._data)):
            raise ElfError(f"{name} table lies outside the file")

    def _unpack(self, fmt: str, offset: int) -> tuple[int, ...]:
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(self._data):
            raise ElfError("truncated ELF structure")
        return struct.unpack_from(fmt, self._data, offset)

    def _slice(self, offset: int, size: int) -> memoryview:
        if offset < 0 or size < 0 or offset + size > len(self._data):
            raise ElfError("ELF range lies outside the file")
        return self._data[offset : offset + size]


def _cstring(data: bytes, offset: int) -> str:
    if offset == 0 and not data:
        return ""
    if offset < 0 or offset >= len(data):
        raise ElfError("section name lies outside the string table")
    end = data.find(b"\x00", offset)
    if end < 0:
        raise ElfError("unterminated ELF section name")
    return data[offset:end].decode("utf-8", errors="replace")

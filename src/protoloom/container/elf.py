from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


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
        return cls(Path(path).read_bytes())

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
        raw_sections = self._raw_sections(shoff, shentsize, shnum)
        if shnum == 0 and raw_sections:
            shnum = int(raw_sections[0][5])
            raw_sections = self._raw_sections(shoff, shentsize, shnum)
        if shstrndx == 0xFFFF and raw_sections:
            shstrndx = int(raw_sections[0][6])
        names = memoryview(b"")
        if raw_sections and shstrndx < len(raw_sections):
            names = self._slice(
                int(raw_sections[shstrndx][4]), int(raw_sections[shstrndx][5])
            )
        sections = tuple(
            ElfSection(
                _cstring(names, int(raw[0])),
                int(raw[4]),
                int(raw[5]),
                int(raw[3]),
                int(raw[2]),
                int(raw[1]),
            )
            for raw in raw_sections
        )
        segments = self._segments(phoff, phentsize, phnum)
        return sections, segments

    def _raw_sections(
        self, offset: int, entry_size: int, count: int
    ) -> list[tuple[int, ...]]:
        fmt = self._prefix + ("IIIIIIIIII" if self.bits == 32 else "IIQQQQIIQQ")
        expected = struct.calcsize(fmt)
        if count and entry_size < expected:
            raise ElfError("invalid section header size")
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

    def _unpack(self, fmt: str, offset: int) -> tuple[int, ...]:
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(self._data):
            raise ElfError("truncated ELF structure")
        return struct.unpack_from(fmt, self._data, offset)

    def _slice(self, offset: int, size: int) -> memoryview:
        if offset < 0 or size < 0 or offset + size > len(self._data):
            raise ElfError("ELF range lies outside the file")
        return self._data[offset : offset + size]


def _cstring(data: memoryview, offset: int) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = bytes(data).find(b"\x00", offset)
    if end < 0:
        end = len(data)
    return bytes(data[offset:end]).decode("utf-8", errors="replace")

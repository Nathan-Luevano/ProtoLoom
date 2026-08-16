from dataclasses import dataclass

from google.protobuf.descriptor_pb2 import FileDescriptorProto
from google.protobuf.message import DecodeError


@dataclass(frozen=True, slots=True)
class DescriptorFinding:
    descriptor: FileDescriptorProto
    offset: int
    length: int
    source: str


def _read_varint(data: bytes, offset: int) -> tuple[int, int] | None:
    value = 0
    shift = 0
    for index in range(offset, min(len(data), offset + 10)):
        byte = data[index]
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, index + 1
        shift += 7
    return None


def _candidate_name(data: bytes, offset: int) -> bool:
    decoded = _read_varint(data, offset + 1)
    if decoded is None:
        return False
    length, start = decoded
    end = start + length
    if not 0 < length <= 4096 or end > len(data):
        return False
    raw = data[start:end]
    return raw.endswith(b".proto") and all(32 <= byte < 127 for byte in raw)


def _valid(descriptor: FileDescriptorProto) -> bool:
    if not descriptor.HasField("name") or not descriptor.name.endswith(".proto"):
        return False
    if not descriptor.name.isprintable():
        return False
    if descriptor.syntax not in {"", "proto2", "proto3", "editions"}:
        return False
    return bool(descriptor.message_type or descriptor.enum_type or descriptor.service)


def _field_ends(data: bytes, offset: int, limit: int) -> list[int]:
    ends: list[int] = []
    cursor = offset
    boundary = min(len(data), offset + limit)
    while cursor < boundary:
        tag = _read_varint(data, cursor)
        if tag is None or tag[0] == 0:
            break
        key, cursor = tag
        wire = key & 7
        if wire == 0:
            value = _read_varint(data, cursor)
            if value is None:
                break
            cursor = value[1]
        elif wire == 1:
            cursor += 8
        elif wire == 2:
            length = _read_varint(data, cursor)
            if length is None:
                break
            cursor = length[1] + length[0]
        elif wire == 5:
            cursor += 4
        else:
            break
        if cursor > boundary:
            break
        ends.append(cursor)
    return ends


def scan_descriptors(data: bytes, source: str = "binary") -> list[DescriptorFinding]:
    findings: dict[str, DescriptorFinding] = {}
    for offset, byte in enumerate(data):
        if byte != 0x0A or not _candidate_name(data, offset):
            continue
        # descriptors have no outer length, so only try complete wire-field boundaries.
        best: DescriptorFinding | None = None
        for end in _field_ends(data, offset, 64 * 1024 * 1024):
            candidate = FileDescriptorProto()
            try:
                consumed = candidate.MergeFromString(data[offset:end])
            except DecodeError:
                continue
            if consumed != end - offset or not _valid(candidate):
                continue
            canonical = candidate.SerializeToString()
            if data[offset : offset + len(canonical)] != canonical:
                continue
            best = DescriptorFinding(candidate, offset, len(canonical), source)
        if best is not None:
            prior = findings.get(best.descriptor.name)
            if prior is None or best.length > prior.length:
                findings[best.descriptor.name] = best
    return sorted(findings.values(), key=lambda item: (item.source, item.offset))

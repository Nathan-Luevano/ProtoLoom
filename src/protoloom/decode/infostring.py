from dataclasses import dataclass

# kept in lockstep with protobuf v33.6; the exact source links live in the docs note.
UPSTREAM_PROTOBUF_SHA = "6e1998413a5bca7c058b85999667893f167434bc"

FIELD_TYPE_MASK = 0xFF
REQUIRED_BIT = 0x100
UTF8_CHECK_BIT = 0x200
CHECK_INITIALIZED_BIT = 0x400
LEGACY_ENUM_IS_CLOSED_BIT = 0x800
HAS_HAS_BIT = 0x1000
ONEOF_TYPE_OFFSET = 51


class InfoStringError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class InfoHeader:
    flags: int
    field_count: int
    oneof_count: int = 0
    hasbits_count: int = 0
    min_field_number: int = 0
    max_field_number: int = 0
    entry_count: int = 0
    map_field_count: int = 0
    repeated_field_count: int = 0
    check_initialized_count: int = 0

    @property
    def is_proto2(self) -> bool:
        return bool(self.flags & 1)

    @property
    def is_message_set(self) -> bool:
        return bool(self.flags & 2)

    @property
    def is_edition(self) -> bool:
        return bool(self.flags & 4)


@dataclass(frozen=True, slots=True)
class InfoField:
    number: int
    type_id: int
    raw_type: int
    oneof_index: int | None = None
    hasbits_index: int | None = None

    @property
    def base_type_id(self) -> int:
        if self.oneof_index is not None:
            return self.type_id - ONEOF_TYPE_OFFSET
        return self.type_id

    @property
    def required(self) -> bool:
        return bool(self.raw_type & REQUIRED_BIT)

    @property
    def check_utf8(self) -> bool:
        return bool(self.raw_type & UTF8_CHECK_BIT)

    @property
    def check_initialized(self) -> bool:
        return bool(self.raw_type & CHECK_INITIALIZED_BIT)

    @property
    def legacy_enum_is_closed(self) -> bool:
        return bool(self.raw_type & LEGACY_ENUM_IS_CLOSED_BIT)

    @property
    def has_presence(self) -> bool:
        return bool(self.raw_type & HAS_HAS_BIT)


@dataclass(frozen=True, slots=True)
class MessageInfo:
    header: InfoHeader
    fields: tuple[InfoField, ...]


class _IntReader:
    def __init__(self, info: str) -> None:
        self.info = info
        self.position = 0

    def read(self) -> int:
        if self.position >= len(self.info):
            raise InfoStringError("truncated integer stream")
        value = ord(self.info[self.position])
        self.position += 1
        if value < 0xD800:
            return value

        result = value & 0x1FFF
        shift = 13
        while True:
            if self.position >= len(self.info):
                raise InfoStringError("unterminated continuation integer")
            value = ord(self.info[self.position])
            self.position += 1
            if value < 0xD800:
                result |= value << shift
                if result > 0xFFFFFFFF:
                    raise InfoStringError("integer exceeds the Java uint32 encoding")
                return result
            result |= (value & 0x1FFF) << shift
            shift += 13
            if shift > 26:
                raise InfoStringError("integer uses too many continuation characters")


def decode_integers(info: str) -> tuple[int, ...]:
    reader = _IntReader(info)
    values = []
    while reader.position < len(info):
        values.append(reader.read())
    return tuple(values)


def decode_info_string(info: str) -> MessageInfo:
    if not info:
        raise InfoStringError("empty info string")

    reader = _IntReader(info)
    flags = reader.read()
    field_count = reader.read()
    if field_count == 0:
        if reader.position != len(info):
            raise InfoStringError("zero-field message has trailing integers")
        return MessageInfo(InfoHeader(flags=flags, field_count=0), ())

    header_values = [reader.read() for _ in range(8)]
    header = InfoHeader(flags, field_count, *header_values)
    if header.entry_count != field_count:
        raise InfoStringError(
            f"entry count {header.entry_count} does not match field count {field_count}"
        )
    if (
        header.min_field_number <= 0
        or header.max_field_number < header.min_field_number
    ):
        raise InfoStringError("invalid field-number range in header")
    if header.map_field_count + header.repeated_field_count > field_count:
        raise InfoStringError("map and repeated counts exceed field count")

    fields = []
    for _ in range(field_count):
        number = reader.read()
        raw_type = reader.read()
        type_id = raw_type & FIELD_TYPE_MASK
        if number <= 0 or number > 536_870_911:
            raise InfoStringError(f"invalid protobuf field number {number}")
        if type_id > 68 or type_id == 50 + ONEOF_TYPE_OFFSET:
            raise InfoStringError(f"unknown protobuf-lite field type {type_id}")

        oneof_index = None
        hasbits_index = None
        if type_id >= ONEOF_TYPE_OFFSET:
            oneof_index = reader.read()
            if oneof_index >= header.oneof_count:
                raise InfoStringError(f"oneof index {oneof_index} is out of range")
        elif raw_type & HAS_HAS_BIT and type_id <= 17:
            hasbits_index = reader.read()
            if hasbits_index >= header.hasbits_count * 32:
                raise InfoStringError(f"hasbits index {hasbits_index} is out of range")
        fields.append(InfoField(number, type_id, raw_type, oneof_index, hasbits_index))

    if reader.position != len(info):
        raise InfoStringError("info string has trailing integers")
    numbers = [field.number for field in fields]
    if (
        min(numbers) != header.min_field_number
        or max(numbers) != header.max_field_number
    ):
        raise InfoStringError("field-number range does not match header")
    if len(set(numbers)) != len(numbers):
        raise InfoStringError("duplicate field number")
    return MessageInfo(header, tuple(fields))

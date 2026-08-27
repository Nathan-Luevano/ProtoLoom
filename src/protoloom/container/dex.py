from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


class DexError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DexHeader:
    version: str
    file_size: int
    header_size: int
    endian_tag: int
    string_ids_size: int
    string_ids_offset: int
    type_ids_size: int
    type_ids_offset: int
    proto_ids_size: int
    proto_ids_offset: int
    field_ids_size: int
    field_ids_offset: int
    method_ids_size: int
    method_ids_offset: int
    class_defs_size: int
    class_defs_offset: int
    data_size: int
    data_offset: int


@dataclass(frozen=True, slots=True)
class DexMethod:
    class_index: int
    prototype_index: int
    name_index: int


@dataclass(frozen=True, slots=True)
class DexField:
    class_index: int
    type_index: int
    name_index: int


@dataclass(frozen=True, slots=True)
class DexPrototype:
    return_type_index: int
    parameter_type_indexes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DexClass:
    class_index: int
    access_flags: int
    superclass_index: int
    interfaces_offset: int
    source_file_index: int
    annotations_offset: int
    class_data_offset: int
    static_values_offset: int


@dataclass(frozen=True, slots=True)
class EncodedMethod:
    method_index: int
    access_flags: int
    code_offset: int


@dataclass(frozen=True, slots=True)
class CodeItem:
    offset: int
    registers_size: int
    ins_size: int
    outs_size: int
    tries_size: int
    debug_info_offset: int
    instructions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class AnnotationItem:
    visibility: int
    type_index: int
    elements: tuple[tuple[int, object], ...]


# dalvik.annotation.EnclosingClass carries a single "value" element of type
# VALUE_TYPE naming the type index of the class this one is nested inside.
_ENCLOSING_CLASS_TYPE = "Ldalvik/annotation/EnclosingClass;"

_VALUE_BYTE = 0x00
_VALUE_SHORT = 0x02
_VALUE_CHAR = 0x03
_VALUE_INT = 0x04
_VALUE_LONG = 0x06
_VALUE_FLOAT = 0x10
_VALUE_DOUBLE = 0x11
_VALUE_METHOD_TYPE = 0x15
_VALUE_METHOD_HANDLE = 0x16
_VALUE_STRING = 0x17
_VALUE_TYPE = 0x18
_VALUE_FIELD = 0x19
_VALUE_METHOD = 0x1A
_VALUE_ENUM = 0x1B
_VALUE_ARRAY = 0x1C
_VALUE_ANNOTATION = 0x1D
_VALUE_NULL = 0x1E
_VALUE_BOOLEAN = 0x1F
_SIZED_VALUE_TYPES = frozenset(
    {
        _VALUE_BYTE,
        _VALUE_SHORT,
        _VALUE_CHAR,
        _VALUE_INT,
        _VALUE_LONG,
        _VALUE_FLOAT,
        _VALUE_DOUBLE,
        _VALUE_METHOD_TYPE,
        _VALUE_METHOD_HANDLE,
        _VALUE_STRING,
        _VALUE_TYPE,
        _VALUE_FIELD,
        _VALUE_METHOD,
        _VALUE_ENUM,
    }
)


class DexFile:
    NO_INDEX = 0xFFFFFFFF

    def __init__(self, data: bytes | bytearray | memoryview) -> None:
        self._data = memoryview(data)
        self.header = self._parse_header()
        self.strings = self._parse_strings()
        self.type_ids = self._uint_table(
            self.header.type_ids_offset, self.header.type_ids_size
        )
        self.prototypes = self._parse_prototypes()
        self.fields = self._parse_fields()
        self.methods = self._parse_methods()
        self.classes = self._parse_classes()

    @classmethod
    def from_path(cls, path: str | Path) -> DexFile:
        return cls(Path(path).read_bytes())

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(self.strings[index] for index in self.type_ids)

    def class_static_fields(self, item: DexClass) -> tuple[DexField, ...]:
        if item.class_data_offset == 0:
            return ()
        cursor = item.class_data_offset
        static_count, cursor = self._uleb128(cursor)
        _, cursor = self._uleb128(cursor)  # instance_fields_size
        _, cursor = self._uleb128(cursor)  # direct_methods_size
        _, cursor = self._uleb128(cursor)  # virtual_methods_size
        result: list[DexField] = []
        field_index = 0
        for _ in range(static_count):
            index_delta, cursor = self._uleb128(cursor)
            _, cursor = self._uleb128(cursor)
            field_index += index_delta
            if field_index >= len(self.fields):
                raise DexError("encoded field index is out of range")
            result.append(self.fields[field_index])
        return tuple(result)

    def static_field_values(self, item: DexClass) -> tuple[object, ...]:
        if item.static_values_offset == 0:
            return ()
        cursor = item.static_values_offset
        count, cursor = self._uleb128(cursor)
        values: list[object] = []
        for _ in range(count):
            value, cursor = self._encoded_value(cursor)
            values.append(value)
        return tuple(values)

    def class_methods(self, item: DexClass) -> tuple[EncodedMethod, ...]:
        if item.class_data_offset == 0:
            return ()
        cursor = item.class_data_offset
        static_count, cursor = self._uleb128(cursor)
        instance_count, cursor = self._uleb128(cursor)
        direct_count, cursor = self._uleb128(cursor)
        virtual_count, cursor = self._uleb128(cursor)
        for _ in range(static_count + instance_count):
            _, cursor = self._uleb128(cursor)
            _, cursor = self._uleb128(cursor)
        result: list[EncodedMethod] = []
        for count in (direct_count, virtual_count):
            method_index = 0
            for _ in range(count):
                index_delta, cursor = self._uleb128(cursor)
                flags, cursor = self._uleb128(cursor)
                code_offset, cursor = self._uleb128(cursor)
                method_index += index_delta
                if method_index >= len(self.methods):
                    raise DexError("encoded method index is out of range")
                result.append(EncodedMethod(method_index, flags, code_offset))
        return tuple(result)

    def code_item(self, offset: int) -> CodeItem:
        if offset == 0:
            raise DexError("method has no code item")
        registers, ins, outs, tries, debug_offset, count = self._unpack(
            "<HHHHII", offset
        )
        instruction_offset = offset + 16
        raw = self._slice(instruction_offset, int(count) * 2)
        instructions = struct.unpack_from(f"<{int(count)}H", raw) if count else ()
        # Validate the start of try/catch data too; callers can safely scan past insns.
        tail = instruction_offset + int(count) * 2
        if tries and count & 1:
            tail += 2
        self._slice(tail, int(tries) * 8)
        return CodeItem(
            offset,
            int(registers),
            int(ins),
            int(outs),
            int(tries),
            int(debug_offset),
            tuple(instructions),
        )

    def iter_code_items(self) -> tuple[tuple[EncodedMethod, CodeItem], ...]:
        result: list[tuple[EncodedMethod, CodeItem]] = []
        for item in self.classes:
            for method in self.class_methods(item):
                if method.code_offset:
                    result.append((method, self.code_item(method.code_offset)))
        return tuple(result)

    def method_name(self, method: DexMethod | EncodedMethod) -> str:
        item = (
            self.methods[method.method_index]
            if isinstance(method, EncodedMethod)
            else method
        )
        return self.strings[item.name_index]

    def method_return_type(self, method: DexMethod | EncodedMethod) -> str:
        item = (
            self.methods[method.method_index]
            if isinstance(method, EncodedMethod)
            else method
        )
        return self.types[self.prototypes[item.prototype_index].return_type_index]

    def method_parameter_types(
        self, method: DexMethod | EncodedMethod
    ) -> tuple[str, ...]:
        item = (
            self.methods[method.method_index]
            if isinstance(method, EncodedMethod)
            else method
        )
        return tuple(
            self.types[index]
            for index in self.prototypes[item.prototype_index].parameter_type_indexes
        )

    def field_name(self, item: DexField) -> str:
        return self.strings[item.name_index]

    def class_by_type_index(self, type_index: int) -> DexClass | None:
        for item in self.classes:
            if item.class_index == type_index:
                return item
        return None

    def class_annotations(self, item: DexClass) -> tuple[AnnotationItem, ...]:
        if item.annotations_offset == 0:
            return ()
        (class_annotations_off,) = self._unpack("<I", item.annotations_offset)
        if class_annotations_off == 0:
            return ()
        (size,) = self._unpack("<I", int(class_annotations_off))
        offsets = self._unpack(f"<{int(size)}I", int(class_annotations_off) + 4)
        return tuple(self._annotation_item(int(offset)) for offset in offsets)

    def enclosing_class_index(self, item: DexClass) -> int | None:
        for annotation in self.class_annotations(item):
            if self.types[annotation.type_index] != _ENCLOSING_CLASS_TYPE:
                continue
            for name_index, value in annotation.elements:
                if self.strings[name_index] == "value" and isinstance(value, int):
                    return value
        return None

    def _annotation_item(self, offset: int) -> AnnotationItem:
        (visibility,) = self._unpack("<B", offset)
        type_index, cursor = self._uleb128(offset + 1)
        if type_index >= len(self.type_ids):
            raise DexError("annotation type index is out of range")
        element_count, cursor = self._uleb128(cursor)
        elements: list[tuple[int, object]] = []
        for _ in range(element_count):
            name_index, cursor = self._uleb128(cursor)
            if name_index >= len(self.strings):
                raise DexError("annotation element name is out of range")
            value, cursor = self._encoded_value(cursor)
            elements.append((name_index, value))
        return AnnotationItem(int(visibility), int(type_index), tuple(elements))

    def _encoded_value(self, offset: int) -> tuple[object, int]:
        (header,) = self._unpack("<B", offset)
        cursor = offset + 1
        value_type = header & 0x1F
        value_arg = header >> 5
        if value_type in _SIZED_VALUE_TYPES:
            width = value_arg + 1
            raw = bytes(self._slice(cursor, width))
            cursor += width
            index = int.from_bytes(raw, "little")
            return index, cursor
        if value_type == _VALUE_BOOLEAN:
            return bool(value_arg), cursor
        if value_type == _VALUE_NULL:
            return None, cursor
        if value_type == _VALUE_ARRAY:
            count, cursor = self._uleb128(cursor)
            values: list[object] = []
            for _ in range(count):
                item, cursor = self._encoded_value(cursor)
                values.append(item)
            return tuple(values), cursor
        if value_type == _VALUE_ANNOTATION:
            type_index, cursor = self._uleb128(cursor)
            count, cursor = self._uleb128(cursor)
            elements: list[tuple[int, object]] = []
            for _ in range(count):
                name_index, cursor = self._uleb128(cursor)
                item, cursor = self._encoded_value(cursor)
                elements.append((name_index, item))
            return AnnotationItem(0, int(type_index), tuple(elements)), cursor
        raise DexError(f"unsupported encoded_value type 0x{value_type:02x}")

    def _parse_header(self) -> DexHeader:
        if (
            len(self._data) < 112
            or bytes(self._data[:4]) != b"dex\n"
            or bytes(self._data[7:8]) != b"\x00"
        ):
            raise DexError("not a valid DEX header")
        version = bytes(self._data[4:7]).decode("ascii", errors="strict")
        values = self._unpack("<20I", 32)
        file_size, header_size, endian_tag = (
            int(values[0]),
            int(values[1]),
            int(values[2]),
        )
        if (
            header_size != 112
            or file_size != len(self._data)
            or endian_tag != 0x12345678
        ):
            raise DexError("invalid DEX size, header size, or byte order")
        return DexHeader(
            version,
            file_size,
            header_size,
            endian_tag,
            int(values[6]),
            int(values[7]),
            int(values[8]),
            int(values[9]),
            int(values[10]),
            int(values[11]),
            int(values[12]),
            int(values[13]),
            int(values[14]),
            int(values[15]),
            int(values[16]),
            int(values[17]),
            int(values[18]),
            int(values[19]),
        )

    def _parse_strings(self) -> tuple[str, ...]:
        offsets = self._uint_table(
            self.header.string_ids_offset, self.header.string_ids_size
        )
        result: list[str] = []
        for offset in offsets:
            utf16_size, cursor = self._uleb128(offset)
            end = cursor
            while end < len(self._data) and self._data[end] != 0:
                end += 1
            if end == len(self._data):
                raise DexError("unterminated string_data_item")
            raw = bytes(self._data[cursor:end])
            value = _decode_mutf8(raw)
            actual_size = len(value.encode("utf-16-le", errors="surrogatepass")) // 2
            if actual_size != utf16_size:
                raise DexError("string_data_item UTF-16 length mismatch")
            result.append(value)
        return tuple(result)

    def _parse_methods(self) -> tuple[DexMethod, ...]:
        result: list[DexMethod] = []
        for index in range(self.header.method_ids_size):
            class_index, prototype_index, name_index = self._unpack(
                "<HHI", self.header.method_ids_offset + index * 8
            )
            if (
                class_index >= len(self.type_ids)
                or prototype_index >= len(self.prototypes)
                or name_index >= len(self.strings)
            ):
                raise DexError("method identifier is out of range")
            result.append(
                DexMethod(int(class_index), int(prototype_index), int(name_index))
            )
        return tuple(result)

    def _parse_prototypes(self) -> tuple[DexPrototype, ...]:
        result: list[DexPrototype] = []
        for index in range(self.header.proto_ids_size):
            _, return_type_index, parameters_offset = self._unpack(
                "<III", self.header.proto_ids_offset + index * 12
            )
            if return_type_index >= len(self.type_ids):
                raise DexError("prototype return type is out of range")
            parameters: tuple[int, ...] = ()
            if parameters_offset:
                (count,) = self._unpack("<I", int(parameters_offset))
                raw = self._unpack(f"<{int(count)}H", int(parameters_offset) + 4)
                if any(item >= len(self.type_ids) for item in raw):
                    raise DexError("prototype parameter type is out of range")
                parameters = tuple(int(item) for item in raw)
            result.append(DexPrototype(int(return_type_index), parameters))
        return tuple(result)

    def _parse_fields(self) -> tuple[DexField, ...]:
        result: list[DexField] = []
        for index in range(self.header.field_ids_size):
            class_index, type_index, name_index = self._unpack(
                "<HHI", self.header.field_ids_offset + index * 8
            )
            if (
                class_index >= len(self.type_ids)
                or type_index >= len(self.type_ids)
                or name_index >= len(self.strings)
            ):
                raise DexError("field identifier is out of range")
            result.append(DexField(int(class_index), int(type_index), int(name_index)))
        return tuple(result)

    def _parse_classes(self) -> tuple[DexClass, ...]:
        result: list[DexClass] = []
        for index in range(self.header.class_defs_size):
            raw = self._unpack("<8I", self.header.class_defs_offset + index * 32)
            if raw[0] >= len(self.type_ids):
                raise DexError("class type identifier is out of range")
            result.append(DexClass(*(int(value) for value in raw)))
        return tuple(result)

    def _uint_table(self, offset: int, count: int) -> tuple[int, ...]:
        raw = self._slice(offset, count * 4)
        return tuple(struct.unpack_from(f"<{count}I", raw)) if count else ()

    def _uleb128(self, offset: int) -> tuple[int, int]:
        value = 0
        for shift in range(0, 35, 7):
            if offset >= len(self._data):
                raise DexError("truncated ULEB128 value")
            byte = int(self._data[offset])
            offset += 1
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                if shift == 28 and byte > 0x0F:
                    raise DexError("ULEB128 value exceeds 32 bits")
                return value, offset
        raise DexError("invalid ULEB128 value")

    def _unpack(self, fmt: str, offset: int) -> tuple[int, ...]:
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(self._data):
            raise DexError("DEX structure lies outside the file")
        return struct.unpack_from(fmt, self._data, offset)

    def _slice(self, offset: int, size: int) -> memoryview:
        if offset < 0 or size < 0 or offset + size > len(self._data):
            raise DexError("DEX range lies outside the file")
        return self._data[offset : offset + size]


def _decode_mutf8(raw: bytes) -> str:
    # DEX uses Java's NUL encoding and permits UTF-16 surrogate code units.
    cooked = raw.replace(b"\xc0\x80", b"\x00")
    try:
        return cooked.decode("utf-8", errors="surrogatepass")
    except UnicodeDecodeError as error:
        raise DexError("invalid modified UTF-8 string") from error

from dataclasses import dataclass

from protoloom.decode.infostring import ONEOF_TYPE_OFFSET


@dataclass(frozen=True, slots=True)
class FieldType:
    id: int
    name: str
    proto_type: str
    label: str
    wire_type: int
    packed: bool = False
    oneof: bool = False


_SCALARS = (
    ("double", "double", 1),
    ("float", "float", 5),
    ("int64", "int64", 0),
    ("uint64", "uint64", 0),
    ("int32", "int32", 0),
    ("fixed64", "fixed64", 1),
    ("fixed32", "fixed32", 5),
    ("bool", "bool", 0),
    ("string", "string", 2),
    ("message", "message", 2),
    ("bytes", "bytes", 2),
    ("uint32", "uint32", 0),
    ("enum", "enum", 0),
    ("sfixed32", "sfixed32", 5),
    ("sfixed64", "sfixed64", 1),
    ("sint32", "sint32", 0),
    ("sint64", "sint64", 0),
    ("group", "group", 3),
)


def _build_types() -> dict[int, FieldType]:
    types = {
        index: FieldType(index, name.upper(), proto, "optional", wire)
        for index, (name, proto, wire) in enumerate(_SCALARS)
    }
    for index, (name, proto, wire) in enumerate(_SCALARS, 18):
        if index > 34:
            break
        types[index] = FieldType(index, f"{name.upper()}_LIST", proto, "repeated", wire)
    packable = tuple(_SCALARS[index] for index in (*range(8), *range(11, 17)))
    for index, (name, proto, _) in enumerate(packable, 35):
        types[index] = FieldType(
            index, f"{name.upper()}_LIST_PACKED", proto, "repeated", 2, packed=True
        )
    types[49] = FieldType(49, "GROUP_LIST", "group", "repeated", 3)
    types[50] = FieldType(50, "MAP", "map", "repeated", 2)
    for index, (name, proto, wire) in enumerate(_SCALARS, ONEOF_TYPE_OFFSET):
        types[index] = FieldType(
            index, f"ONEOF_{name.upper()}", proto, "optional", wire, oneof=True
        )
    return types


FIELD_TYPES = _build_types()


def field_type(type_id: int) -> FieldType:
    try:
        return FIELD_TYPES[type_id]
    except KeyError as error:
        raise ValueError(f"unknown protobuf-lite field type {type_id}") from error

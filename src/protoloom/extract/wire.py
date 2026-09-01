from dataclasses import dataclass

from protoloom.container.dex import AnnotationItem, DexField, DexFile

_MESSAGE = "Lcom/squareup/wire/Message;"
_WIRE_FIELD = "Lcom/squareup/wire/WireField;"
_SCALARS = {
    "BOOL": "bool",
    "BYTES": "bytes",
    "DOUBLE": "double",
    "FIXED32": "fixed32",
    "FIXED64": "fixed64",
    "FLOAT": "float",
    "INT32": "int32",
    "INT64": "int64",
    "SFIXED32": "sfixed32",
    "SFIXED64": "sfixed64",
    "SINT32": "sint32",
    "SINT64": "sint64",
    "STRING": "string",
    "UINT32": "uint32",
    "UINT64": "uint64",
}


@dataclass(frozen=True, slots=True)
class WireFieldFinding:
    owner: str
    field: DexField
    number: int
    adapter: str
    label: str
    oneof: str | None


def _elements(dex: DexFile, annotation: AnnotationItem) -> dict[str, object]:
    return {dex.strings[name]: value for name, value in annotation.elements}


def _string(dex: DexFile, value: object) -> str | None:
    if isinstance(value, int) and 0 <= value < len(dex.strings):
        return dex.strings[value]
    return None


def _label(dex: DexFile, value: object) -> str:
    if isinstance(value, int) and 0 <= value < len(dex.fields):
        name = dex.field_name(dex.fields[value]).lower()
        if name in {"optional", "required", "repeated", "packed"}:
            return "repeated" if name == "packed" else name
    return "optional"


def wire_adapter_type(adapter: str) -> str | None:
    owner, separator, member = adapter.partition("#")
    if not separator:
        return None
    scalar = _SCALARS.get(member)
    if scalar is not None:
        return scalar
    if member != "ADAPTER":
        return None
    return f".{owner.replace('$', '.')}"


def extract_wire_annotations(dex: DexFile) -> tuple[WireFieldFinding, ...]:
    types = dex.types
    findings = []
    for item in dex.classes:
        if item.superclass_index == dex.NO_INDEX:
            continue
        if types[item.superclass_index] != _MESSAGE:
            continue
        owner = types[item.class_index]
        for field, annotations in dex.field_annotations(item):
            annotation = next(
                (
                    value
                    for value in annotations
                    if types[value.type_index] == _WIRE_FIELD
                ),
                None,
            )
            if annotation is None:
                continue
            values = _elements(dex, annotation)
            number = values.get("tag")
            adapter = _string(dex, values.get("adapter"))
            if not isinstance(number, int) or adapter is None:
                continue
            findings.append(
                WireFieldFinding(
                    owner,
                    field,
                    number,
                    adapter,
                    _label(dex, values.get("label")),
                    _string(dex, values.get("oneofName")),
                )
            )
    return tuple(findings)

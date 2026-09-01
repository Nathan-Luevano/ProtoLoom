from dataclasses import dataclass

from protoloom.container.dex import AnnotationItem, DexField, DexFile, EncodedMethod
from protoloom.extract.lite import _Instruction, _instructions, _invoke_registers

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


@dataclass(frozen=True, slots=True)
class WireAdapterFinding:
    owner: str
    field: DexField
    number: int
    adapter: DexField
    method_index: int
    instruction_offset: int


def _constant(instruction: _Instruction) -> tuple[int, int] | None:
    opcode = instruction.opcode
    units = instruction.units
    if opcode == 0x12:
        register = (units[0] >> 8) & 0xF
        value = units[0] >> 12
        return register, value - 16 if value & 8 else value
    if opcode == 0x13:
        value = units[1]
        return units[0] >> 8, value - 65536 if value & 0x8000 else value
    if opcode == 0x14:
        value = units[1] | units[2] << 16
        return units[0] >> 8, value - 2**32 if value & 0x80000000 else value
    return None


def _method_writes(dex: DexFile, method: EncodedMethod) -> list[WireAdapterFinding]:
    registers: dict[int, DexField | int] = {}
    findings = []
    for instruction in _instructions(dex.code_item(method.code_offset).instructions):
        units = instruction.units
        if 0x52 <= instruction.opcode <= 0x58 and units[1] < len(dex.fields):
            registers[(units[0] >> 8) & 0xF] = dex.fields[units[1]]
            continue
        if 0x60 <= instruction.opcode <= 0x66 and units[1] < len(dex.fields):
            registers[units[0] >> 8] = dex.fields[units[1]]
            continue
        constant = _constant(instruction)
        if constant is not None:
            registers[constant[0]] = constant[1]
            continue
        if instruction.opcode not in {*range(0x6E, 0x73), *range(0x74, 0x79)}:
            continue
        target = dex.methods[units[1]]
        parameters = dex.method_parameter_types(target)
        arguments = _invoke_registers(instruction)
        if parameters[-2:] != ("I", "Ljava/lang/Object;") or len(arguments) != 4:
            continue
        adapter = registers.get(arguments[0])
        number = registers.get(arguments[2])
        field = registers.get(arguments[3])
        if not isinstance(adapter, DexField) or not isinstance(field, DexField):
            continue
        if not isinstance(number, int) or adapter.class_index == field.class_index:
            continue
        findings.append(
            WireAdapterFinding(
                dex.types[field.class_index],
                field,
                number,
                adapter,
                method.method_index,
                instruction.offset,
            )
        )
    return findings


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

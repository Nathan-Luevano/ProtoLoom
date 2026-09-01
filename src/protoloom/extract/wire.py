from dataclasses import dataclass
from typing import TypeVar

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
_RegisterValue = TypeVar("_RegisterValue")


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
    label: str = "optional"
    packed: bool = False


@dataclass(frozen=True, slots=True)
class _AdapterValue:
    field: DexField
    label: str = "optional"
    packed: bool = False


@dataclass(frozen=True, slots=True)
class WireNameFinding:
    owner: str
    field: DexField
    name: str
    message_name: str | None


@dataclass(frozen=True, slots=True)
class WireEnumFinding:
    descriptor: str
    values: tuple[tuple[str, int], ...]
    method_index: int


def _move_register(
    registers: dict[int, _RegisterValue], instruction: _Instruction
) -> bool:
    units = instruction.units
    if instruction.opcode in {0x01, 0x04, 0x07}:
        destination = (units[0] >> 8) & 0xF
        source = (units[0] >> 12) & 0xF
    elif instruction.opcode in {0x02, 0x05, 0x08}:
        destination, source = units[0] >> 8, units[1]
    elif instruction.opcode in {0x03, 0x06, 0x09}:
        destination, source = units[1], units[2]
    else:
        return False
    if source in registers:
        registers[destination] = registers[source]
    return True


def extract_wire_names(dex: DexFile) -> tuple[WireNameFinding, ...]:
    findings = []
    for item in dex.classes:
        owner = dex.types[item.class_index]
        for method in dex.class_methods(item):
            if dex.method_name(method) != "toString" or not method.code_offset:
                continue
            instructions = _instructions(dex.code_item(method.code_offset).instructions)
            message_name = next(
                (
                    dex.strings[instruction.units[1]].removesuffix("{")
                    for instruction in instructions
                    if instruction.opcode == 0x1A
                    and dex.strings[instruction.units[1]].endswith("{")
                ),
                None,
            )
            for index, instruction in enumerate(instructions):
                if instruction.opcode != 0x1A:
                    continue
                value = dex.strings[instruction.units[1]]
                if not value.endswith("="):
                    continue
                nearby = (
                    *reversed(instructions[max(0, index - 2) : index]),
                    *instructions[index + 1 : index + 5],
                )
                field_instruction = next(
                    (item for item in nearby if 0x52 <= item.opcode <= 0x58), None
                )
                if field_instruction is not None:
                    findings.append(
                        WireNameFinding(
                            owner,
                            dex.fields[field_instruction.units[1]],
                            value[:-1],
                            message_name,
                        )
                    )
    return tuple(findings)


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


def _wire_enum_method(
    dex: DexFile, method: EncodedMethod, descriptor: str
) -> WireEnumFinding | None:
    registers: dict[int, object] = {}
    recovered: dict[str, int] = {}
    for instruction in _instructions(dex.code_item(method.code_offset).instructions):
        units = instruction.units
        if _move_register(registers, instruction):
            continue
        constant = _constant(instruction)
        if constant is not None:
            registers[constant[0]] = constant[1]
        elif instruction.opcode in {0x1A, 0x1B}:
            index = (
                units[1] if instruction.opcode == 0x1A else units[1] | units[2] << 16
            )
            registers[units[0] >> 8] = dex.strings[index]
        elif instruction.opcode == 0x22 and dex.types[units[1]] == descriptor:
            registers[(units[0] >> 8) & 0xF] = ("instance", descriptor)
        elif instruction.opcode == 0x70:
            target = dex.methods[units[1]]
            if dex.method_name(target) != "<init>":
                continue
            parameters = dex.method_parameter_types(target)
            arguments = _invoke_registers(instruction)
            if len(arguments) != 4 or registers.get(arguments[0]) != (
                "instance",
                descriptor,
            ):
                continue
            positions = (
                (3, 1) if parameters == ("I", "I", "Ljava/lang/String;") else (1, 3)
            )
            name, number = (registers.get(arguments[index]) for index in positions)
            if isinstance(name, str) and isinstance(number, int):
                registers[arguments[0]] = ("enum", name, number)
        elif instruction.opcode == 0x69:
            value = registers.get(units[0] >> 8)
            field = dex.fields[units[1]]
            if (
                field.class_index == field.type_index
                and isinstance(value, tuple)
                and len(value) == 3
                and value[0] == "enum"
            ):
                recovered[str(value[1])] = int(value[2])
    if not recovered:
        return None
    return WireEnumFinding(descriptor, tuple(recovered.items()), method.method_index)


def _method_writes(dex: DexFile, method: EncodedMethod) -> list[WireAdapterFinding]:
    registers: dict[int, DexField | int | _AdapterValue] = {}
    findings = []
    pending: _AdapterValue | None = None
    for instruction in _instructions(dex.code_item(method.code_offset).instructions):
        units = instruction.units
        if _move_register(registers, instruction):
            continue
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
        if instruction.opcode == 0x0C and pending is not None:
            registers[units[0] >> 8] = pending
            pending = None
            continue
        if instruction.opcode not in {*range(0x6E, 0x73), *range(0x74, 0x79)}:
            continue
        target = dex.methods[units[1]]
        parameters = dex.method_parameter_types(target)
        arguments = _invoke_registers(instruction)
        target_name = dex.method_name(target)
        if target_name in {"asPacked", "asRepeated"} and len(arguments) == 1:
            value = registers.get(arguments[0])
            if isinstance(value, DexField):
                value = _AdapterValue(value)
            if isinstance(value, _AdapterValue):
                pending = _AdapterValue(
                    value.field, "repeated", target_name == "asPacked"
                )
            continue
        if parameters[-2:] != ("I", "Ljava/lang/Object;") or len(arguments) != 4:
            continue
        adapter = registers.get(arguments[0])
        number = registers.get(arguments[2])
        field = registers.get(arguments[3])
        if isinstance(adapter, DexField):
            adapter = _AdapterValue(adapter)
        if not isinstance(adapter, _AdapterValue) or not isinstance(field, DexField):
            continue
        if (
            not isinstance(number, int)
            or adapter.field.class_index == field.class_index
        ):
            continue
        findings.append(
            WireAdapterFinding(
                dex.types[field.class_index],
                field,
                number,
                adapter.field,
                method.method_index,
                instruction.offset,
                adapter.label,
                adapter.packed,
            )
        )
    return findings


def extract_wire_adapter_writes(dex: DexFile) -> tuple[WireAdapterFinding, ...]:
    findings = []
    types = dex.types
    adapter_bases = {
        method.class_index
        for method in dex.methods
        if tuple(
            types[index]
            for index in dex.prototypes[method.prototype_index].parameter_type_indexes
        )[-2:]
        == ("I", "Ljava/lang/Object;")
        and types[dex.prototypes[method.prototype_index].return_type_index] == "V"
    }
    for item in dex.classes:
        if item.superclass_index not in adapter_bases:
            continue
        methods = dex.class_methods(item)
        signatures = []
        for method in methods:
            raw = dex.methods[method.method_index]
            prototype = dex.prototypes[raw.prototype_index]
            parameters = tuple(
                types[index] for index in prototype.parameter_type_indexes
            )
            signatures.append((parameters, types[prototype.return_type_index]))
        for method, (parameters, result) in zip(methods, signatures, strict=True):
            if method.code_offset == 0:
                continue
            if len(parameters) != 2 or result != "V":
                continue
            try:
                writes = _method_writes(dex, method)
            except (IndexError, ValueError):
                continue
            findings.extend(writes)
    unique = {
        (item.owner, item.field.name_index, item.number): item for item in findings
    }
    return tuple(unique[key] for key in sorted(unique))


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

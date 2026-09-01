import re
from dataclasses import dataclass
from typing import TypeVar

from protoloom.container.dex import AnnotationItem, DexField, DexFile, EncodedMethod
from protoloom.extract.lite import (
    _branch_target,
    _Instruction,
    _instructions,
    _invoke_registers,
)

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
    schema_index: int | None = None


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


@dataclass(frozen=True, slots=True)
class WireOneofFinding:
    owner: str
    fields: tuple[str, ...]
    method_index: int


_ONEOF_MESSAGE = re.compile(r"^At most one of (.+) may be non-null$")
_DEFAULT_MARKER = "Lkotlin/jvm/internal/DefaultConstructorMarker;"
_NON_NULL = object()


def _parameter_registers(
    registers_size: int, ins_size: int, parameters: tuple[str, ...]
) -> tuple[int, ...]:
    register = registers_size - ins_size + 1
    result = []
    for parameter in parameters:
        result.append(register)
        register += 2 if parameter in {"D", "J"} else 1
    return tuple(result)


def _constructor_move(registers: dict[int, object], instruction: _Instruction) -> bool:
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
    else:
        registers.pop(destination, None)
    return True


def _constructor_and(registers: dict[int, object], instruction: _Instruction) -> bool:
    units = instruction.units
    if instruction.opcode == 0x95:
        destination = units[0] >> 8
        left, right = units[1] & 0xFF, units[1] >> 8
        operands = (registers.get(left), registers.get(right))
    elif instruction.opcode in {0xD5, 0xDD}:
        wide = instruction.opcode == 0xD5
        destination = (units[0] >> 8) & (0xF if wide else 0xFF)
        source = units[0] >> 12 if wide else units[1] & 0xFF
        literal = units[1] if wide else units[1] >> 8
        bits = 16 if wide else 8
        literal -= 1 << bits if literal & (1 << (bits - 1)) else 0
        operands = (registers.get(source), literal)
    else:
        return False
    left_value, right_value = operands
    if isinstance(left_value, int) and isinstance(right_value, int):
        registers[destination] = left_value & right_value
    else:
        registers.pop(destination, None)
    return True


def _constructor_value(registers: dict[int, object], instruction: _Instruction) -> bool:
    if _constructor_move(registers, instruction) or _constructor_and(
        registers, instruction
    ):
        return True
    constant = _constant(instruction)
    if constant is not None:
        registers[constant[0]] = constant[1]
        return True
    if instruction.opcode in {0x1A, 0x1B, 0x62}:
        registers[instruction.units[0] >> 8] = _NON_NULL
        return True
    return False


def _constructor_arguments(
    dex: DexFile,
    instruction: _Instruction,
    registers: dict[int, object],
) -> tuple[int, ...] | None:
    if instruction.opcode not in {0x70, 0x76}:
        return None
    method_index = instruction.units[1]
    if method_index >= len(dex.methods):
        return None
    target = dex.methods[method_index]
    parameters = dex.method_parameter_types(target)
    if dex.method_name(target) != "<init>" or parameters[-1:] == (_DEFAULT_MARKER,):
        return None
    arguments = _invoke_registers(instruction)
    cursor = 1
    nulls = []
    for index, parameter in enumerate(parameters):
        if cursor >= len(arguments):
            return None
        reference = parameter.startswith("L") or parameter.startswith("[")
        if reference and registers.get(arguments[cursor]) == 0:
            nulls.append(index)
        cursor += 2 if parameter in {"D", "J"} else 1
    return tuple(nulls)


def _default_constructor_state(
    dex: DexFile, method: EncodedMethod
) -> tuple[tuple[_Instruction, ...], dict[int, object]] | None:
    parameters = dex.method_parameter_types(method)
    if parameters[-1:] != (_DEFAULT_MARKER,):
        return None
    code = dex.code_item(method.code_offset)
    parameter_registers = _parameter_registers(
        code.registers_size, code.ins_size, parameters
    )
    registers: dict[int, object] = {}
    for index in range(len(parameters) - 2, -1, -1):
        if parameters[index] != "I":
            break
        registers[parameter_registers[index]] = -1
    return _instructions(code.instructions), registers


def _constructor_next(
    instruction: _Instruction,
    registers: dict[int, object],
    by_offset: dict[int, int],
    index: int,
) -> int | None:
    if instruction.opcode == 0x38:
        value = registers.get(instruction.units[0] >> 8)
        if not isinstance(value, int):
            return None
        if value != 0:
            return index + 1
        target = _branch_target(instruction)
        return by_offset.get(target) if target is not None else None
    if instruction.opcode in {0x28, 0x29, 0x2A}:
        target = _branch_target(instruction)
        return by_offset.get(target) if target is not None else None
    return index + 1


def extract_wire_messages(dex: DexFile) -> tuple[str, ...]:
    return tuple(
        dex.types[item.class_index]
        for item in dex.classes
        if item.superclass_index != dex.NO_INDEX
        and dex.types[item.superclass_index] == _MESSAGE
    )


def extract_wire_oneofs(dex: DexFile, owners: set[str]) -> tuple[WireOneofFinding, ...]:
    result = []
    for item in dex.classes:
        owner = dex.types[item.class_index]
        if owner not in owners:
            continue
        for method in dex.class_methods(item):
            if dex.method_name(method) != "<init>" or not method.code_offset:
                continue
            for instruction in _instructions(
                dex.code_item(method.code_offset).instructions
            ):
                if instruction.opcode != 0x1A:
                    continue
                match = _ONEOF_MESSAGE.fullmatch(dex.strings[instruction.units[1]])
                if match is not None:
                    fields = tuple(match.group(1).split(", "))
                    if len(fields) > 1:
                        result.append(
                            WireOneofFinding(owner, fields, method.method_index)
                        )
    return tuple(result)


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


def extract_wire_syntaxes(dex: DexFile, owners: set[str]) -> dict[str, str]:
    result = {}
    for item in dex.classes:
        owner = dex.types[item.class_index]
        if owner not in owners:
            continue
        values = set()
        for method in dex.class_methods(item):
            if dex.method_name(method) != "<clinit>" or not method.code_offset:
                continue
            for instruction in _instructions(
                dex.code_item(method.code_offset).instructions
            ):
                if not 0x60 <= instruction.opcode <= 0x66:
                    continue
                field = dex.fields[instruction.units[1]]
                if dex.types[field.class_index] == "Lcom/squareup/wire/Syntax;":
                    values.add(dex.field_name(field).lower().replace("_", ""))
        if len(values) == 1:
            value = values.pop()
            if value in {"proto2", "proto3"}:
                result[owner] = value
    return result


def _constant(instruction: _Instruction) -> tuple[int, int] | None:
    opcode = instruction.opcode
    units = instruction.units
    if opcode == 0x12:
        register = (units[0] >> 8) & 0xF
        value = units[0] >> 12
        return register, value - 16 if value & 8 else value
    if opcode in {0x13, 0x15}:
        value = units[1]
        value = value - 65536 if value & 0x8000 else value
        return units[0] >> 8, value << (16 if opcode == 0x15 else 0)
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
            if parameters == ("I", "I", "Ljava/lang/String;"):
                positions = (3, 2)
            elif parameters == ("Ljava/lang/String;", "I", "I"):
                positions = (1, 3)
            elif parameters == ("Ljava/lang/String;", "I"):
                positions = (1, 2)
            else:
                continue
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


def extract_wire_enums(
    dex: DexFile,
    findings: tuple[WireAdapterFinding, ...],
    annotations: tuple[WireFieldFinding, ...] = (),
) -> tuple[WireEnumFinding, ...]:
    result = []
    descriptors = {
        f"L{item.adapter.partition('#')[0].replace('.', '/')};"
        for item in annotations
        if item.adapter.endswith("#ADAPTER")
    }
    type_indexes = {item.adapter.class_index for item in findings}
    type_indexes.update(
        index for index, descriptor in enumerate(dex.types) if descriptor in descriptors
    )
    model_packages = {item.owner.rsplit("/", 1)[0] for item in findings}
    model_packages.update(item.owner.rsplit("/", 1)[0] for item in annotations)
    for item in dex.classes:
        descriptor = dex.types[item.class_index]
        if descriptor.rsplit("/", 1)[0] not in model_packages:
            continue
        if any(
            dex.method_name(method) == "getValue"
            and not dex.method_parameter_types(method)
            and dex.method_return_type(method) == "I"
            for method in dex.class_methods(item)
        ):
            type_indexes.add(item.class_index)
    for type_index in sorted(type_indexes):
        enum_class = dex.class_by_type_index(type_index)
        if enum_class is None or enum_class.superclass_index == dex.NO_INDEX:
            continue
        if dex.types[enum_class.superclass_index] != "Ljava/lang/Enum;":
            continue
        descriptor = dex.types[type_index]
        initializer = next(
            (
                method
                for method in dex.class_methods(enum_class)
                if dex.method_name(method) == "<clinit>" and method.code_offset
            ),
            None,
        )
        if initializer is None:
            continue
        finding = _wire_enum_method(dex, initializer, descriptor)
        if finding is not None:
            result.append(finding)
    return tuple(result)


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
            schema_index = values.get("schemaIndex")
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
                    schema_index if isinstance(schema_index, int) else None,
                )
            )
    return tuple(findings)

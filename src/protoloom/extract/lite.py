from dataclasses import dataclass
from typing import Any

from protoloom.container.dex import CodeItem, DexFile, EncodedMethod
from protoloom.decode.infostring import InfoStringError, decode_info_string


@dataclass(frozen=True, slots=True)
class LiteObject:
    kind: str
    value: str | int


@dataclass(frozen=True, slots=True)
class LiteFinding:
    containing_method: int
    code_offset: int
    instruction_offset: int
    info_string: str
    objects: tuple[LiteObject, ...]
    heuristic: bool = False


@dataclass(frozen=True, slots=True)
class LiteBailout:
    containing_method: int
    instruction_offset: int
    reason: str


@dataclass(frozen=True, slots=True)
class LiteEnumEvidence:
    descriptor: str
    values: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class LiteExtraction:
    findings: tuple[LiteFinding, ...]
    bailouts: tuple[LiteBailout, ...]

    @property
    def bailout_count(self) -> int:
        return len(self.bailouts)


@dataclass(slots=True)
class _Array:
    size: int | None
    values: dict[int, LiteObject]
    heuristic: bool = False


@dataclass(frozen=True, slots=True)
class _Instruction:
    offset: int
    opcode: int
    units: tuple[int, ...]


_ONE_UNIT = {
    *range(0x0A, 0x13),
    0x00,
    0x01,
    0x04,
    0x07,
    0x1D,
    0x1E,
    0x21,
    0x27,
    0x28,
    *range(0x7B, 0x90),
    *range(0xB0, 0xD0),
}
_TWO_UNIT = {
    0x02,
    0x05,
    0x08,
    0x13,
    0x15,
    0x16,
    0x19,
    0x1A,
    0x1C,
    0x1F,
    0x20,
    0x22,
    0x23,
    0x29,
    *range(0x2D, 0x3E),
    *range(0x44, 0x6E),
    *range(0x90, 0xB0),
    *range(0xD0, 0xE3),
    0xFE,
    0xFF,
}
_THREE_UNIT = {
    0x03,
    0x06,
    0x09,
    0x14,
    0x17,
    0x1B,
    0x24,
    0x25,
    0x26,
    0x2A,
    0x2B,
    0x2C,
    *range(0x6E, 0x79),
    0xFC,
    0xFD,
}
_BRANCHES = {0x28, 0x29, 0x2A, *range(0x32, 0x3E), 0x2B, 0x2C}


def extract_lite(dex: DexFile, *, allow_heuristic: bool = False) -> LiteExtraction:
    findings: list[LiteFinding] = []
    bailouts: list[LiteBailout] = []
    targets = {
        index
        for index, method in enumerate(dex.methods)
        if dex.method_name(method) == "newMessageInfo"
    }
    targets.update(_raw_info_wrappers(dex))
    for method, code in dex.iter_code_items():
        found, failed = _scan_method(dex, method, code, targets, allow_heuristic)
        findings.extend(found)
        bailouts.extend(failed)
    return LiteExtraction(tuple(findings), tuple(bailouts))


def _raw_info_wrappers(dex: DexFile) -> set[int]:
    wrappers: set[int] = set()
    for method, code in dex.iter_code_items():
        instructions = _instructions(code.instructions)
        if len(instructions) != 3 or [item.opcode for item in instructions] != [
            0x22,
            0x70,
            0x11,
        ]:
            continue
        invocation = instructions[1]
        arguments = _invoke_registers(invocation)
        incoming = code.registers_size - code.ins_size
        target_index = invocation.units[1]
        if target_index >= len(dex.methods):
            continue
        constructor = dex.methods[target_index]
        if constructor.class_index >= len(dex.types):
            continue
        constructor_type = dex.types[constructor.class_index]
        parameters = (
            dex.method_parameter_types(constructor)
            if hasattr(dex, "method_parameter_types")
            else ()
        )
        raw_info_shape = len(parameters) == 3 and parameters[1:] == (
            "Ljava/lang/String;",
            "[Ljava/lang/Object;",
        )
        if (
            code.ins_size == 3
            and len(arguments) == 4
            and arguments[1:] == tuple(range(incoming, incoming + 3))
            and (constructor_type.endswith("/RawMessageInfo;") or raw_info_shape)
            and dex.method_name(constructor) == "<init>"
        ):
            wrappers.add(method.method_index)
    return wrappers


def recover_enum_evidence(
    dex: DexFile, owner_descriptor: str, java_field_name: str
) -> LiteEnumEvidence | None:
    owner_indexes = {
        index
        for index, descriptor in enumerate(dex.types)
        if descriptor == owner_descriptor
    }
    base_name = java_field_name.rstrip("_")
    getter = f"get{base_name[:1].upper()}{base_name[1:]}"
    return_types = {
        dex.method_return_type(method)
        for method in dex.methods
        if method.class_index in owner_indexes and dex.method_name(method) == getter
    }
    if len(return_types) != 1:
        return None
    descriptor = return_types.pop()
    class_name = descriptor.removeprefix("L").removesuffix(";").rsplit("/", 1)[-1]
    if class_name.count("$") != 1:
        return None
    values = _enum_values(dex, descriptor)
    if not values or values[0][1] != 0:
        return None
    return LiteEnumEvidence(descriptor, values)


def _enum_values(dex: DexFile, descriptor: str) -> tuple[tuple[str, int], ...]:
    type_indexes = {
        index for index, value in enumerate(dex.types) if value == descriptor
    }
    recovered: dict[str, int] = {}
    for method, code in dex.iter_code_items():
        raw_method = dex.methods[method.method_index]
        if (
            raw_method.class_index not in type_indexes
            or dex.method_name(raw_method) != "<clinit>"
        ):
            continue
        registers: dict[int, Any] = {}
        for instruction in _instructions(code.instructions):
            opcode = instruction.opcode
            units = instruction.units
            if opcode == 0x12:
                destination = (units[0] >> 8) & 0xF
                value = (units[0] >> 12) & 0xF
                registers[destination] = value - 16 if value & 8 else value
            elif opcode in {0x13, 0x15}:
                destination = units[0] >> 8
                value = units[1]
                if value & 0x8000:
                    value -= 0x10000
                registers[destination] = value << (16 if opcode == 0x15 else 0)
            elif opcode == 0x14:
                destination = units[0] >> 8
                value = units[1] | units[2] << 16
                registers[destination] = (
                    value - 0x100000000 if value & 0x80000000 else value
                )
            elif opcode in {0x1A, 0x1B}:
                destination = units[0] >> 8
                index = units[1] if opcode == 0x1A else units[1] | units[2] << 16
                if index < len(dex.strings):
                    registers[destination] = dex.strings[index]
            elif opcode == 0x22:
                destination = (units[0] >> 8) & 0xF
                if units[1] < len(dex.types):
                    registers[destination] = ("instance", dex.types[units[1]])
            elif opcode == 0x70 and units[1] < len(dex.methods):
                arguments = _invoke_registers(instruction)
                if len(arguments) != 4:
                    continue
                instance, name, _, number = (
                    registers.get(register) for register in arguments
                )
                constructor = dex.methods[units[1]]
                if (
                    instance == ("instance", descriptor)
                    and isinstance(name, str)
                    and isinstance(number, int)
                    and dex.method_name(constructor) == "<init>"
                    and dex.types[constructor.class_index] == descriptor
                    and dex.method_parameter_types(constructor)
                    == ("Ljava/lang/String;", "I", "I")
                ):
                    registers[arguments[0]] = ("enum", descriptor, name, number)
            elif opcode == 0x69 and units[1] < len(dex.fields):
                item = dex.fields[units[1]]
                enum_instance = registers.get(units[0] >> 8)
                name = dex.field_name(item)
                if (
                    item.class_index in type_indexes
                    and isinstance(enum_instance, tuple)
                    and len(enum_instance) == 4
                    and enum_instance == ("enum", descriptor, name, enum_instance[3])
                    and name != "UNRECOGNIZED"
                ):
                    recovered[name] = int(enum_instance[3])
    return tuple(recovered.items())


def _scan_method(
    dex: DexFile,
    method: EncodedMethod,
    code: CodeItem,
    targets: set[int],
    allow_heuristic: bool,
) -> tuple[list[LiteFinding], list[LiteBailout]]:
    registers: dict[int, Any] = {}
    pending_result: Any = None
    findings: list[LiteFinding] = []
    bailouts: list[LiteBailout] = []
    for instruction in _instructions(code.instructions):
        opcode = instruction.opcode
        units = instruction.units
        if opcode in _BRANCHES:
            pending_result = None
            continue
        if opcode == 0x12:
            destination = (units[0] >> 8) & 0xF
            value = (units[0] >> 12) & 0xF
            registers[destination] = value - 16 if value & 8 else value
        elif opcode in {0x13, 0x15}:
            destination = units[0] >> 8
            value = units[1]
            if value & 0x8000:
                value -= 0x10000
            registers[destination] = value << (16 if opcode == 0x15 else 0)
        elif opcode == 0x14:
            destination = units[0] >> 8
            value = units[1] | units[2] << 16
            registers[destination] = (
                value - 0x100000000 if value & 0x80000000 else value
            )
        elif opcode in {0x07, 0x08, 0x09}:
            _move_object(registers, instruction)
        elif opcode == 0x0C:
            destination = units[0] >> 8
            registers[destination] = pending_result
            pending_result = None
        elif opcode in {0x1A, 0x1B}:
            destination = units[0] >> 8
            index = units[1] if opcode == 0x1A else units[1] | units[2] << 16
            registers[destination] = _pool_object("string", index, dex.strings)
        elif opcode == 0x1C:
            destination = units[0] >> 8
            registers[destination] = _pool_object("class", units[1], dex.types)
        elif opcode == 0x62:
            registers[units[0] >> 8] = LiteObject("static_field", units[1])
        elif opcode == 0x23:
            destination = (units[0] >> 8) & 0xF
            size_register = (units[0] >> 12) & 0xF
            size = registers.get(size_register)
            known_size = size if isinstance(size, int) and size >= 0 else None
            registers[destination] = _Array(
                known_size, {}, heuristic=known_size is None
            )
        elif opcode in {0x24, 0x25}:
            object_registers = _invoke_registers(instruction)
            values: dict[int, LiteObject] = {}
            for index, register in enumerate(object_registers):
                object_value = registers.get(register)
                if isinstance(object_value, LiteObject):
                    values[index] = object_value
            pending_result = _Array(len(object_registers), values)
        elif opcode == 0x4D:
            value_register = units[0] >> 8
            array_register = units[1] & 0xFF
            index_register = units[1] >> 8
            array = registers.get(array_register)
            stored_index = registers.get(index_register)
            stored_value = registers.get(value_register)
            if isinstance(array, _Array) and isinstance(stored_value, LiteObject):
                if (
                    isinstance(stored_index, int)
                    and stored_index >= 0
                    and (array.size is None or stored_index < array.size)
                ):
                    array.values[stored_index] = stored_value
                else:
                    size = (
                        array.size if array.size is not None else len(array.values) + 1
                    )
                    missing = next(
                        (index for index in range(size) if index not in array.values),
                        None,
                    )
                    if missing is not None:
                        array.values[missing] = stored_value
                        array.heuristic = True
        elif opcode in {0x70, 0x71, 0x76, 0x77}:
            target = units[1]
            if target >= len(dex.methods):
                bailouts.append(
                    LiteBailout(
                        method.method_index,
                        instruction.offset,
                        f"invoke references invalid method index {target}",
                    )
                )
                pending_result = None
                continue
            argument_registers = _invoke_registers(instruction)
            is_named_target = target in targets
            is_inlined_constructor = (
                dex.method_name(dex.methods[target]) == "<init>"
                and len(argument_registers) == 4
                and _register_is_info_string(registers, argument_registers[2])
            )
            if not is_named_target and not is_inlined_constructor:
                pending_result = LiteObject("call_result", target)
                continue
            if is_inlined_constructor:
                argument_registers = argument_registers[1:]
            finding, reason = _resolve_call(
                dex,
                method,
                code,
                instruction,
                argument_registers,
                registers,
                allow_heuristic,
            )
            if finding is not None:
                findings.append(finding)
                if finding.heuristic:
                    bailouts.append(
                        LiteBailout(
                            method.method_index,
                            instruction.offset,
                            "schema emitted with opt-in unresolved-order heuristics",
                        )
                    )
            else:
                bailouts.append(
                    LiteBailout(method.method_index, instruction.offset, reason)
                )
            registers.clear()
    return findings, bailouts


def _resolve_call(
    dex: DexFile,
    method: EncodedMethod,
    code: CodeItem,
    instruction: _Instruction,
    arguments: tuple[int, ...],
    registers: dict[int, Any],
    allow_heuristic: bool,
) -> tuple[LiteFinding | None, str]:
    if len(arguments) != 3:
        return None, f"newMessageInfo has {len(arguments)} registers, expected 3"
    info = registers.get(arguments[1])
    if not isinstance(info, LiteObject) or info.kind != "string":
        return None, "info string register is not a const-string"
    try:
        decoded = decode_info_string(str(info.value))
    except InfoStringError as error:
        return None, f"invalid info string: {error}"
    array = registers.get(arguments[2])
    if decoded.header.field_count == 0:
        return (
            LiteFinding(
                method.method_index,
                code.offset,
                instruction.offset,
                str(info.value),
                (),
                False,
            ),
            "",
        )
    if not isinstance(array, _Array):
        return None, "objects register is not a tracked new-array"
    size = array.size if array.size is not None else len(array.values)
    missing = [index for index in range(size) if index not in array.values]
    if missing:
        return None, f"objects array has unresolved indexes: {missing}"
    objects = tuple(array.values[index] for index in range(size))
    if array.heuristic and not allow_heuristic:
        return None, "objects array requires unresolved-order heuristics"
    return (
        LiteFinding(
            method.method_index,
            code.offset,
            instruction.offset,
            str(info.value),
            objects,
            array.heuristic,
        ),
        "",
    )


def _register_is_info_string(registers: dict[int, Any], register: int) -> bool:
    value = registers.get(register)
    if not isinstance(value, LiteObject) or value.kind != "string":
        return False
    try:
        decode_info_string(str(value.value))
    except InfoStringError:
        return False
    return True


def _pool_object(kind: str, index: int, values: tuple[str, ...]) -> LiteObject:
    if index >= len(values):
        return LiteObject(f"invalid_{kind}", index)
    return LiteObject(kind, values[index])


def _move_object(registers: dict[int, Any], instruction: _Instruction) -> None:
    units = instruction.units
    if instruction.opcode == 0x07:
        destination = (units[0] >> 8) & 0xF
        source = (units[0] >> 12) & 0xF
    elif instruction.opcode == 0x08:
        destination = units[0] >> 8
        source = units[1]
    else:
        destination = units[1]
        source = units[2]
    registers[destination] = registers.get(source)


def _invoke_registers(instruction: _Instruction) -> tuple[int, ...]:
    units = instruction.units
    if instruction.opcode in {0x25, 0x76, 0x77}:
        count = units[0] >> 8
        return tuple(range(units[2], units[2] + count))
    count = units[0] >> 12
    packed = units[2]
    registers = (
        packed & 0xF,
        (packed >> 4) & 0xF,
        (packed >> 8) & 0xF,
        (packed >> 12) & 0xF,
        (units[0] >> 8) & 0xF,
    )
    return registers[:count]


def _instructions(code: tuple[int, ...]) -> tuple[_Instruction, ...]:
    result = []
    offset = 0
    while offset < len(code):
        opcode = code[offset] & 0xFF
        width = _instruction_width(code, offset, opcode)
        if width <= 0 or offset + width > len(code):
            raise ValueError(f"truncated DEX instruction at code-unit {offset}")
        result.append(_Instruction(offset, opcode, code[offset : offset + width]))
        offset += width
    return tuple(result)


def _instruction_width(code: tuple[int, ...], offset: int, opcode: int) -> int:
    if opcode:
        if opcode in _ONE_UNIT:
            return 1
        if opcode in _TWO_UNIT:
            return 2
        if opcode in _THREE_UNIT:
            return 3
        if opcode == 0x18:
            return 5
        if opcode in {0xFA, 0xFB}:
            return 4
        raise ValueError(f"unsupported DEX opcode 0x{opcode:02x}")
    ident = code[offset]
    if ident == 0x0100:
        return 4 + code[offset + 1] * 2
    if ident == 0x0200:
        return 2 + code[offset + 1] * 4
    if ident == 0x0300:
        element_width = code[offset + 1]
        size = code[offset + 2] | code[offset + 3] << 16
        return 4 + (element_width * size + 1) // 2
    return 1

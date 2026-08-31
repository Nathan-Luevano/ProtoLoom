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
    code_offset: int
    instruction_offsets: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LiteMapEvidence:
    key_type: str
    value_type: str


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


def _enum_evidence_from_descriptor(
    dex: DexFile, descriptor: str
) -> LiteEnumEvidence | None:
    class_name = descriptor.removeprefix("L").removesuffix(";").rsplit("/", 1)[-1]
    if "$" not in class_name or any(not part for part in class_name.split("$")):
        return None
    values, code_offset, instruction_offsets = _enum_values(dex, descriptor)
    if not values:
        return None
    return LiteEnumEvidence(descriptor, values, code_offset, instruction_offsets)


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
    return _enum_evidence_from_descriptor(dex, return_types.pop())


def recover_enum_evidence_from_verifier(
    dex: DexFile, verifier_descriptor: str
) -> LiteEnumEvidence | None:
    # newMessageInfo's objects array carries a reference to the field's
    # EnumVerifier.INSTANCE singleton, not the enum type itself -- but the
    # verifier is always a nested class of the real enum
    # (CardType$CardTypeVerifier), so its enclosing class *is* the enum.
    # This survives even when R8 strips the getter/setter accessors that
    # recover_enum_evidence needs.
    normalized = verifier_descriptor.removeprefix("L").removesuffix(";")
    if "$" not in normalized:
        return None
    enum_descriptor = f"L{normalized.rsplit('$', 1)[0]};"
    return _enum_evidence_from_descriptor(dex, enum_descriptor)


def _enum_values(
    dex: DexFile, descriptor: str
) -> tuple[tuple[tuple[str, int], ...], int, tuple[int, ...]]:
    type_indexes = {
        index for index, value in enumerate(dex.types) if value == descriptor
    }
    recovered: dict[str, int] = {}
    initializer_offset = 0
    store_offsets: list[int] = []
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
                    initializer_offset = code.offset
                    store_offsets.append(instruction.offset)
    return tuple(recovered.items()), initializer_offset, tuple(store_offsets)


def recover_map_evidence(dex: DexFile, field_index: int) -> LiteMapEvidence | None:
    if field_index < 0 or field_index >= len(dex.fields):
        return None
    target = dex.fields[field_index]
    registers: dict[int, tuple[str, int]] = {}
    pending: LiteMapEvidence | None = None
    ready_to_store: tuple[int, LiteMapEvidence] | None = None
    scalar_names = {
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
    key_scalar_names = {
        "BOOL",
        "FIXED32",
        "FIXED64",
        "INT32",
        "INT64",
        "SFIXED32",
        "SFIXED64",
        "SINT32",
        "SINT64",
        "STRING",
        "UINT32",
        "UINT64",
    }
    for method, code in dex.iter_code_items():
        raw_method = dex.methods[method.method_index]
        if (
            raw_method.class_index != target.class_index
            or dex.method_name(raw_method) != "<clinit>"
        ):
            continue
        registers.clear()
        pending = None
        ready_to_store = None
        for instruction in _instructions(code.instructions):
            units = instruction.units
            if pending is not None and instruction.opcode != 0x0C:
                pending = None
            if ready_to_store is not None and instruction.opcode != 0x69:
                ready_to_store = None
            destination = _written_register(instruction)
            if destination is not None:
                registers.pop(destination, None)
            if instruction.opcode == 0x62 and units[1] < len(dex.fields):
                registers[units[0] >> 8] = ("field", units[1])
            elif instruction.opcode == 0x71:
                pending = None
                if units[1] >= len(dex.methods):
                    continue
                arguments = _invoke_registers(instruction)
                called = dex.methods[units[1]]
                parameters = dex.method_parameter_types(called)
                if (
                    len(arguments) == 4
                    and parameters
                    == (
                        "Lcom/google/protobuf/WireFormat$FieldType;",
                        "Ljava/lang/Object;",
                        "Lcom/google/protobuf/WireFormat$FieldType;",
                        "Ljava/lang/Object;",
                    )
                    and dex.method_name(called) == "newDefaultInstance"
                    and dex.types[called.class_index].endswith("/MapEntryLite;")
                    and dex.method_return_type(called).endswith("/MapEntryLite;")
                ):
                    key = registers.get(arguments[0])
                    value = registers.get(arguments[2])
                    if key is not None and value is not None:
                        key_field = dex.fields[key[1]]
                        value_field = dex.fields[value[1]]
                        if (
                            dex.types[key_field.type_index] != parameters[0]
                            or dex.types[value_field.type_index] != parameters[2]
                        ):
                            continue
                        names = (
                            dex.field_name(key_field),
                            dex.field_name(value_field),
                        )
                        if names[0] in key_scalar_names and names[1] in scalar_names:
                            pending = LiteMapEvidence(
                                scalar_names[names[0]], scalar_names[names[1]]
                            )
            elif instruction.opcode == 0x0C:
                destination = units[0] >> 8
                if pending is not None:
                    ready_to_store = (destination, pending)
                pending = None
            elif instruction.opcode == 0x69:
                if (
                    units[1] == field_index
                    and ready_to_store is not None
                    and units[0] >> 8 == ready_to_store[0]
                ):
                    return ready_to_store[1]
                ready_to_store = None
    return None


def _written_register(instruction: _Instruction) -> int | None:
    opcode = instruction.opcode
    if opcode in {0x01, 0x04, 0x07, 0x12, 0x21} or 0x7B <= opcode <= 0x8F:
        return (instruction.units[0] >> 8) & 0xF
    if 0xB0 <= opcode <= 0xCF or 0xD0 <= opcode <= 0xD7:
        return (instruction.units[0] >> 8) & 0xF
    if (
        0x02 <= opcode <= 0x03
        or 0x05 <= opcode <= 0x06
        or 0x08 <= opcode <= 0x0D
        or 0x13 <= opcode <= 0x1C
        or 0x1F <= opcode <= 0x20
        or 0x22 <= opcode <= 0x23
        or 0x2D <= opcode <= 0x31
        or 0x44 <= opcode <= 0x4A
        or 0x52 <= opcode <= 0x58
        or 0x60 <= opcode <= 0x66
        or 0x90 <= opcode <= 0xAF
        or 0xD8 <= opcode <= 0xE2
    ):
        return instruction.units[0] >> 8
    return None


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
    branch_states: dict[int, dict[int, Any]] = {}
    for instruction in _instructions(code.instructions):
        if instruction.offset in branch_states:
            registers = branch_states[instruction.offset].copy()
        opcode = instruction.opcode
        units = instruction.units
        if opcode in _BRANCHES:
            if opcode == 0x2B:
                for target in _packed_switch_targets(code.instructions, instruction):
                    branch_states[target] = registers.copy()
            else:
                branch_target = _branch_target(instruction)
                if branch_target is not None:
                    current = branch_states.get(branch_target)
                    branch_states[branch_target] = (
                        registers.copy()
                        if current is None
                        else {
                            key: value
                            for key, value in current.items()
                            if registers.get(key) == value
                        }
                    )
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


def _packed_switch_targets(
    code: tuple[int, ...], instruction: _Instruction
) -> tuple[int, ...]:
    delta = instruction.units[1] | instruction.units[2] << 16
    if delta & 0x80000000:
        delta -= 0x100000000
    payload = instruction.offset + delta
    if payload < 0 or payload + 4 > len(code) or code[payload] != 0x0100:
        return ()
    size = code[payload + 1]
    if payload + 4 + size * 2 > len(code):
        return ()
    targets = []
    for offset in range(payload + 4, payload + 4 + size * 2, 2):
        target = code[offset] | code[offset + 1] << 16
        if target & 0x80000000:
            target -= 0x100000000
        targets.append(instruction.offset + target)
    return tuple(targets)


def _branch_target(instruction: _Instruction) -> int | None:
    if instruction.opcode == 0x28:
        delta = instruction.units[0] >> 8
        if delta & 0x80:
            delta -= 0x100
    elif instruction.opcode in {0x29, *range(0x32, 0x3E)}:
        delta = instruction.units[1]
        if delta & 0x8000:
            delta -= 0x10000
    elif instruction.opcode == 0x2A:
        delta = instruction.units[1] | instruction.units[2] << 16
        if delta & 0x80000000:
            delta -= 0x100000000
    else:
        return None
    return instruction.offset + delta


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

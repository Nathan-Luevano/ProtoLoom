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


@dataclass(frozen=True, slots=True)
class LiteBailout:
    containing_method: int
    instruction_offset: int
    reason: str


@dataclass(frozen=True, slots=True)
class LiteExtraction:
    findings: tuple[LiteFinding, ...]
    bailouts: tuple[LiteBailout, ...]

    @property
    def bailout_count(self) -> int:
        return len(self.bailouts)


@dataclass(slots=True)
class _Array:
    size: int
    values: dict[int, LiteObject]


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
    0x2A,
    0x2B,
    0x2C,
    *range(0x6E, 0x79),
    0xFC,
    0xFD,
}
_BRANCHES = {0x28, 0x29, 0x2A, *range(0x32, 0x3E), 0x2B, 0x2C}


def extract_lite(dex: DexFile) -> LiteExtraction:
    findings: list[LiteFinding] = []
    bailouts: list[LiteBailout] = []
    targets = {
        index
        for index, method in enumerate(dex.methods)
        if dex.method_name(method) == "newMessageInfo"
        and dex.types[method.class_index].endswith("/GeneratedMessageLite;")
    }
    if not targets:
        return LiteExtraction((), ())

    for method, code in dex.iter_code_items():
        found, failed = _scan_method(dex, method, code, targets)
        findings.extend(found)
        bailouts.extend(failed)
    return LiteExtraction(tuple(findings), tuple(bailouts))


def _scan_method(
    dex: DexFile,
    method: EncodedMethod,
    code: CodeItem,
    targets: set[int],
) -> tuple[list[LiteFinding], list[LiteBailout]]:
    registers: dict[int, Any] = {}
    findings: list[LiteFinding] = []
    bailouts: list[LiteBailout] = []
    for instruction in _instructions(code.instructions):
        opcode = instruction.opcode
        units = instruction.units
        if opcode in _BRANCHES:
            registers.clear()
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
            registers[destination] = (
                _Array(size, {}) if isinstance(size, int) and size >= 0 else None
            )
        elif opcode == 0x4D:
            value_register = units[0] >> 8
            array_register = units[1] & 0xFF
            index_register = units[1] >> 8
            array = registers.get(array_register)
            stored_index = registers.get(index_register)
            stored_value = registers.get(value_register)
            if (
                isinstance(array, _Array)
                and isinstance(stored_index, int)
                and isinstance(stored_value, LiteObject)
                and 0 <= stored_index < array.size
            ):
                array.values[stored_index] = stored_value
        elif opcode in {0x71, 0x77}:
            target = units[1]
            if target not in targets:
                registers.clear()
                continue
            argument_registers = _invoke_registers(instruction)
            finding, reason = _resolve_call(
                dex, method, code, instruction, argument_registers, registers
            )
            if finding is not None:
                findings.append(finding)
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
    if decoded.header.field_count == 0 and array == 0:
        return (
            LiteFinding(
                method.method_index,
                code.offset,
                instruction.offset,
                str(info.value),
                (),
            ),
            "",
        )
    if not isinstance(array, _Array):
        return None, "objects register is not a tracked new-array"
    missing = [index for index in range(array.size) if index not in array.values]
    if missing:
        return None, f"objects array has unresolved indexes: {missing}"
    objects = tuple(array.values[index] for index in range(array.size))
    return (
        LiteFinding(
            method.method_index,
            code.offset,
            instruction.offset,
            str(info.value),
            objects,
        ),
        "",
    )


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
    if instruction.opcode == 0x77:
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

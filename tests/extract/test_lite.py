from protoloom.container.dex import CodeItem, DexMethod, EncodedMethod
from protoloom.extract.lite import LiteObject, extract_lite


def encode_int(value: int) -> str:
    chars = []
    while value >= 0xD800:
        chars.append(chr((value & 0x1FFF) | 0xE000))
        value >>= 13
    chars.append(chr(value))
    return "".join(chars)


def info_string() -> str:
    values = (0, 2, 0, 0, 1, 2, 2, 0, 0, 0, 1, 8, 2, 9)
    return "".join(encode_int(value) for value in values)


class FakeDex:
    def __init__(self, instructions: tuple[int, ...], strings: tuple[str, ...]) -> None:
        self.strings = strings
        self.types = (
            "Lcom/google/protobuf/GeneratedMessageLite;",
            "[Ljava/lang/Object;",
            "LNested;",
        )
        self.methods = (
            DexMethod(0, 0, 0),
            DexMethod(0, 0, 1),
        )
        method = EncodedMethod(0, 0, 100)
        code = CodeItem(100, 8, 0, 3, 0, 0, instructions)
        self._items = ((method, code),)

    def method_name(self, method: DexMethod) -> str:
        return self.strings[method.name_index]

    def iter_code_items(self) -> tuple[tuple[EncodedMethod, CodeItem], ...]:
        return self._items


def const_string(register: int, index: int) -> tuple[int, int]:
    return (0x1A | register << 8, index)


def const_number(register: int, value: int) -> tuple[int, ...]:
    return (0x12 | register << 8 | (value & 0xF) << 12,)


def aput(value: int, array: int, index: int) -> tuple[int, int]:
    return (0x4D | value << 8, array | index << 8)


def invoke(method_index: int, registers: tuple[int, int, int]) -> tuple[int, ...]:
    first, second, third = registers
    return (0x71 | 3 << 12, method_index, first | second << 4 | third << 8)


def complete_instructions() -> tuple[int, ...]:
    return (
        *const_number(0, 2),
        0x23 | 1 << 8,
        1,
        *const_string(2, 2),
        *const_string(3, 3),
        *const_number(4, 0),
        *aput(3, 1, 4),
        0x1C | 3 << 8,
        2,
        *const_number(4, 1),
        *aput(3, 1, 4),
        0x62,
        7,
        *invoke(1, (0, 2, 1)),
        0x0E,
    )


def test_extracts_info_string_and_ordered_objects() -> None:
    dex = FakeDex(
        complete_instructions(), ("owner", "newMessageInfo", info_string(), "name_")
    )

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert result.bailout_count == 0
    assert len(result.findings) == 1
    assert result.findings[0].info_string == info_string()
    assert result.findings[0].objects == (
        LiteObject("string", "name_"),
        LiteObject("class", "LNested;"),
    )


def test_subclass_owned_new_message_info_reference_is_supported() -> None:
    dex = FakeDex(
        complete_instructions(), ("owner", "newMessageInfo", info_string(), "name_")
    )
    dex.types = ("Lmatrix/Thing;", "[Ljava/lang/Object;", "LNested;")

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert len(result.findings) == 1


def test_inlined_raw_message_info_constructor_is_detected() -> None:
    base = complete_instructions()
    instructions = (*base[:-4], 0x4070, 0, 0x1205, 0x0E)
    dex = FakeDex(instructions, ("<init>", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert len(result.findings) == 1


def test_records_incomplete_array_as_a_bailout() -> None:
    instructions = complete_instructions()
    second_aput = instructions.index(0x034D, instructions.index(0x034D) + 1)
    instructions = (
        *instructions[:second_aput],
        0x00,
        0x00,
        *instructions[second_aput + 2 :],
    )
    dex = FakeDex(instructions, ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert result.findings == ()
    assert result.bailout_count == 1
    assert "unresolved indexes: [1]" in result.bailouts[0].reason


def test_records_untracked_info_register_as_a_bailout() -> None:
    instructions = complete_instructions()
    info_at = instructions.index(0x021A)
    instructions = (*instructions[:info_at], 0x00, 0x00, *instructions[info_at + 2 :])
    dex = FakeDex(instructions, ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert result.bailout_count == 1
    assert result.bailouts[0].reason == "info string register is not a const-string"


def test_non_target_invokes_do_not_become_findings() -> None:
    instructions = complete_instructions()[:-4] + invoke(0, (0, 2, 1))
    dex = FakeDex(instructions, ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert result.findings == ()
    assert result.bailouts == ()


def test_switch_constants_survive_branches_and_unrelated_calls() -> None:
    base = list(complete_instructions())
    base[base.index(0x0412)] = 0
    instructions = (
        *const_number(4, 0),
        0x28,
        0x71,
        0,
        0,
        *base,
    )
    dex = FakeDex(instructions, ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert len(result.findings) == 1


def test_unresolved_array_index_uses_visible_order_at_lower_confidence() -> None:
    base = list(complete_instructions())
    index_constant = base.index(0x0412)
    base[index_constant : index_constant + 1] = const_string(4, 3)
    dex = FakeDex(tuple(base), ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert len(result.findings) == 1
    assert result.findings[0].heuristic


def test_unknown_array_size_expands_from_aput_indexes() -> None:
    base = list(complete_instructions())
    size_constant = base.index(0x2012)
    base[size_constant : size_constant + 1] = (0x001C, 2)
    dex = FakeDex(tuple(base), ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert len(result.findings) == 1
    assert len(result.findings[0].objects) == 2
    assert result.findings[0].heuristic


def test_object_result_from_helper_call_can_fill_array() -> None:
    base = list(complete_instructions())
    class_at = base.index(0x031C)
    base[class_at : class_at + 2] = (0x71, 0, 0, 0x030C)
    dex = FakeDex(tuple(base), ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert len(result.findings) == 1
    assert result.findings[0].objects[1].kind == "call_result"


def test_fill_array_data_keeps_instruction_alignment() -> None:
    base = complete_instructions()
    instructions = (*base[:-4], 0x26, 0, 0, *base[-4:])
    dex = FakeDex(instructions, ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert len(result.findings) == 1


def test_filled_new_array_and_move_result_are_tracked() -> None:
    replacement = (
        *const_string(2, 2),
        *const_string(3, 3),
        0x041C,
        2,
        0x2024,
        1,
        0x43,
        0x010C,
        0x62,
        7,
        *invoke(1, (0, 2, 1)),
        0x0E,
    )
    dex = FakeDex(replacement, ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert len(result.findings) == 1
    assert result.findings[0].objects == (
        LiteObject("string", "name_"),
        LiteObject("class", "LNested;"),
    )


def test_invoke_range_and_object_moves_are_supported() -> None:
    instructions = complete_instructions()
    instructions = (
        *instructions[:-4],
        0x62 | 5 << 8,
        7,
        0x07 | 6 << 8 | 2 << 12,
        0x07 | 7 << 8 | 1 << 12,
        0x77 | 3 << 8,
        1,
        5,
    )
    dex = FakeDex(instructions, ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert len(result.findings) == 1
    assert result.bailout_count == 0


def test_empty_message_accepts_a_null_objects_array() -> None:
    empty_info = encode_int(0) + encode_int(0)
    instructions = (
        *const_string(2, 2),
        *const_number(1, 0),
        0x62,
        7,
        *invoke(1, (0, 2, 1)),
    )
    dex = FakeDex(instructions, ("owner", "newMessageInfo", empty_info))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert result.findings[0].objects == ()
    assert result.bailout_count == 0


def test_empty_message_ignores_stale_objects_register() -> None:
    empty_info = encode_int(0) + encode_int(0)
    instructions = (
        *const_string(2, 2),
        0x1C | 1 << 8,
        2,
        0x62,
        7,
        *invoke(1, (0, 2, 1)),
    )
    dex = FakeDex(instructions, ("owner", "newMessageInfo", empty_info))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert len(result.findings) == 1
    assert result.bailout_count == 0

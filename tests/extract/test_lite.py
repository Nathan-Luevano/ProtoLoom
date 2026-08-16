from protoloom.container.dex import CodeItem, DexField, DexMethod, EncodedMethod
from protoloom.extract.lite import (
    LiteObject,
    extract_lite,
    recover_enum_evidence,
)


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
        self.types: tuple[str, ...] = (
            "Lcom/google/protobuf/GeneratedMessageLite;",
            "[Ljava/lang/Object;",
            "LNested;",
        )
        self.methods: tuple[DexMethod, ...] = (
            DexMethod(0, 0, 0),
            DexMethod(0, 0, 1),
        )
        method = EncodedMethod(0, 0, 100)
        code = CodeItem(100, 8, 0, 3, 0, 0, instructions)
        self._items: tuple[tuple[EncodedMethod, CodeItem], ...] = ((method, code),)

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


def test_unresolved_array_index_bails_out_instead_of_guessing_order() -> None:
    base = list(complete_instructions())
    index_constant = base.index(0x0412)
    base[index_constant : index_constant + 1] = const_string(4, 3)
    dex = FakeDex(tuple(base), ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert result.findings == ()
    assert result.bailout_count == 1
    assert result.bailouts[0].reason == (
        "objects array requires unresolved-order heuristics"
    )


def test_unknown_array_size_bails_out_instead_of_guessing_size() -> None:
    base = list(complete_instructions())
    size_constant = base.index(0x2012)
    base[size_constant : size_constant + 1] = (0x001C, 2)
    dex = FakeDex(tuple(base), ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert result.findings == ()
    assert result.bailout_count == 1
    assert result.bailouts[0].reason == (
        "objects array requires unresolved-order heuristics"
    )


def test_heuristic_recovery_is_explicit_and_still_counted() -> None:
    base = list(complete_instructions())
    index_constant = base.index(0x0412)
    base[index_constant : index_constant + 1] = const_string(4, 3)
    dex = FakeDex(tuple(base), ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex, allow_heuristic=True)  # type: ignore[arg-type]

    assert len(result.findings) == 1
    assert result.findings[0].heuristic
    assert result.bailout_count == 1
    assert "opt-in" in result.bailouts[0].reason


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


def test_obfuscated_raw_message_info_wrapper_is_a_target() -> None:
    dex = FakeDex(
        complete_instructions(),
        ("owner", "a", info_string(), "name_", "<init>"),
    )
    dex.methods = (
        DexMethod(0, 0, 0),
        DexMethod(0, 0, 1),
        DexMethod(3, 0, 4),
    )
    dex.types = (*dex.types, "Lcom/google/protobuf/RawMessageInfo;")
    wrapper = CodeItem(
        200,
        4,
        3,
        4,
        0,
        0,
        (0x22, 0, 0x4070, 2, 0x3210, 0x11),
    )
    dex._items = (*dex._items, (EncodedMethod(1, 0, 200), wrapper))

    result = extract_lite(dex, allow_heuristic=True)  # type: ignore[arg-type]

    assert len(result.findings) == 1


class EnumFakeDex:
    def __init__(
        self,
        constructor_index: int = 1,
        constructor_parameters: tuple[str, ...] = ("Ljava/lang/String;", "I", "I"),
    ) -> None:
        self.types = (
            "Lmatrix/MatrixProto$Everything;",
            "Lmatrix/MatrixProto$Mode;",
        )
        self.strings = (
            "getMode",
            "<init>",
            "<clinit>",
            "MODE_UNSPECIFIED",
            "MODE_ACTIVE",
        )
        self.methods = (
            DexMethod(0, 0, 0),
            DexMethod(1, 0, 1),
            DexMethod(1, 0, 2),
        )
        self.fields = (
            DexField(1, 1, 3),
            DexField(1, 1, 4),
        )
        instructions = (
            0x22,
            1,
            0x011A,
            3,
            0x0212,
            0x4070,
            constructor_index,
            0x2210,
            0x69,
            0,
            0x22,
            1,
            0x011A,
            4,
            0x1212,
            0x4070,
            constructor_index,
            0x2210,
            0x69,
            1,
            0x0E,
        )
        self._items: tuple[tuple[EncodedMethod, CodeItem], ...] = (
            (
                EncodedMethod(2, 0, 200),
                CodeItem(200, 3, 0, 4, 0, 0, instructions),
            ),
        )
        self.constructor_parameters = constructor_parameters

    def method_name(self, method: DexMethod) -> str:
        return self.strings[method.name_index]

    def method_return_type(self, method: DexMethod) -> str:
        return self.types[1]

    def method_parameter_types(self, method: DexMethod) -> tuple[str, ...]:
        return self.constructor_parameters

    def field_name(self, field: DexField) -> str:
        return self.strings[field.name_index]

    def iter_code_items(self) -> tuple[tuple[EncodedMethod, CodeItem], ...]:
        return self._items


def test_enum_values_require_getter_constructor_and_static_field_evidence() -> None:
    evidence = recover_enum_evidence(
        EnumFakeDex(),  # type: ignore[arg-type]
        "Lmatrix/MatrixProto$Everything;",
        "mode_",
    )

    assert evidence is not None
    assert evidence.descriptor == "Lmatrix/MatrixProto$Mode;"
    assert evidence.values == (("MODE_UNSPECIFIED", 0), ("MODE_ACTIVE", 1))


def test_enum_recovery_rejects_hostile_constructor_index() -> None:
    evidence = recover_enum_evidence(
        EnumFakeDex(99),  # type: ignore[arg-type]
        "Lmatrix/MatrixProto$Everything;",
        "mode_",
    )

    assert evidence is None


def test_enum_recovery_rejects_wrong_constructor_signature() -> None:
    evidence = recover_enum_evidence(
        EnumFakeDex(  # type: ignore[arg-type]
            constructor_parameters=("Ljava/lang/String;", "I", "J")
        ),
        "Lmatrix/MatrixProto$Everything;",
        "mode_",
    )

    assert evidence is None


def test_enum_registers_do_not_leak_between_clinit_candidates() -> None:
    dex = EnumFakeDex()
    _, complete = dex._items[0]
    without_names = tuple(
        unit
        for index, unit in enumerate(complete.instructions)
        if index not in {2, 3, 12, 13}
    )
    dex._items = (
        (
            EncodedMethod(2, 0, 180),
            CodeItem(180, 3, 0, 0, 0, 0, (0x011A, 3, 0x0E)),
        ),
        (
            EncodedMethod(2, 0, 200),
            CodeItem(200, 3, 0, 4, 0, 0, without_names),
        ),
    )

    evidence = recover_enum_evidence(
        dex,  # type: ignore[arg-type]
        "Lmatrix/MatrixProto$Everything;",
        "mode_",
    )

    assert evidence is None


def test_unrelated_constructor_wrapper_is_not_a_target() -> None:
    dex = FakeDex(
        complete_instructions(),
        ("owner", "a", info_string(), "name_", "<init>"),
    )
    dex.methods = (
        DexMethod(0, 0, 0),
        DexMethod(0, 0, 1),
        DexMethod(2, 0, 4),
    )
    wrapper = CodeItem(
        200,
        4,
        3,
        4,
        0,
        0,
        (0x22, 0, 0x4070, 2, 0x3210, 0x11),
    )
    dex._items = (*dex._items, (EncodedMethod(1, 0, 200), wrapper))

    result = extract_lite(dex, allow_heuristic=True)  # type: ignore[arg-type]

    assert result.findings == ()


def test_invalid_invoke_method_index_is_a_bailout() -> None:
    instructions = (*invoke(99, (0, 1, 2)), *complete_instructions())
    dex = FakeDex(instructions, ("owner", "newMessageInfo", info_string(), "name_"))

    result = extract_lite(dex)  # type: ignore[arg-type]

    assert len(result.findings) == 1
    assert result.bailout_count == 1
    assert "invalid method index 99" in result.bailouts[0].reason

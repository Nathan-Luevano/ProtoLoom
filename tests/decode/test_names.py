import pytest

from protoloom.decode.names import (
    java_to_proto_name,
    names_are_obfuscated,
    recover_names,
    unpack_field_names,
)


@pytest.mark.parametrize(
    ("java", "proto"),
    [
        ("userId_", "user_id"),
        ("URLValue_", "url_value"),
        ("class_", "class"),
        ("message_", "message"),
        ("9patch_", "field_9patch"),
        ("bad$name_", "bad_name"),
    ],
)
def test_java_name_normalization(java: str, proto: str) -> None:
    assert java_to_proto_name(java) == proto


def test_obfuscation_requires_strict_majority() -> None:
    assert names_are_obfuscated(("a_", "bc_", "realName_"))
    assert not names_are_obfuscated(("a_", "realName_"))
    assert not names_are_obfuscated(())


def test_obfuscated_names_get_numbered_placeholders() -> None:
    names = recover_names(("a_", "b_", "realName_"), (2, 7, 9))
    assert [name.proto_name for name in names] == ["field_2", "field_7", "field_9"]
    assert all(name.obfuscated for name in names)


def test_unpacks_packed_and_separate_object_layouts() -> None:
    assert unpack_field_names(("Thing first_ second_",), 2) == ("first_", "second_")
    assert unpack_field_names(("first_", "second_", object()), 2) == (
        "first_",
        "second_",
    )


def test_bad_object_layout_is_loud() -> None:
    with pytest.raises(ValueError, match="expected 2"):
        unpack_field_names(("only_", object()), 2)


def test_java_name_with_no_alphanumeric_content_falls_back_to_field() -> None:
    assert java_to_proto_name("$$$") == "field"


def test_recover_names_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="same length"):
        recover_names(("a_", "b_"), (1,))


def test_unpack_field_names_rejects_negative_expected_count() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        unpack_field_names((), -1)


def test_unpack_field_names_returns_empty_for_zero_expected_count() -> None:
    assert unpack_field_names((), 0) == ()
    assert unpack_field_names(("anything",), 0) == ()


def test_unpack_field_names_rejects_non_string_leading_entry() -> None:
    with pytest.raises(ValueError, match="does not start with field-name data"):
        unpack_field_names((object(),), 1)

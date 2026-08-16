import pytest
from hypothesis import given
from hypothesis import strategies as st

from protoloom.decode.infostring import (
    CHECK_INITIALIZED_BIT,
    HAS_HAS_BIT,
    ONEOF_TYPE_OFFSET,
    REQUIRED_BIT,
    UTF8_CHECK_BIT,
    InfoStringError,
    decode_info_string,
    decode_integers,
)


def encode_int(value: int) -> str:
    chars = []
    while value >= 0xD800:
        chars.append(chr((value & 0x1FFF) | 0xE000))
        value >>= 13
    chars.append(chr(value))
    return "".join(chars)


def encode(*values: int) -> str:
    return "".join(encode_int(value) for value in values)


@st.composite
def valid_scalar_messages(
    draw: st.DrawFn,
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    numbers = tuple(
        sorted(
            draw(
                st.lists(
                    st.integers(min_value=1, max_value=536_870_911),
                    min_size=1,
                    max_size=24,
                    unique=True,
                )
            )
        )
    )
    type_ids = tuple(
        draw(
            st.lists(
                st.integers(min_value=0, max_value=17),
                min_size=len(numbers),
                max_size=len(numbers),
            )
        )
    )
    presence = tuple(
        draw(
            st.lists(
                st.integers(min_value=0, max_value=63),
                min_size=len(numbers),
                max_size=len(numbers),
            )
        )
    )
    return numbers, type_ids, presence


def test_decodes_boundaries_exactly_like_raw_message_info() -> None:
    values = (0, 0xD7FF, 0xD800, 0xFFFF, 0x10000, 0xFFFFFFFF)
    assert decode_integers(encode(*values)) == values


@given(
    st.lists(
        st.integers(min_value=0, max_value=0xFFFFFFFF),
        min_size=0,
        max_size=100,
    )
)
def test_integer_stream_round_trips_java_uint32_values(values: list[int]) -> None:
    assert decode_integers(encode(*values)) == tuple(values)


@given(valid_scalar_messages(), st.integers(min_value=0, max_value=7))
def test_valid_scalar_message_round_trips_structure(
    message: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]], flags: int
) -> None:
    numbers, type_ids, presence = message
    values = [
        flags,
        len(numbers),
        0,
        2,
        min(numbers),
        max(numbers),
        len(numbers),
        0,
        0,
        0,
    ]
    for number, type_id, hasbits_index in zip(numbers, type_ids, presence, strict=True):
        values.extend((number, type_id | HAS_HAS_BIT, hasbits_index))

    decoded = decode_info_string(encode(*values))

    assert decoded.header.flags == flags
    assert decoded.header.field_count == len(numbers)
    assert decoded.header.min_field_number == min(numbers)
    assert decoded.header.max_field_number == max(numbers)
    assert tuple(field.number for field in decoded.fields) == numbers
    assert tuple(field.base_type_id for field in decoded.fields) == type_ids
    assert tuple(field.hasbits_index for field in decoded.fields) == presence


@given(valid_scalar_messages())
def test_truncating_any_valid_nonempty_message_is_rejected(
    message: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]],
) -> None:
    numbers, type_ids, presence = message
    values = [
        0,
        len(numbers),
        0,
        2,
        min(numbers),
        max(numbers),
        len(numbers),
        0,
        0,
        0,
    ]
    for number, type_id, hasbits_index in zip(numbers, type_ids, presence, strict=True):
        values.extend((number, type_id | HAS_HAS_BIT, hasbits_index))
    encoded = encode(*values)

    with pytest.raises(InfoStringError):
        decode_info_string(encoded[:-1])


@given(st.text(alphabet=st.characters(codec="utf-16")))
def test_arbitrary_java_character_streams_fail_closed(info: str) -> None:
    try:
        decoded = decode_info_string(info)
    except InfoStringError:
        return

    assert decoded.header.field_count == len(decoded.fields)
    assert len({field.number for field in decoded.fields}) == len(decoded.fields)
    assert all(1 <= field.number <= 536_870_911 for field in decoded.fields)


def test_decodes_header_scalar_presence_and_oneof() -> None:
    raw_scalar = 8 | REQUIRED_BIT | UTF8_CHECK_BIT | CHECK_INITIALIZED_BIT | HAS_HAS_BIT
    raw_oneof = ONEOF_TYPE_OFFSET + 9
    info = encode(
        1,
        2,
        1,
        1,
        1,
        7,
        2,
        2,
        0,
        1,
        1,
        raw_scalar,
        31,
        7,
        raw_oneof,
        0,
    )

    decoded = decode_info_string(info)

    assert decoded.header.is_proto2
    assert decoded.header.check_initialized_count == 1
    assert decoded.fields[0].required
    assert decoded.fields[0].check_utf8
    assert decoded.fields[0].check_initialized
    assert decoded.fields[0].hasbits_index == 31
    assert decoded.fields[1].base_type_id == 9
    assert decoded.fields[1].oneof_index == 0


def test_zero_field_encoding_stops_after_count() -> None:
    decoded = decode_info_string(encode(4, 0))
    assert decoded.header.is_edition
    assert decoded.fields == ()


@pytest.mark.parametrize(
    ("values", "match"),
    [
        ((0, 0, 1), "trailing"),
        ((0, 1, 0, 0, 1, 1, 1, 0, 0, 0), "truncated"),
        ((0, 1, 0, 0, 1, 1, 2, 0, 0, 0, 1, 4), "entry count"),
        ((0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 0, 4), "field number"),
        ((0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 1, 69), "field type"),
    ],
)
def test_rejects_malformed_streams(values: tuple[int, ...], match: str) -> None:
    with pytest.raises(InfoStringError, match=match):
        decode_info_string(encode(*values))


def test_rejects_unterminated_integer() -> None:
    with pytest.raises(InfoStringError, match="unterminated"):
        decode_integers(chr(0xE000))

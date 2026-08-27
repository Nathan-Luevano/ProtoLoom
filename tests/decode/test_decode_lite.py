from protoloom.decode.infostring import HAS_HAS_BIT, InfoField
from protoloom.decode.lite import _field_oneof


def _field(*, oneof_index: int | None, raw_type: int) -> InfoField:
    return InfoField(number=1, type_id=9, raw_type=raw_type, oneof_index=oneof_index)


def test_real_oneof_index_wins_over_hasbit() -> None:
    field = _field(oneof_index=2, raw_type=HAS_HAS_BIT)
    assert _field_oneof(field, is_proto2=False) == "choice_2"


def test_proto3_hasbit_without_oneof_synthesizes_one() -> None:
    field = _field(oneof_index=None, raw_type=HAS_HAS_BIT)
    assert _field_oneof(field, is_proto2=False) == "synthetic_1"


def test_proto2_hasbit_does_not_synthesize_a_oneof() -> None:
    field = _field(oneof_index=None, raw_type=HAS_HAS_BIT)
    assert _field_oneof(field, is_proto2=True) is None


def test_no_presence_and_no_oneof_is_plain() -> None:
    field = _field(oneof_index=None, raw_type=0)
    assert _field_oneof(field, is_proto2=False) is None

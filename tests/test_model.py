import pytest

from protoloom.model import (
    Confidence,
    EnumType,
    EnumValue,
    Evidence,
    Field,
    Message,
    RecoveredSchema,
)


def test_schema_defaults_are_independent() -> None:
    first = RecoveredSchema("first.proto")
    second = RecoveredSchema("second.proto")
    first.messages.append(Message("Record"))
    assert second.messages == []


def test_model_represents_nested_recovery_evidence() -> None:
    evidence = Evidence("lite", "com.example.Record#dynamicMethod", "info[10]")
    field = Field("identifier", 1, "string", Confidence.HIGH, [evidence])
    enum = EnumType(
        "State",
        [EnumValue("STATE_UNKNOWN", 0), EnumValue("STATE_READY", 1)],
        Confidence.CERTAIN,
        [evidence],
    )
    message = Message("Record", [field], enums=[enum], confidence=Confidence.HIGH)
    schema = RecoveredSchema("record.proto", "example", "proto3", [message])

    assert schema.messages[0].fields[0].evidence[0].location.endswith("#dynamicMethod")
    assert schema.messages[0].enums[0].values[1].number == 1
    assert str(field.confidence) == "high"


@pytest.mark.parametrize("number", [0, -1, 2**29, 19_000, 19_999])
def test_field_rejects_invalid_numbers(number: int) -> None:
    with pytest.raises(ValueError, match="field number"):
        Field("bad", number, "bytes", Confidence.SPECULATIVE)


def test_field_rejects_invalid_label() -> None:
    with pytest.raises(ValueError, match="field label"):
        Field("bad", 1, "bytes", Confidence.HIGH, label="sometimes")


def test_schema_rejects_unknown_syntax() -> None:
    with pytest.raises(ValueError, match="syntax"):
        RecoveredSchema("bad.proto", syntax="editions")

import pytest

from protoloom.emit.report import emit_report
from protoloom.model import Confidence, Field, Message, RecoveredSchema


def test_report_counts_nested_messages_confidence_and_bailouts() -> None:
    schema = RecoveredSchema(
        name="nested.proto",
        messages=[
            Message(
                name="Outer",
                fields=[Field("id", 1, "int32", Confidence.CERTAIN)],
                messages=[
                    Message(
                        name="Inner",
                        fields=[Field("note", 1, "string", Confidence.HIGH)],
                    )
                ],
            )
        ],
    )

    report = emit_report([schema], ["classes.dex: incomplete info string"])

    assert "Recovered 1 files and 2 messages." in report
    assert "- certain: 1" in report
    assert "- high: 1" in report
    assert "- medium: 0" in report
    assert "- speculative: 0" in report
    assert "Bail-outs: 1\n- classes.dex: incomplete info string\n" in report


def test_report_flattens_multiline_bailouts() -> None:
    report = emit_report([], ["first line\n- injected bullet\r\nlast line"])
    assert "- first line - injected bullet last line\n" in report


def test_report_handles_deep_message_trees() -> None:
    root = Message("Level0")
    current = root
    for index in range(1, 1100):
        child = Message(f"Level{index}")
        current.messages.append(child)
        current = child
    report = emit_report([RecoveredSchema("deep.proto", messages=[root])], [])
    assert "Recovered 1 files and 1100 messages." in report


@pytest.mark.parametrize(
    ("schemas", "bailouts", "message"),
    [
        ([RecoveredSchema("a"), RecoveredSchema("b")], [], "schemas"),
        ([], ["a", "b"], "bailouts"),
    ],
)
def test_report_bounds_top_level_collections(
    schemas: list[RecoveredSchema], bailouts: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        emit_report(schemas, bailouts, max_items=1)


def test_report_bounds_encoded_output() -> None:
    with pytest.raises(ValueError, match="exceeds 8 bytes"):
        emit_report([], [], max_bytes=8)


@pytest.mark.parametrize(("max_items", "max_bytes"), [(0, 1), (1, 0)])
def test_report_rejects_nonpositive_limits(max_items: int, max_bytes: int) -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        emit_report([], [], max_items=max_items, max_bytes=max_bytes)

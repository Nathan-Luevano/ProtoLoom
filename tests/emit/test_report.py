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

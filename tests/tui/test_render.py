from pathlib import Path

from rich.text import Text

from protoloom.tui.render import render_schema, render_summary
from protoloom.tui.results import RecoveryOutput, SchemaRecord


def _plain(value: str) -> str:
    return Text.from_ansi(value).plain


def test_renders_summary_and_nested_schema_evidence() -> None:
    schema = SchemaRecord(
        "sample.proto",
        "demo",
        {
            "messages": [
                {
                    "name": "Sample",
                    "confidence": "certain",
                    "fields": [
                        {
                            "name": "payload",
                            "number": 1,
                            "type_name": "bytes",
                            "confidence": "high",
                        }
                    ],
                }
            ],
            "enums": [{"name": "Mode", "values": [{"name": "ACTIVE", "number": 1}]}],
        },
    )

    detail = _plain(render_schema(schema, width=40))
    summary = _plain(render_summary(RecoveryOutput(Path("out"), (schema,), ())))

    assert "1  payload: bytes [high]" in detail
    assert "ACTIVE = 1" in detail
    assert "Schemas" in summary
    assert "1" in summary

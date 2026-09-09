from pathlib import Path

import pytest
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


def test_deep_schema_display_is_bounded() -> None:
    message: dict[str, object] = {"name": "bottom"}
    for index in range(100):
        message = {"name": f"level{index}", "messages": [message]}
    schema = SchemaRecord("deep.proto", "", {"messages": [message]})

    detail = _plain(render_schema(schema, width=5000))

    assert "Further" in detail
    assert "omitted" in detail
    assert "bottom" not in detail


def test_wide_schema_display_is_bounded() -> None:
    schema = SchemaRecord(
        "wide.proto",
        "",
        {
            "messages": [
                {
                    "name": "Record",
                    "fields": [
                        {"number": index, "name": f"field_{index}"}
                        for index in range(100)
                    ],
                }
            ]
        },
    )

    detail = _plain(render_schema(schema, width=200, max_items=3))

    assert "field_0" in detail
    assert "field_1" in detail
    assert "field_2" not in detail
    assert detail.count("Further items omitted") == 1


def test_schema_display_rejects_nonpositive_limit() -> None:
    schema = SchemaRecord("empty.proto", "", {})

    with pytest.raises(ValueError, match="limit must be positive"):
        render_schema(schema, max_items=0)


def test_schema_display_truncates_untrusted_text() -> None:
    oversized = "x" * 2000
    schema = SchemaRecord(
        oversized,
        oversized,
        {
            "messages": [
                {
                    "name": oversized,
                    "fields": [
                        {
                            "number": 1,
                            "name": oversized,
                            "type_name": oversized,
                            "confidence": oversized,
                        }
                    ],
                }
            ],
            "enums": [{"name": oversized, "values": [{"name": oversized}]}],
        },
    )

    detail = _plain(render_schema(schema, width=100_000))

    assert oversized not in detail
    assert detail.count("...") >= 8
    assert max(map(len, detail.splitlines())) <= 5000

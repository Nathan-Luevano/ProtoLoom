import json

import pytest

from protoloom.emit.jsonout import emit_json
from protoloom.model import Message, RecoveredSchema


def test_json_output_records_canonical_artifact_manifest() -> None:
    schema = RecoveredSchema(name="demo.proto", messages=[Message("Demo")])

    encoded = emit_json(
        [schema],
        [],
        ["report.md", "demo.proto", "recovery.json", "demo.proto"],
    )
    payload = json.loads(encoded)

    assert payload["artifacts"] == ["demo.proto", "recovery.json", "report.md"]
    assert payload["conflicts"] == []
    assert payload["schemas"][0]["name"] == "demo.proto"
    assert encoded.endswith("\n")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_schemas": 1}, "schemas"),
        ({"max_conflicts": 1}, "conflicts"),
        ({"max_artifacts": 1}, "artifacts"),
    ],
)
def test_json_output_bounds_collections(kwargs: dict[str, int], message: str) -> None:
    schemas = [RecoveredSchema("first"), RecoveredSchema("second")]
    conflicts: list[dict[str, object]] = [{"id": 1}, {"id": 2}]
    artifacts = ["first", "second"]
    with pytest.raises(ValueError, match=message):
        emit_json(schemas, conflicts, artifacts, **kwargs)


def test_json_output_bounds_encoded_bytes() -> None:
    with pytest.raises(ValueError, match="exceeds 8 bytes"):
        emit_json([], [], [], max_bytes=8)


def test_json_output_bounds_schema_items() -> None:
    schema = RecoveredSchema(
        "deep.proto",
        messages=[Message("Outer", messages=[Message("Inner")])],
    )

    with pytest.raises(ValueError, match="exceeds 2 schema items"):
        emit_json([schema], [], [], max_items=2)


def test_json_output_bounds_message_depth() -> None:
    schema = RecoveredSchema(
        "deep.proto",
        messages=[Message("Outer", messages=[Message("Inner")])],
    )

    with pytest.raises(ValueError, match="message depth 1"):
        emit_json([schema], [], [], max_depth=1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_schemas": 0},
        {"max_conflicts": 0},
        {"max_artifacts": 0},
        {"max_items": 0},
        {"max_depth": 0},
        {"max_bytes": 0},
    ],
)
def test_json_output_rejects_nonpositive_limits(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        emit_json([], [], [], **kwargs)

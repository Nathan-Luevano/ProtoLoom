import json
from dataclasses import asdict
from io import StringIO

from protoloom.model import Message, RecoveredSchema

MAX_JSON_SCHEMAS = 100_000
MAX_JSON_CONFLICTS = 1_000_000
MAX_JSON_ARTIFACTS = 100_000
MAX_JSON_ITEMS = 1_000_000
MAX_JSON_DEPTH = 100
MAX_JSON_OUTPUT_BYTES = 256 * 1024 * 1024


class _BoundedTextWriter:
    def __init__(self, max_bytes: int) -> None:
        self._buffer = StringIO()
        self._max_bytes = max_bytes
        self._size = 0

    def write(self, value: str) -> int:
        self._size += len(value.encode("utf-8"))
        if self._size > self._max_bytes:
            raise ValueError(f"JSON output exceeds {self._max_bytes} bytes")
        return self._buffer.write(value)

    def value(self) -> str:
        return self._buffer.getvalue()


def emit_json(
    schemas: list[RecoveredSchema],
    conflicts: list[dict[str, object]],
    artifacts: list[str],
    *,
    max_schemas: int = MAX_JSON_SCHEMAS,
    max_conflicts: int = MAX_JSON_CONFLICTS,
    max_artifacts: int = MAX_JSON_ARTIFACTS,
    max_items: int = MAX_JSON_ITEMS,
    max_depth: int = MAX_JSON_DEPTH,
    max_bytes: int = MAX_JSON_OUTPUT_BYTES,
) -> str:
    limits = (
        max_schemas,
        max_conflicts,
        max_artifacts,
        max_items,
        max_depth,
        max_bytes,
    )
    if any(limit <= 0 for limit in limits):
        raise ValueError("JSON output limits must be positive")
    if len(schemas) > max_schemas:
        raise ValueError(f"JSON output exceeds {max_schemas} schemas")
    if len(conflicts) > max_conflicts:
        raise ValueError(f"JSON output exceeds {max_conflicts} conflicts")
    if len(artifacts) > max_artifacts:
        raise ValueError(f"JSON output exceeds {max_artifacts} artifacts")
    _validate_schema_budget(schemas, max_items, max_depth)
    payload = {
        "artifacts": sorted(set(artifacts)),
        "conflicts": conflicts,
        "schemas": [asdict(schema) for schema in schemas],
    }
    output = _BoundedTextWriter(max_bytes)
    json.dump(payload, output, indent=2, sort_keys=True)
    output.write("\n")
    return output.value()


def _validate_schema_budget(
    schemas: list[RecoveredSchema], max_items: int, max_depth: int
) -> None:
    count = sum(
        1
        + len(schema.dependencies)
        + len(schema.evidence)
        + len(schema.enums)
        + sum(len(enum.values) + len(enum.evidence) for enum in schema.enums)
        for schema in schemas
    )
    if count > max_items:
        raise ValueError(f"JSON output exceeds {max_items} schema items")
    pending: list[tuple[Message, int]] = [
        (message, 1) for schema in schemas for message in schema.messages
    ]
    while pending:
        message, depth = pending.pop()
        if depth > max_depth:
            raise ValueError(f"JSON output exceeds message depth {max_depth}")
        count += 1 + len(message.evidence)
        count += sum(1 + len(field.evidence) for field in message.fields)
        count += sum(
            1 + len(enum.values) + len(enum.evidence) for enum in message.enums
        )
        if count > max_items:
            raise ValueError(f"JSON output exceeds {max_items} schema items")
        pending.extend((child, depth + 1) for child in message.messages)

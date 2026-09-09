import json
from dataclasses import asdict
from io import StringIO

from protoloom.model import RecoveredSchema

MAX_JSON_SCHEMAS = 100_000
MAX_JSON_CONFLICTS = 1_000_000
MAX_JSON_ARTIFACTS = 100_000
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
    max_bytes: int = MAX_JSON_OUTPUT_BYTES,
) -> str:
    limits = (max_schemas, max_conflicts, max_artifacts, max_bytes)
    if any(limit <= 0 for limit in limits):
        raise ValueError("JSON output limits must be positive")
    if len(schemas) > max_schemas:
        raise ValueError(f"JSON output exceeds {max_schemas} schemas")
    if len(conflicts) > max_conflicts:
        raise ValueError(f"JSON output exceeds {max_conflicts} conflicts")
    if len(artifacts) > max_artifacts:
        raise ValueError(f"JSON output exceeds {max_artifacts} artifacts")
    payload = {
        "artifacts": sorted(set(artifacts)),
        "conflicts": conflicts,
        "schemas": [asdict(schema) for schema in schemas],
    }
    output = _BoundedTextWriter(max_bytes)
    json.dump(payload, output, indent=2, sort_keys=True)
    output.write("\n")
    return output.value()

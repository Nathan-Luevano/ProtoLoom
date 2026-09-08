import json
from dataclasses import asdict

from protoloom.model import RecoveredSchema


def emit_json(
    schemas: list[RecoveredSchema],
    conflicts: list[dict[str, object]],
    artifacts: list[str],
) -> str:
    payload = {
        "artifacts": sorted(set(artifacts)),
        "conflicts": conflicts,
        "schemas": [asdict(schema) for schema in schemas],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"

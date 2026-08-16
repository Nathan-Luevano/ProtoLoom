import json
from dataclasses import asdict

from protoloom.model import RecoveredSchema


def emit_json(
    schemas: list[RecoveredSchema], conflicts: list[dict[str, object]]
) -> str:
    return (
        json.dumps(
            {"schemas": [asdict(schema) for schema in schemas], "conflicts": conflicts},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

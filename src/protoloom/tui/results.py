import json
from dataclasses import dataclass
from pathlib import Path


class OutputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SchemaRecord:
    name: str
    package: str
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class RecoveryOutput:
    root: Path
    schemas: tuple[SchemaRecord, ...]
    conflicts: tuple[dict[str, object], ...]


def _records(value: object, label: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise OutputError(f"recovery.json {label} must be a list of objects")
    return tuple(value)


def load_output(root: Path) -> RecoveryOutput:
    path = root / "recovery.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise OutputError(f"cannot read {path}: {error.strerror}") from error
    except json.JSONDecodeError as error:
        raise OutputError(
            f"malformed {path}: line {error.lineno}, column {error.colno}"
        ) from error
    except UnicodeDecodeError as error:
        raise OutputError(
            f"malformed {path}: invalid UTF-8 at byte {error.start}"
        ) from error
    except RecursionError as error:
        raise OutputError(f"malformed {path}: nesting is too deep") from error
    if not isinstance(value, dict):
        raise OutputError("recovery.json root must be an object")
    schemas = _records(value.get("schemas"), "schemas")
    conflicts = _records(value.get("conflicts"), "conflicts")
    items: list[SchemaRecord] = []
    for schema in schemas:
        name, package = schema.get("name"), schema.get("package", "")
        if not isinstance(name, str) or not isinstance(package, str):
            raise OutputError("each schema requires string name and package values")
        items.append(SchemaRecord(name, package, schema))
    return RecoveryOutput(root, tuple(items), conflicts)

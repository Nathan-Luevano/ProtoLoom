import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path


class OutputError(ValueError):
    pass


MAX_RECOVERY_OUTPUT_SIZE = 16 * 1024 * 1024
MAX_REPORT_SIZE = 1024 * 1024


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
    bailouts: int | None = None


def _records(value: object, label: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise OutputError(f"recovery.json {label} must be a list of objects")
    return tuple(value)


def _read_text(path: Path, max_size: int) -> str:
    with path.open("rb") as stream:
        status = os.fstat(stream.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise OutputError(f"{path} is not a regular file")
        if status.st_size > max_size:
            raise OutputError(f"{path} exceeds {max_size} bytes")
        data = stream.read(max_size + 1)
    if len(data) > max_size:
        raise OutputError(f"{path} exceeds {max_size} bytes")
    return data.decode("utf-8")


def load_output(root: Path) -> RecoveryOutput:
    path = root / "recovery.json"
    try:
        value = json.loads(_read_text(path, MAX_RECOVERY_OUTPUT_SIZE))
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
    return RecoveryOutput(root, tuple(items), conflicts, _bailout_count(root))


def _bailout_count(root: Path) -> int | None:
    try:
        report = _read_text(root / "report.md", MAX_REPORT_SIZE)
    except (OSError, OutputError, UnicodeDecodeError):
        return None
    match = re.search(r"^Bail-outs: (\d+)$", report, re.MULTILINE)
    return int(match.group(1)) if match else None

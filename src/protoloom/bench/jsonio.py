import json
import os
import stat
from pathlib import Path
from typing import Any

MAX_BENCH_JSON_SIZE = 16 * 1024 * 1024


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path, max_size: int = MAX_BENCH_JSON_SIZE) -> Any:
    if max_size <= 0:
        raise ValueError("maximum JSON size must be positive")
    with path.open("rb") as stream:
        status = os.fstat(stream.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise ValueError(f"JSON input is not a regular file: {path}")
        if status.st_size > max_size:
            raise ValueError(f"JSON input exceeds {max_size} bytes: {path}")
        payload = stream.read(max_size + 1)
    if len(payload) > max_size:
        raise ValueError(f"JSON input exceeds {max_size} bytes: {path}")
    return json.loads(payload, object_pairs_hook=_unique_object)

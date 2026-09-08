import json
from pathlib import Path
from typing import Any

MAX_BENCH_JSON_SIZE = 16 * 1024 * 1024


def read_json(path: Path, max_size: int = MAX_BENCH_JSON_SIZE) -> Any:
    if max_size <= 0:
        raise ValueError("maximum JSON size must be positive")
    if path.stat().st_size > max_size:
        raise ValueError(f"JSON input exceeds {max_size} bytes: {path}")
    with path.open("rb") as stream:
        payload = stream.read(max_size + 1)
    if len(payload) > max_size:
        raise ValueError(f"JSON input exceeds {max_size} bytes: {path}")
    return json.loads(payload)

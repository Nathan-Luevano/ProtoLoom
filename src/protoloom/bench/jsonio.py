import json
import math
import os
import stat
from pathlib import Path
from typing import Any

MAX_BENCH_JSON_SIZE = 16 * 1024 * 1024
MAX_JSON_NUMBER_CHARACTERS = 1000


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _bounded_number(value: str) -> str:
    if len(value) > MAX_JSON_NUMBER_CHARACTERS:
        raise ValueError(f"JSON number exceeds {MAX_JSON_NUMBER_CHARACTERS} characters")
    return value


def _bounded_int(value: str) -> int:
    return int(_bounded_number(value))


def _finite_float(value: str) -> float:
    result = float(_bounded_number(value))
    if not math.isfinite(result):
        raise ValueError(f"JSON number exceeds finite range: {value}")
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
    return json.loads(
        payload,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
        parse_int=_bounded_int,
    )

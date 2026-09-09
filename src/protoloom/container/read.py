import os
import stat
from pathlib import Path

MAX_CONTAINER_SIZE = 256 * 1024 * 1024


def read_limited(path: str | Path, *, max_size: int = MAX_CONTAINER_SIZE) -> bytes:
    if max_size <= 0:
        raise ValueError("maximum input size must be positive")
    source = Path(path)
    with source.open("rb") as stream:
        status = os.fstat(stream.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise OSError(f"input is not a regular file: {source}")
        if status.st_size > max_size:
            raise OSError(f"input exceeds {max_size} bytes: {source}")
        data = stream.read(max_size + 1)
    if len(data) > max_size:
        raise OSError(f"input exceeds {max_size} bytes: {source}")
    return data

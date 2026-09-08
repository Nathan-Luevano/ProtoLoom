from pathlib import Path

MAX_CONTAINER_SIZE = 256 * 1024 * 1024


def read_limited(path: str | Path, *, max_size: int = MAX_CONTAINER_SIZE) -> bytes:
    if max_size <= 0:
        raise ValueError("maximum input size must be positive")
    source = Path(path)
    if source.stat().st_size > max_size:
        raise OSError(f"input exceeds {max_size} bytes: {source}")
    with source.open("rb") as stream:
        data = stream.read(max_size + 1)
    if len(data) > max_size:
        raise OSError(f"input exceeds {max_size} bytes: {source}")
    return data

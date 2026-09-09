import os
from pathlib import Path

import pytest

from protoloom.container.read import read_limited


def test_limited_read_returns_complete_input(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"payload")

    assert read_limited(source, max_size=7) == b"payload"


def test_limited_read_rejects_oversized_input_before_reading(tmp_path: Path) -> None:
    source = tmp_path / "large.bin"
    source.write_bytes(b"1234")

    with pytest.raises(OSError, match="input exceeds 3 bytes"):
        read_limited(source, max_size=3)


def test_limited_read_rejects_nonpositive_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        read_limited(tmp_path / "missing", max_size=0)


def test_limited_read_rejects_special_files() -> None:
    with pytest.raises(OSError, match="input is not a regular file"):
        read_limited(Path(os.devnull))

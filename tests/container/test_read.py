import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import pytest

import protoloom.container.read as read_module
from protoloom.container.read import open_limited, read_limited


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


def test_limited_read_rejects_input_that_grows_past_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A file could grow between open_limited's own fstat-based size check
    # and the actual read (e.g. a concurrent writer); read_limited must
    # not trust that first check alone.
    class _GrowingStream:
        def read(self, size: int) -> bytes:
            return b"1234"

    @contextmanager
    def fake_open_limited(path: str | Path, *, max_size: int = 0) -> Iterator[BinaryIO]:
        yield _GrowingStream()  # type: ignore[misc]

    monkeypatch.setattr(read_module, "open_limited", fake_open_limited)

    with pytest.raises(OSError, match="input exceeds 3 bytes"):
        read_limited(tmp_path / "unused.bin", max_size=3)


def test_limited_open_exposes_bounded_regular_stream(tmp_path: Path) -> None:
    source = tmp_path / "input.bin"
    source.write_bytes(b"payload")

    with open_limited(source, max_size=7) as stream:
        assert stream.read() == b"payload"

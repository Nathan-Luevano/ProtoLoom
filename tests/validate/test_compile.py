import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, cast

import pytest
from pytest import MonkeyPatch

from protoloom.validate.compile import compile_proto


def _compiler(
    monkeypatch: MonkeyPatch,
    *,
    returncode: int = 0,
    stderr: bytes = b"",
    timeout: bool = False,
    interrupt: bool = False,
) -> Callable[..., object]:
    should_timeout = timeout

    class Process:
        pid = 42

        def __init__(self) -> None:
            self.returncode = returncode
            self.waits = 0

        def wait(self, timeout: float | None = None) -> int:
            self.waits += 1
            if interrupt and self.waits == 1:
                raise KeyboardInterrupt
            if should_timeout and self.waits == 1:
                raise subprocess.TimeoutExpired("protoc", timeout or 0)
            return self.returncode

    def start(*args: object, **kwargs: object) -> Process:
        diagnostic = cast(BinaryIO, kwargs["stderr"])
        diagnostic.write(stderr)
        return Process()

    monkeypatch.setattr("protoloom.validate.compile.subprocess.Popen", start)
    monkeypatch.setattr("protoloom.validate.compile.os.killpg", lambda pid, sig: None)
    return start


def test_compile_returns_failure_when_compiler_times_out(
    monkeypatch: MonkeyPatch,
) -> None:
    _compiler(monkeypatch, timeout=True)

    result = compile_proto('syntax = "proto3";', timeout_seconds=0.25)

    assert not result.success
    assert result.descriptor_set is None
    assert result.stderr == "compiler exceeded 0.25s timeout"


def test_compile_preserves_compiler_failure(monkeypatch: MonkeyPatch) -> None:
    _compiler(monkeypatch, returncode=1, stderr=b"invalid schema")

    result = compile_proto("invalid")

    assert not result.success
    assert result.descriptor_set is None
    assert result.stderr == "invalid schema"


def test_compile_bounds_compiler_diagnostic(monkeypatch: MonkeyPatch) -> None:
    _compiler(monkeypatch, returncode=1, stderr=b"prefix-tail")
    monkeypatch.setattr("protoloom.validate.compile.MAX_COMPILER_DIAGNOSTIC_SIZE", 4)

    result = compile_proto("invalid")

    assert result.stderr == "tail"


def test_compile_kills_interrupted_process(monkeypatch: MonkeyPatch) -> None:
    kills: list[tuple[int, int]] = []
    _compiler(monkeypatch, interrupt=True)
    monkeypatch.setattr(
        "protoloom.validate.compile.os.killpg",
        lambda pid, sig: kills.append((pid, sig)),
    )
    with pytest.raises(KeyboardInterrupt):
        compile_proto("invalid")

    assert kills == [(42, 9)]


def test_compile_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        compile_proto("", timeout_seconds=0)


@pytest.mark.parametrize("name", ["", ".", "..", "bad\n.proto"])
def test_compile_rejects_unsafe_file_name(name: str) -> None:
    with pytest.raises(ValueError, match="unsafe proto file name"):
        compile_proto("", name)


def test_compile_rejects_oversized_source(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("protoloom.validate.compile.MAX_PROTO_SOURCE_SIZE", 3)

    with pytest.raises(ValueError, match="proto source exceeds 3 bytes"):
        compile_proto("four")


def test_compile_rejects_oversized_descriptor(
    monkeypatch: MonkeyPatch,
) -> None:
    start = _compiler(monkeypatch)

    def compile_output(args: list[str], **kwargs: object) -> object:
        output_arg = next(
            item for item in args if item.startswith("--descriptor_set_out=")
        )
        Path(output_arg.partition("=")[2]).write_bytes(b"large")
        return start(args, **kwargs)

    monkeypatch.setattr("protoloom.validate.compile.MAX_DESCRIPTOR_SET_SIZE", 4)
    monkeypatch.setattr("protoloom.validate.compile.subprocess.Popen", compile_output)

    with pytest.raises(ValueError, match="descriptor set exceeds 4 bytes"):
        compile_proto('syntax = "proto3";')


def test_compile_rejects_special_descriptor(monkeypatch: MonkeyPatch) -> None:
    start = _compiler(monkeypatch)

    def compile_output(args: list[str], **kwargs: object) -> object:
        output_arg = next(
            item for item in args if item.startswith("--descriptor_set_out=")
        )
        Path(output_arg.partition("=")[2]).symlink_to(Path(os.devnull))
        return start(args, **kwargs)

    monkeypatch.setattr("protoloom.validate.compile.subprocess.Popen", compile_output)

    with pytest.raises(ValueError, match="descriptor set is not a regular file"):
        compile_proto('syntax = "proto3";')

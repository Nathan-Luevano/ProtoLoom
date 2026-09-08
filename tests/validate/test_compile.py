import subprocess
from pathlib import Path
from typing import Never, cast

import pytest
from pytest import MonkeyPatch

from protoloom.validate.compile import compile_proto


def test_compile_returns_failure_when_compiler_times_out(
    monkeypatch: MonkeyPatch,
) -> None:
    observed: list[float] = []

    def timeout(*args: object, **kwargs: object) -> Never:
        timeout_seconds = cast(float, kwargs["timeout"])
        observed.append(timeout_seconds)
        raise subprocess.TimeoutExpired("protoc", timeout_seconds)

    monkeypatch.setattr("protoloom.validate.compile.subprocess.run", timeout)

    result = compile_proto('syntax = "proto3";', timeout_seconds=0.25)

    assert not result.success
    assert result.descriptor_set is None
    assert result.stderr == "compiler exceeded 0.25s timeout"
    assert observed == [0.25]


def test_compile_preserves_compiler_failure(monkeypatch: MonkeyPatch) -> None:
    process = subprocess.CompletedProcess[str](
        args=["protoc"], returncode=1, stdout="", stderr="invalid schema"
    )
    monkeypatch.setattr(
        "protoloom.validate.compile.subprocess.run", lambda *args, **kwargs: process
    )

    result = compile_proto("invalid")

    assert not result.success
    assert result.descriptor_set is None
    assert result.stderr == "invalid schema"


def test_compile_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        compile_proto("", timeout_seconds=0)


def test_compile_rejects_oversized_source(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("protoloom.validate.compile.MAX_PROTO_SOURCE_SIZE", 3)

    with pytest.raises(ValueError, match="proto source exceeds 3 bytes"):
        compile_proto("four")


def test_compile_rejects_oversized_descriptor(
    monkeypatch: MonkeyPatch,
) -> None:
    process = subprocess.CompletedProcess[str](
        args=["protoc"], returncode=0, stdout="", stderr=""
    )

    def compile_output(args: list[str], **kwargs: object) -> object:
        output_arg = next(
            item for item in args if item.startswith("--descriptor_set_out=")
        )
        Path(output_arg.partition("=")[2]).write_bytes(b"large")
        return process

    monkeypatch.setattr("protoloom.validate.compile.MAX_DESCRIPTOR_SET_SIZE", 4)
    monkeypatch.setattr("protoloom.validate.compile.subprocess.run", compile_output)

    with pytest.raises(ValueError, match="descriptor set exceeds 4 bytes"):
        compile_proto('syntax = "proto3";')

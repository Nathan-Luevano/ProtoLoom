import subprocess
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

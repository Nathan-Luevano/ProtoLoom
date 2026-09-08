import importlib.util
import signal
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "run_tier_a_upstream.py"
    spec = importlib.util.spec_from_file_location("run_tier_a_upstream", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load run_tier_a_upstream.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()


def test_benchmark_tool_runner_enforces_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], bool]] = []
    waits: list[float | None] = []

    class Process:
        returncode = 0
        pid = 42

        def wait(self, timeout: float | None = None) -> int:
            waits.append(timeout)
            return self.returncode

    def start(command: list[str], *, start_new_session: bool) -> Process:
        calls.append((command, start_new_session))
        return Process()

    monkeypatch.setattr(subprocess, "Popen", start)

    script._run(["protoc", "schema.proto"])

    assert calls == [(["protoc", "schema.proto"], True)]
    assert waits == [120.0]


def test_benchmark_tool_runner_kills_timed_out_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits = 0
    kills: list[tuple[int, int]] = []

    class Process:
        returncode = 0
        pid = 42

        def wait(self, timeout: float | None = None) -> int:
            nonlocal waits
            waits += 1
            if waits == 1:
                raise subprocess.TimeoutExpired("protoc", timeout or 0)
            return 0

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(script.os, "killpg", lambda pid, sig: kills.append((pid, sig)))

    with pytest.raises(subprocess.TimeoutExpired):
        script._run(["protoc"])

    assert waits == 2
    assert kills == [(42, signal.SIGKILL)]


def test_benchmark_tool_runner_raises_for_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        returncode = 7

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Process())

    with pytest.raises(subprocess.CalledProcessError) as failure:
        script._run(["protoc"])

    assert failure.value.returncode == 7

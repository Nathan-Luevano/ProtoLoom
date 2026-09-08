import importlib.util
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
    calls: list[tuple[list[str], bool, float]] = []

    def run(command: list[str], *, check: bool, timeout: float) -> None:
        calls.append((command, check, timeout))

    monkeypatch.setattr(subprocess, "run", run)

    script._run(["protoc", "schema.proto"])

    assert calls == [(["protoc", "schema.proto"], True, 120.0)]

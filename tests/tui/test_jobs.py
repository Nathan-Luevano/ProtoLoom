import asyncio
import sys
from pathlib import Path

import pytest

from protoloom.tui.jobs import ExtractionJob, ExtractionRequest


def test_builds_existing_cli_command() -> None:
    request = ExtractionRequest(
        Path("input app.apk"),
        Path("output dir"),
        allow_heuristic_lite=True,
        jadx=True,
    )

    assert request.command() == (
        sys.executable,
        "-m",
        "protoloom.cli",
        "extract",
        "input app.apk",
        "--output",
        "output dir",
        "--allow-heuristic-lite",
        "--jadx",
    )


def test_streams_cli_failure_without_shell(tmp_path: Path) -> None:
    lines: list[str] = []
    job = ExtractionJob()

    result = asyncio.run(
        job.run(ExtractionRequest(tmp_path / "missing.apk", tmp_path), lines.append)
    )

    assert result.returncode == 2
    assert result.cancelled is False
    assert any("file does not exist" in line for line in lines)
    assert job.running is False


def test_cancels_long_running_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = "import time; print('ready', flush=True); time.sleep(30)"
    monkeypatch.setattr(
        ExtractionRequest,
        "command",
        lambda self: (sys.executable, "-c", script),
    )

    async def exercise() -> tuple[bool, list[str]]:
        job = ExtractionJob()
        lines: list[str] = []
        task = asyncio.create_task(
            job.run(ExtractionRequest(tmp_path, tmp_path), lines.append)
        )
        while not lines:
            await asyncio.sleep(0.01)
        await job.cancel()
        return (await task).cancelled, lines

    cancelled, lines = asyncio.run(exercise())

    assert cancelled is True
    assert lines == ["ready"]

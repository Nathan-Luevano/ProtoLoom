import asyncio
import sys
from pathlib import Path

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

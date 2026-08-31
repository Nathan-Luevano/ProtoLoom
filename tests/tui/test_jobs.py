import sys
from pathlib import Path

from protoloom.tui.jobs import ExtractionRequest


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

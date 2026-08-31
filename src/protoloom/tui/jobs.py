import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    source: Path
    output: Path
    allow_heuristic_lite: bool = False
    jadx: bool = False

    def command(self) -> tuple[str, ...]:
        command = [
            sys.executable,
            "-m",
            "protoloom.cli",
            "extract",
            str(self.source),
            "--output",
            str(self.output),
        ]
        if self.allow_heuristic_lite:
            command.append("--allow-heuristic-lite")
        if self.jadx:
            command.append("--jadx")
        return tuple(command)

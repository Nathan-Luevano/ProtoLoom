import asyncio
import sys
from collections.abc import Callable
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


@dataclass(frozen=True, slots=True)
class JobResult:
    returncode: int
    cancelled: bool


class ExtractionJob:
    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._cancelled = False

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def run(
        self, request: ExtractionRequest, on_line: Callable[[str], None]
    ) -> JobResult:
        if self.running:
            raise RuntimeError("extraction job is already running")
        self._cancelled = False
        self._process = await asyncio.create_subprocess_exec(
            *request.command(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert self._process.stdout is not None
        while line := await self._process.stdout.readline():
            on_line(line.decode(errors="replace").rstrip())
        returncode = await self._process.wait()
        return JobResult(returncode, self._cancelled)

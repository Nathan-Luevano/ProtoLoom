import asyncio
import os
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MAX_JOB_OUTPUT_LINE_BYTES = 4096


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
        self._starting = False

    @property
    def running(self) -> bool:
        return self._starting or (
            self._process is not None and self._process.returncode is None
        )

    async def run(
        self, request: ExtractionRequest, on_line: Callable[[str], None]
    ) -> JobResult:
        if self.running:
            raise RuntimeError("extraction job is already running")
        self._cancelled = False
        self._starting = True
        try:
            self._process = await asyncio.create_subprocess_exec(
                *request.command(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=os.name == "posix",
                limit=MAX_JOB_OUTPUT_LINE_BYTES + 1,
            )
        finally:
            self._starting = False
        assert self._process.stdout is not None
        try:
            while True:
                try:
                    line = await self._process.stdout.readline()
                except ValueError:
                    message = f"Output line exceeded {MAX_JOB_OUTPUT_LINE_BYTES} bytes"
                    on_line(message)
                    await asyncio.shield(self._stop())
                    await self._drain_output()
                    assert self._process.returncode is not None
                    return JobResult(self._process.returncode, self._cancelled)
                if not line:
                    break
                on_line(line.decode(errors="replace").rstrip())
            returncode = await self._process.wait()
        except asyncio.CancelledError:
            await asyncio.shield(self.cancel())
            raise
        except BaseException:
            await asyncio.shield(self._stop())
            raise
        return JobResult(returncode, self._cancelled)

    async def cancel(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        self._cancelled = True
        await self._stop()

    async def _stop(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        self._signal(process, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            self._signal(process, signal.SIGKILL)
            await process.wait()

    async def _drain_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        while await process.stdout.read(MAX_JOB_OUTPUT_LINE_BYTES):
            pass

    @staticmethod
    def _signal(process: asyncio.subprocess.Process, value: signal.Signals) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, value)
            elif value is signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except ProcessLookupError:
            pass

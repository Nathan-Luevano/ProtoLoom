import json
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True, slots=True)
class JadxResult:
    output: Path
    source_files: int
    candidate_sites: int
    stderr_tail: str


class JadxError(RuntimeError):
    pass


def decompile_with_jadx(
    input_path: Path,
    output: Path,
    *,
    timeout_seconds: float = 120.0,
    executable: str | None = None,
) -> JadxResult:
    if timeout_seconds <= 0:
        raise ValueError("jadx timeout must be positive")
    command = executable or shutil.which("jadx")
    if command is None:
        raise JadxError("jadx is not installed; run `protoloom doctor`")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [
                command,
                "--no-res",
                "--show-bad-code",
                "-d",
                str(output),
                str(input_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            detail = _log_tail(log)
            raise JadxError(
                f"jadx exceeded {timeout_seconds:g}s timeout"
                + (f": {detail}" if detail else "")
            ) from error
        detail = _log_tail(log)
    if process.returncode != 0:
        raise JadxError(
            f"jadx exited with status {process.returncode}"
            + (f": {detail}" if detail else "")
        )
    sources = tuple(output.rglob("*.java"))
    candidates = _index_candidates(output, sources)
    return JadxResult(output, len(sources), candidates, detail)


def _log_tail(log: BinaryIO, limit: int = 2000) -> str:
    log.seek(0, os.SEEK_END)
    size = log.tell()
    log.seek(max(0, size - limit))
    return log.read().decode("utf-8", errors="replace").strip()


def _index_candidates(output: Path, sources: tuple[Path, ...]) -> int:
    sites: list[dict[str, str | int]] = []
    needles = ("newMessageInfo(", "new RawMessageInfo(")
    for source in sources:
        try:
            lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            if any(needle in line for needle in needles):
                sites.append(
                    {
                        "file": source.relative_to(output).as_posix(),
                        "line": number,
                        "context": line.strip()[:500],
                    }
                )
    (output / "protoloom-candidates.json").write_text(
        json.dumps({"candidate_sites": sites}, indent=2) + "\n", encoding="utf-8"
    )
    return len(sites)

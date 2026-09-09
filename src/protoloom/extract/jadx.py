import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
from contextlib import suppress
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


MAX_JADX_SOURCES = 100_000
MAX_JADX_SOURCE_SIZE = 8 * 1024 * 1024
MAX_JADX_SOURCE_TOTAL = 512 * 1024 * 1024
MAX_JADX_CANDIDATES = 100_000


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


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
    if output.is_symlink():
        raise JadxError(f"jadx output directory is a symlink: {output}")
    if output.exists() and not output.is_dir():
        raise JadxError(f"jadx output path is not a directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(
                [
                    command,
                    "--no-res",
                    "--show-bad-code",
                    "-d",
                    str(staging),
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
                _kill_process_group(process)
                detail = _log_tail(log)
                raise JadxError(
                    f"jadx exceeded {timeout_seconds:g}s timeout"
                    + (f": {detail}" if detail else "")
                ) from error
            except BaseException:
                _kill_process_group(process)
                raise
            detail = _log_tail(log)
        if process.returncode != 0:
            raise JadxError(
                f"jadx exited with status {process.returncode}"
                + (f": {detail}" if detail else "")
            )
        sources, candidates = _index_candidates(staging)
        _publish_directory(staging, output)
        return JadxResult(output, sources, candidates, detail)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _publish_directory(staging: Path, output: Path) -> None:
    backup: Path | None = None
    if output.exists():
        backup = Path(
            tempfile.mkdtemp(prefix=f".{output.name}.old.", dir=output.parent)
        )
        backup.rmdir()
        output.replace(backup)
    try:
        staging.replace(output)
        _sync_directory(output.parent)
    except BaseException:
        if output.exists():
            output.replace(staging)
        if backup is not None:
            backup.replace(output)
        with suppress(OSError):
            _sync_directory(output.parent)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)
        with suppress(OSError):
            _sync_directory(output.parent)


def _sync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _log_tail(log: BinaryIO, limit: int = 2000) -> str:
    log.seek(0, os.SEEK_END)
    size = log.tell()
    log.seek(max(0, size - limit))
    return log.read().decode("utf-8", errors="replace").strip()


def _write_candidates(output: Path, sites: list[dict[str, str | int]]) -> None:
    destination = output / "protoloom-candidates.json"
    payload = json.dumps({"candidate_sites": sites}, indent=2) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output,
            prefix=".protoloom-candidates.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(destination)
    except BaseException:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink()
        raise


def _index_candidates(output: Path) -> tuple[int, int]:
    sites: list[dict[str, str | int]] = []
    needles = ("newMessageInfo(", "new RawMessageInfo(")
    source_count = 0
    source_bytes = 0
    for source in output.rglob("*.java"):
        source_count += 1
        if source_count > MAX_JADX_SOURCES:
            raise JadxError(f"jadx produced more than {MAX_JADX_SOURCES} Java sources")
        try:
            if source.is_symlink():
                raise JadxError(f"jadx produced a symlinked Java source: {source}")
            with source.open("rb") as stream:
                status = os.fstat(stream.fileno())
                if not stat.S_ISREG(status.st_mode):
                    raise JadxError(f"jadx produced a non-file Java source: {source}")
                if status.st_size > MAX_JADX_SOURCE_SIZE:
                    raise JadxError(
                        f"jadx Java source exceeds {MAX_JADX_SOURCE_SIZE} bytes"
                    )
                data = stream.read(MAX_JADX_SOURCE_SIZE + 1)
            if len(data) > MAX_JADX_SOURCE_SIZE:
                raise JadxError(
                    f"jadx Java source exceeds {MAX_JADX_SOURCE_SIZE} bytes"
                )
            source_bytes += len(data)
            if source_bytes > MAX_JADX_SOURCE_TOTAL:
                raise JadxError(
                    f"jadx Java sources exceed {MAX_JADX_SOURCE_TOTAL} bytes"
                )
            lines = data.decode("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            if any(needle in line for needle in needles):
                if len(sites) >= MAX_JADX_CANDIDATES:
                    raise JadxError(
                        f"jadx produced more than {MAX_JADX_CANDIDATES} candidates"
                    )
                sites.append(
                    {
                        "file": source.relative_to(output).as_posix(),
                        "line": number,
                        "context": line.strip()[:500],
                    }
                )
    _write_candidates(output, sites)
    return source_count, len(sites)

import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MAX_PROTO_SOURCE_SIZE = 16 * 1024 * 1024
MAX_DESCRIPTOR_SET_SIZE = 64 * 1024 * 1024
MAX_COMPILER_DIAGNOSTIC_SIZE = 64 * 1024
MAX_PROTO_NAME_BYTES = 255


@dataclass(frozen=True, slots=True)
class CompileResult:
    success: bool
    stderr: str
    descriptor_set: bytes | None = None


def compile_proto(
    source: str,
    name: str = "recovered.proto",
    *,
    timeout_seconds: float = 30.0,
) -> CompileResult:
    if timeout_seconds <= 0:
        raise ValueError("compiler timeout must be positive")
    encoded_source = source.encode("utf-8")
    if len(encoded_source) > MAX_PROTO_SOURCE_SIZE:
        raise ValueError(f"proto source exceeds {MAX_PROTO_SOURCE_SIZE} bytes")
    protoc = shutil.which("protoc")
    command = (
        [protoc] if protoc is not None else [sys.executable, "-m", "grpc_tools.protoc"]
    )
    safe_name = Path(name).name
    if (
        safe_name in {"", ".", ".."}
        or len(os.fsencode(safe_name)) > MAX_PROTO_NAME_BYTES
        or any(
            unicodedata.category(character).startswith("C") for character in safe_name
        )
    ):
        raise ValueError("unsafe proto file name")
    with tempfile.TemporaryDirectory(prefix="protoloom-") as directory:
        root = Path(directory)
        proto = root / safe_name
        output = root / "compiled.desc"
        proto.write_bytes(encoded_source)
        with tempfile.TemporaryFile() as diagnostic:
            process = subprocess.Popen(
                [
                    *command,
                    f"--proto_path={root}",
                    f"--descriptor_set_out={output}",
                    str(proto),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=diagnostic,
                start_new_session=True,
            )
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                _kill_process_group(process)
                return CompileResult(
                    False, f"compiler exceeded {timeout_seconds:g}s timeout"
                )
            except BaseException:
                _kill_process_group(process)
                raise
            stderr = _read_diagnostic(diagnostic)
        payload = _read_descriptor(output) if process.returncode == 0 else None
        return CompileResult(process.returncode == 0, stderr, payload)


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _read_diagnostic(stream: BinaryIO) -> str:
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(max(0, size - MAX_COMPILER_DIAGNOSTIC_SIZE))
    return stream.read().decode("utf-8", errors="replace")


def _read_descriptor(path: Path) -> bytes:
    with path.open("rb") as stream:
        status = os.fstat(stream.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise ValueError("compiler descriptor set is not a regular file")
        if status.st_size > MAX_DESCRIPTOR_SET_SIZE:
            raise ValueError(f"descriptor set exceeds {MAX_DESCRIPTOR_SET_SIZE} bytes")
        payload = stream.read(MAX_DESCRIPTOR_SET_SIZE + 1)
    if len(payload) > MAX_DESCRIPTOR_SET_SIZE:
        raise ValueError(f"descriptor set exceeds {MAX_DESCRIPTOR_SET_SIZE} bytes")
    return payload

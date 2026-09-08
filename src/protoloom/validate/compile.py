import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

MAX_PROTO_SOURCE_SIZE = 16 * 1024 * 1024
MAX_DESCRIPTOR_SET_SIZE = 64 * 1024 * 1024


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
    with tempfile.TemporaryDirectory(prefix="protoloom-") as directory:
        root = Path(directory)
        proto = root / safe_name
        output = root / "compiled.desc"
        proto.write_bytes(encoded_source)
        try:
            process = subprocess.run(
                [
                    *command,
                    f"--proto_path={root}",
                    f"--descriptor_set_out={output}",
                    str(proto),
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return CompileResult(
                False, f"compiler exceeded {timeout_seconds:g}s timeout"
            )
        payload = _read_descriptor(output) if process.returncode == 0 else None
        return CompileResult(process.returncode == 0, process.stderr, payload)


def _read_descriptor(path: Path) -> bytes:
    if path.stat().st_size > MAX_DESCRIPTOR_SET_SIZE:
        raise ValueError(f"descriptor set exceeds {MAX_DESCRIPTOR_SET_SIZE} bytes")
    with path.open("rb") as stream:
        payload = stream.read(MAX_DESCRIPTOR_SET_SIZE + 1)
    if len(payload) > MAX_DESCRIPTOR_SET_SIZE:
        raise ValueError(f"descriptor set exceeds {MAX_DESCRIPTOR_SET_SIZE} bytes")
    return payload

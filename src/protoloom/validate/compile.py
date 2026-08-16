import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CompileResult:
    success: bool
    stderr: str
    descriptor_set: bytes | None = None


def compile_proto(source: str, name: str = "recovered.proto") -> CompileResult:
    protoc = shutil.which("protoc")
    command = (
        [protoc] if protoc is not None else [sys.executable, "-m", "grpc_tools.protoc"]
    )
    safe_name = Path(name).name
    with tempfile.TemporaryDirectory(prefix="protoloom-") as directory:
        root = Path(directory)
        proto = root / safe_name
        output = root / "compiled.desc"
        proto.write_text(source, encoding="utf-8")
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
        )
        payload = output.read_bytes() if process.returncode == 0 else None
        return CompileResult(process.returncode == 0, process.stderr, payload)

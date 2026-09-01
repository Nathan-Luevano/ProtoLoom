import json
from pathlib import Path
from zipfile import ZipFile

from google.protobuf.descriptor_pb2 import FileDescriptorSet
from pytest import MonkeyPatch
from typer.testing import CliRunner

from protoloom.cli import _dex_inputs, _find, app
from protoloom.container.detect import ContainerKind, Detection
from protoloom.extract.jadx import JadxError, JadxResult
from protoloom.model import Confidence, Field, Message, RecoveredSchema

runner = CliRunner()


def test_demo_artifact_runs_through_inspect_and_extract(tmp_path: Path) -> None:
    demo_dir = tmp_path / "demo"
    demo = runner.invoke(app, ["demo", "--output", str(demo_dir)])
    assert demo.exit_code == 0, demo.output
    assert "PASS: recovered 1/1 schema" in demo.output

    descriptor = demo_dir / "demo.desc"
    inspected = runner.invoke(app, ["inspect", str(descriptor)])
    assert inspected.exit_code == 0, inspected.output
    assert "kind: unknown" in inspected.output

    recovered_dir = tmp_path / "recovered"
    recovered = runner.invoke(
        app, ["extract", str(descriptor), "--output", str(recovered_dir)]
    )
    assert recovered.exit_code == 0, recovered.output
    assert "bail-outs: 0; recovered files: 1" in recovered.output
    assert "bytes payload = 1;" in (recovered_dir / "demo.proto").read_text()
    recovery = json.loads((recovered_dir / "recovery.json").read_text())
    assert recovery["schemas"][0]["name"] == "demo.proto"
    emitted = FileDescriptorSet.FromString((recovered_dir / "demo.desc").read_bytes())
    assert [item.name for item in emitted.file] == ["demo.proto"]
    assert (recovered_dir / "dashboard/index.html").is_file()


def test_inspect_and_extract_reject_missing_input(tmp_path: Path) -> None:
    missing = tmp_path / "missing.bin"
    for command in ("inspect", "extract"):
        result = runner.invoke(app, [command, str(missing)])
        assert result.exit_code == 2
        assert "file does not exist" in result.output


def test_extract_reports_absent_schema_evidence(tmp_path: Path) -> None:
    binary = tmp_path / "empty.bin"
    binary.write_bytes(b"not a protobuf descriptor")

    result = runner.invoke(app, ["extract", str(binary)])

    assert result.exit_code == 2
    assert "no recoverable schema evidence found" in result.output


def test_demo_uses_temporary_output_by_default(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    output = tmp_path / "default-demo"
    monkeypatch.setattr("protoloom.cli.tempfile.mkdtemp", lambda prefix: str(output))

    result = runner.invoke(app, ["demo"])

    assert result.exit_code == 0, result.output
    assert str(output) in result.output
    assert (output / "demo.proto").is_file()


def test_inspect_reports_android_archive_inventory(tmp_path: Path) -> None:
    apk = tmp_path / "sample.apk"
    with ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("classes.dex", b"dex\n039\x00")
        archive.writestr("lib/arm64-v8a/libsample.so", b"\x7fELF")

    result = runner.invoke(app, ["inspect", str(apk)])

    assert result.exit_code == 0, result.output
    assert "kind: apk" in result.output
    assert "entries: 3" in result.output
    assert "dex: 1" in result.output
    assert "native: 1" in result.output


def test_inspect_reports_real_elf_shape() -> None:
    binary = next(
        path for path in (Path("/bin/sh"), Path("/usr/bin/env")) if path.exists()
    )

    result = runner.invoke(app, ["inspect", str(binary)])

    assert result.exit_code == 0, result.output
    assert "kind: elf" in result.output
    assert "bits:" in result.output
    assert "sections:" in result.output
    assert "segments:" in result.output
    assert "go: no" in result.output


def test_extract_refuses_jadx_for_unsupported_container(tmp_path: Path) -> None:
    binary = tmp_path / "unknown.bin"
    binary.write_bytes(b"unknown")

    result = runner.invoke(app, ["extract", str(binary), "--jadx"])

    assert result.exit_code == 2
    assert "--jadx only supports APK, AAB, and DEX inputs" in result.output


def test_extract_reports_jadx_failure(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    binary = tmp_path / "classes.dex"
    binary.write_bytes(b"dex")
    monkeypatch.setattr("protoloom.cli._find", lambda path: [])
    monkeypatch.setattr(
        "protoloom.cli._find_lite",
        lambda path, allow_heuristic: ([], [], {}, {}),
    )
    monkeypatch.setattr(
        "protoloom.cli.detect", lambda path: Detection(ContainerKind.DEX)
    )

    def fail_jadx(path: Path, output: Path, timeout_seconds: float) -> None:
        raise JadxError("jadx unavailable")

    monkeypatch.setattr("protoloom.cli.decompile_with_jadx", fail_jadx)
    result = runner.invoke(app, ["extract", str(binary), "--jadx"])

    assert result.exit_code == 2
    assert "jadx fallback failed: jadx unavailable" in result.output


def test_extract_reports_retained_jadx_context(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    binary = tmp_path / "classes.dex"
    binary.write_bytes(b"dex")
    monkeypatch.setattr("protoloom.cli._find", lambda path: [])
    monkeypatch.setattr(
        "protoloom.cli._find_lite",
        lambda path, allow_heuristic: ([], [], {}, {}),
    )
    monkeypatch.setattr(
        "protoloom.cli.detect", lambda path: Detection(ContainerKind.DEX)
    )
    calls: list[float] = []

    def retain(path: Path, output: Path, timeout_seconds: float) -> JadxResult:
        calls.append(timeout_seconds)
        return JadxResult(output, 4, 2, "")

    monkeypatch.setattr("protoloom.cli.decompile_with_jadx", retain)
    result = runner.invoke(
        app, ["extract", str(binary), "--jadx", "--jadx-timeout", "3.5"]
    )

    assert result.exit_code == 2
    assert calls == [3.5]
    assert (
        "retained 4 Java sources and indexed 2 protobuf metadata sites" in result.output
    )


def test_archive_scanning_finds_and_deduplicates_descriptors(tmp_path: Path) -> None:
    descriptor = FileDescriptorSet()
    file_descriptor = descriptor.file.add(
        name="embedded.proto", package="embedded", syntax="proto3"
    )
    file_descriptor.message_type.add(name="Embedded")
    payload = file_descriptor.SerializeToString()
    dex = b"dex\n039\x00" + payload
    apk = tmp_path / "embedded.apk"
    with ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"manifest")
        archive.writestr("classes.dex", dex)
        archive.writestr("assets/schema.pb", payload)

    findings = _find(apk)

    assert [finding.descriptor.name for finding in findings] == ["embedded.proto"]
    assert findings[0].source in {"classes.dex", "assets/schema.pb"}
    assert _dex_inputs(apk) == [("classes.dex", dex)]


def test_dex_inputs_ignore_non_android_container(tmp_path: Path) -> None:
    binary = tmp_path / "unknown.bin"
    binary.write_bytes(b"unknown")
    assert _dex_inputs(binary) == []


def test_extract_compiles_lite_schema_and_honors_heuristic_flag(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    binary = tmp_path / "classes.dex"
    binary.write_bytes(b"dex")
    schema = RecoveredSchema(
        name="lite.proto",
        package="demo",
        messages=[
            Message(
                "Lite",
                fields=[Field("value", 1, "string", Confidence.HIGH)],
            )
        ],
    )
    flags: list[bool] = []

    def find_lite(path: Path, *, allow_heuristic: bool = False) -> object:
        flags.append(allow_heuristic)
        lineage = {("demo", "lite.proto"): ("Ldemo/Lite;", None)}
        return [schema], [], lineage, {}

    monkeypatch.setattr("protoloom.cli._find", lambda path: [])
    monkeypatch.setattr("protoloom.cli._find_lite", find_lite)
    output = tmp_path / "output"
    result = runner.invoke(
        app,
        ["extract", str(binary), "--allow-heuristic-lite", "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert flags == [True]
    assert "string value = 1;" in (output / "lite.proto").read_text()
    assert FileDescriptorSet.FromString((output / "classes.desc").read_bytes()).file


def test_doctor_reports_required_and_optional_tools() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    tools = json.loads(result.output)
    assert set(tools) == {"protoc", "jadx (optional)", "docker (optional)"}
    assert all(isinstance(value, str) for value in tools.values())


def test_bench_reports_invalid_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["bench", "--corpus", str(manifest)])

    assert result.exit_code == 2
    assert "benchmark failed:" in result.output


def test_extract_reports_uncompilable_recovery(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    binary = tmp_path / "classes.dex"
    binary.write_bytes(b"dex")
    schema = RecoveredSchema(name="broken.proto", messages=[Message("Broken")])
    monkeypatch.setattr("protoloom.cli._find", lambda path: [])
    monkeypatch.setattr(
        "protoloom.cli._find_lite",
        lambda path, allow_heuristic: ([schema], [], {}, {}),
    )
    monkeypatch.setattr(
        "protoloom.cli._compiled_descriptors",
        lambda schema: (_ for _ in ()).throw(ValueError("protoc rejected schema")),
    )

    result = runner.invoke(app, ["extract", str(binary)])

    assert result.exit_code == 2
    assert "recovery failed: protoc rejected schema" in result.output

import json
from pathlib import Path

from google.protobuf.descriptor_pb2 import FileDescriptorSet
from pytest import MonkeyPatch
from typer.testing import CliRunner

from protoloom.cli import app

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

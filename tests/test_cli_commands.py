import json
from pathlib import Path

from google.protobuf.descriptor_pb2 import FileDescriptorSet
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

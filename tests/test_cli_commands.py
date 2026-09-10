import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from zipfile import ZipFile

import pytest
from click import unstyle
from google.protobuf.descriptor_pb2 import FileDescriptorSet
from pytest import MonkeyPatch
from typer.testing import CliRunner

from protoloom.cli import (
    _atomic_write,
    _dex_inputs,
    _find,
    _output_names,
    _previous_artifacts,
    _publish_outputs,
    _remove_stale_artifacts,
    app,
)
from protoloom.container.detect import ContainerKind, Detection
from protoloom.extract.gotags import GoTagExtraction
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


@pytest.mark.parametrize(
    ("command", "message"),
    [("inspect", "inspection failed"), ("extract", "extraction failed")],
)
@pytest.mark.parametrize(
    "payload",
    [b"dex\n039\x00broken", b"\x7fELF" + bytes(12), b"\xcf\xfa\xed\xfebroken"],
)
def test_commands_report_malformed_containers_without_traceback(
    tmp_path: Path, command: str, message: str, payload: bytes
) -> None:
    malformed = tmp_path / "malformed.dex"
    malformed.write_bytes(payload)

    result = runner.invoke(app, [command, str(malformed)], color=False)

    assert result.exit_code == 2
    assert message in result.output
    assert "Traceback" not in result.output


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


def test_demo_rolls_back_partial_publication(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    output = tmp_path / "demo"
    real_replace = Path.replace

    def fail_descriptor(source: Path, target: Path) -> Path:
        if target == output / "demo.desc":
            raise OSError("publication failed")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_descriptor)
    result = runner.invoke(app, ["demo", "-o", str(output)])
    assert result.exit_code == 2
    assert "demo failed: publication failed" in result.output
    assert not (output / "demo.proto").exists()
    assert not (output / "demo.desc").exists()
    assert not tuple(output.glob(".*"))


def test_demo_rejects_symlinked_output(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    output = tmp_path / "demo"
    output.symlink_to(victim, target_is_directory=True)
    result = runner.invoke(app, ["demo", "-o", str(output)])
    assert result.exit_code == 2
    assert "demo failed: output directory is a symlink" in result.output
    assert list(victim.iterdir()) == []


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


def test_inspect_reports_dex_shape(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    dex = tmp_path / "classes.dex"
    dex.write_bytes(b"dex")
    monkeypatch.setattr(
        "protoloom.cli.detect", lambda path: Detection(ContainerKind.DEX, "039")
    )
    fake = SimpleNamespace(
        strings=["a", "b"], type_ids=[1], methods=[1, 2], classes=[1]
    )
    monkeypatch.setattr("protoloom.cli.DexFile.from_path", lambda path: fake)

    result = runner.invoke(app, ["inspect", str(dex)])

    assert result.exit_code == 0, result.output
    assert "detail: 039" in result.output
    assert "strings: 2" in result.output
    assert "types: 1" in result.output
    assert "methods: 2" in result.output
    assert "classes: 1" in result.output


def test_inspect_reports_macho_sections(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    binary = tmp_path / "sample.macho"
    binary.write_bytes(b"macho")
    monkeypatch.setattr(
        "protoloom.cli.detect", lambda path: Detection(ContainerKind.MACHO)
    )
    monkeypatch.setattr(
        "protoloom.cli.MachOFile.from_path",
        lambda path: SimpleNamespace(sections=[1, 2, 3]),
    )

    result = runner.invoke(app, ["inspect", str(binary)])

    assert result.exit_code == 0, result.output
    assert "sections: 3" in result.output


def test_extract_refuses_jadx_for_unsupported_container(tmp_path: Path) -> None:
    binary = tmp_path / "unknown.bin"
    binary.write_bytes(b"unknown")

    result = runner.invoke(app, ["extract", str(binary), "--jadx"], color=False)

    assert result.exit_code == 2
    assert "--jadx only supports APK, AAB, and DEX inputs" in " ".join(
        unstyle(result.output).split()
    )


def test_extract_reports_jadx_failure(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    binary = tmp_path / "classes.dex"
    binary.write_bytes(b"dex")
    monkeypatch.setattr("protoloom.cli._find", lambda path, **kwargs: [])
    monkeypatch.setattr(
        "protoloom.cli._find_lite",
        lambda path, allow_heuristic, **kwargs: ([], [], {}, {}),
    )
    monkeypatch.setattr("protoloom.cli._find_wire", lambda path, **kwargs: ([], {}, {}))
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
    monkeypatch.setattr("protoloom.cli._find", lambda path, **kwargs: [])
    monkeypatch.setattr(
        "protoloom.cli._find_lite",
        lambda path, allow_heuristic, **kwargs: ([], [], {}, {}),
    )
    monkeypatch.setattr("protoloom.cli._find_wire", lambda path, **kwargs: ([], {}, {}))
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


def test_extract_reuses_loaded_dex_inputs(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    binary = tmp_path / "input.bin"
    binary.write_bytes(b"input")
    shared = [("classes.dex", b"dex")]
    loads = 0
    received: list[list[tuple[str, bytes]]] = []

    def load(path: Path) -> list[tuple[str, bytes]]:
        nonlocal loads
        loads += 1
        return shared

    def find(path: Path, *, dex_inputs: list[tuple[str, bytes]]) -> list[object]:
        received.append(dex_inputs)
        return []

    def find_lite(path: Path, **kwargs: object) -> tuple[object, ...]:
        received.append(cast(list[tuple[str, bytes]], kwargs["dex_inputs"]))
        return [], [], {}, {}

    def find_wire(path: Path, **kwargs: object) -> tuple[object, ...]:
        received.append(cast(list[tuple[str, bytes]], kwargs["dex_inputs"]))
        return [], {}, {}

    monkeypatch.setattr("protoloom.cli._dex_inputs", load)
    monkeypatch.setattr("protoloom.cli._find", find)
    monkeypatch.setattr("protoloom.cli._find_lite", find_lite)
    monkeypatch.setattr("protoloom.cli._find_wire", find_wire)

    result = runner.invoke(app, ["extract", str(binary)])

    assert result.exit_code == 2
    assert loads == 1
    assert all(item is shared for item in received)


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

    def find_lite(
        path: Path, *, allow_heuristic: bool = False, **kwargs: object
    ) -> object:
        flags.append(allow_heuristic)
        lineage = {("demo", "lite.proto"): ("Ldemo/Lite;", None)}
        return [schema], [], lineage, {}

    monkeypatch.setattr("protoloom.cli._find", lambda path, **kwargs: [])
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


def test_extract_emits_descriptor_free_go_schema(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    binary = tmp_path / "go-binary"
    binary.write_bytes(b"ELF")
    schema = RecoveredSchema(
        name="Record.proto",
        syntax="proto3",
        messages=[
            Message(
                "Record",
                fields=[Field("id", 1, "uint64", Confidence.CERTAIN)],
            )
        ],
    )
    monkeypatch.setattr("protoloom.cli._find", lambda path, **kwargs: [])
    monkeypatch.setattr(
        "protoloom.cli._find_go_tags", lambda path: GoTagExtraction((schema,), ())
    )
    output = tmp_path / "output"

    result = runner.invoke(app, ["extract", str(binary), "-o", str(output)])

    assert result.exit_code == 0, result.output
    assert "uint64 id = 1;" in (output / "Record.proto").read_text()


def test_doctor_reports_required_and_optional_tools() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    tools = json.loads(result.output)
    assert set(tools) == {"protoc", "jadx (optional)", "docker (optional)"}
    assert all(isinstance(value, str) for value in tools.values())
    assert tools["protoc"] != "missing"


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
    monkeypatch.setattr("protoloom.cli._find", lambda path, **kwargs: [])
    monkeypatch.setattr(
        "protoloom.cli._find_lite",
        lambda path, allow_heuristic, **kwargs: ([schema], [], {}, {}),
    )
    monkeypatch.setattr(
        "protoloom.cli._compiled_descriptors",
        lambda schema: (_ for _ in ()).throw(ValueError("protoc rejected schema")),
    )

    result = runner.invoke(app, ["extract", str(binary)])

    assert result.exit_code == 2
    assert "recovery failed: protoc rejected schema" in result.output


def test_extract_reports_descriptor_assembly_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    binary = tmp_path / "input.bin"
    binary.write_bytes(b"descriptor")
    demo_dir = tmp_path / "demo"
    assert runner.invoke(app, ["demo", "-o", str(demo_dir)]).exit_code == 0
    findings = _find(demo_dir / "demo.desc")
    monkeypatch.setattr("protoloom.cli._find", lambda path, **kwargs: findings)
    monkeypatch.setattr(
        "protoloom.cli._combined_lite_descriptors",
        lambda *args: (_ for _ in ()).throw(ValueError("duplicate descriptor")),
    )

    result = runner.invoke(app, ["extract", str(binary)])

    assert result.exit_code == 2
    assert "descriptor-set assembly failed: duplicate descriptor" in result.output


def test_extract_serializes_descriptor_set_before_writing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    binary = tmp_path / "input.bin"
    binary.write_bytes(b"descriptor")
    demo_dir = tmp_path / "demo"
    assert runner.invoke(app, ["demo", "-o", str(demo_dir)]).exit_code == 0
    findings = _find(demo_dir / "demo.desc")
    monkeypatch.setattr("protoloom.cli._find", lambda path, **kwargs: findings)
    monkeypatch.setattr(
        "protoloom.cli.emit_descriptor_set",
        lambda descriptors: (_ for _ in ()).throw(ValueError("conflict")),
    )
    output = tmp_path / "output"

    result = runner.invoke(app, ["extract", str(binary), "-o", str(output)])

    assert result.exit_code == 2
    assert "descriptor-set assembly failed: conflict" in result.output
    assert not output.exists()


@pytest.mark.parametrize(
    "emitter",
    ["emit_report", "emit_dashboard", "emit_json"],
)
def test_extract_renders_all_outputs_before_writing(
    tmp_path: Path, monkeypatch: MonkeyPatch, emitter: str
) -> None:
    binary = tmp_path / "input.bin"
    binary.write_bytes(b"descriptor")
    demo_dir = tmp_path / "demo"
    assert runner.invoke(app, ["demo", "-o", str(demo_dir)]).exit_code == 0
    findings = _find(demo_dir / "demo.desc")
    monkeypatch.setattr("protoloom.cli._find", lambda path, **kwargs: findings)

    def fail(*args: object, **kwargs: object) -> str:
        raise ValueError("output limit")

    monkeypatch.setattr(f"protoloom.cli.{emitter}", fail)
    output = tmp_path / "output"
    result = runner.invoke(app, ["extract", str(binary), "-o", str(output)])
    assert result.exit_code == 2
    assert "output generation failed: output limit" in result.output
    assert not output.exists()


@pytest.mark.parametrize("existing_output", [False, True])
def test_extract_reports_reconciliation_limit_before_writing(
    tmp_path: Path, monkeypatch: MonkeyPatch, existing_output: bool
) -> None:
    binary = tmp_path / "input.bin"
    binary.write_bytes(b"descriptor")
    demo_dir = tmp_path / "demo"
    assert runner.invoke(app, ["demo", "-o", str(demo_dir)]).exit_code == 0
    findings = _find(demo_dir / "demo.desc")
    monkeypatch.setattr("protoloom.cli._find", lambda path, **kwargs: findings)

    def fail(*args: object, **kwargs: object) -> object:
        raise ValueError("item limit")

    monkeypatch.setattr("protoloom.cli.reconcile", fail)
    output = tmp_path / "output"
    sentinel = output / "existing.txt"
    if existing_output:
        output.mkdir()
        sentinel.write_text("preserve")
    result = runner.invoke(app, ["extract", str(binary), "-o", str(output)])
    assert result.exit_code == 2
    assert "reconciliation failed: item limit" in result.output
    assert output.exists() is existing_output
    if existing_output:
        assert sentinel.read_text() == "preserve"


@pytest.mark.parametrize(
    "names",
    [
        ("a/types.proto", "b/types.proto"),
        ("Types.proto", "types.proto"),
        ("report.md", "safe.proto"),
        ("input.desc", "safe.proto"),
    ],
)
def test_extract_rejects_output_name_collisions_before_writing(
    tmp_path: Path, monkeypatch: MonkeyPatch, names: tuple[str, str]
) -> None:
    binary = tmp_path / "input.bin"
    binary.write_bytes(b"input")
    schemas = [
        RecoveredSchema(name=name, messages=[Message("Record")]) for name in names
    ]
    monkeypatch.setattr("protoloom.cli._find", lambda path, **kwargs: [])
    monkeypatch.setattr(
        "protoloom.cli._find_lite",
        lambda path, allow_heuristic, **kwargs: (schemas, [], {}, {}),
    )
    monkeypatch.setattr("protoloom.cli._find_wire", lambda path, **kwargs: ([], {}, {}))
    output = tmp_path / "output"

    result = runner.invoke(app, ["extract", str(binary), "-o", str(output)])

    assert result.exit_code == 2
    assert "recovery failed: output name collision" in result.output
    assert not output.exists()


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "bad\n.proto", "bad\x1b.proto", f"{'a' * 256}.proto"],
)
def test_output_names_reject_unsafe_file_names(name: str) -> None:
    schema = RecoveredSchema(name=name, messages=[Message("Record")])

    with pytest.raises(ValueError, match="unsafe schema output name"):
        _output_names([schema], "input.desc")


def test_extract_replaces_file_symlink_without_following_it(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    assert runner.invoke(app, ["demo", "-o", str(demo)]).exit_code == 0
    output = tmp_path / "output"
    output.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("preserve", encoding="utf-8")
    recovery = output / "recovery.json"
    recovery.symlink_to(victim)

    result = runner.invoke(app, ["extract", str(demo / "demo.desc"), "-o", str(output)])

    assert result.exit_code == 0, result.output
    assert victim.read_text() == "preserve"
    assert not recovery.is_symlink()
    assert json.loads(recovery.read_text())["schemas"][0]["name"] == "demo.proto"


def test_previous_artifacts_reads_safe_output_names(tmp_path: Path) -> None:
    (tmp_path / "recovery.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    "old.proto",
                    "old.desc",
                    "report.md",
                    "../outside.proto",
                    1,
                ]
            }
        ),
        encoding="utf-8",
    )

    assert _previous_artifacts(tmp_path) == {"old.proto", "old.desc"}


def test_previous_artifacts_rejects_excessive_entries(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    (tmp_path / "recovery.json").write_text(
        json.dumps({"artifacts": ["first.proto", "second.proto"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("protoloom.cli.MAX_PREVIOUS_ARTIFACTS", 1)

    assert _previous_artifacts(tmp_path) == set()


def test_atomic_write_respects_process_umask(tmp_path: Path) -> None:
    output = tmp_path / "output"
    previous = os.umask(0o077)
    try:
        _atomic_write(output, b"private")
    finally:
        os.umask(previous)

    assert output.read_bytes() == b"private"
    assert output.stat().st_mode & 0o777 == 0o600


def test_atomic_write_syncs_file_and_directory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    synced: list[int] = []
    real_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        synced.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    output = tmp_path / "output"
    _atomic_write(output, b"durable")
    assert output.read_bytes() == b"durable"
    assert len(synced) == 2


def test_atomic_write_closes_directory_after_sync_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    real_fsync = os.fsync
    real_close = os.close
    closed: list[int] = []
    sync_count = 0

    def fail_fsync(descriptor: int) -> None:
        nonlocal sync_count
        sync_count += 1
        if sync_count == 2:
            raise OSError("sync failed")
        real_fsync(descriptor)

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(os, "fsync", fail_fsync)
    monkeypatch.setattr(os, "close", record_close)
    with pytest.raises(OSError, match="sync failed"):
        _atomic_write(tmp_path / "output", b"payload")
    assert sync_count == 3
    assert closed


def test_output_publication_rolls_back_all_files(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"old first")
    second.write_bytes(b"old second")
    real_replace = Path.replace
    failed = False

    def fail_second(source: Path, target: Path) -> Path:
        nonlocal failed
        if not failed and target == second and source.name.startswith(".second."):
            failed = True
            raise OSError("publication failed")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_second)
    with pytest.raises(OSError, match="publication failed"):
        _publish_outputs([(first, b"new first"), (second, b"new second")])
    assert first.read_bytes() == b"old first"
    assert second.read_bytes() == b"old second"
    assert not tuple(tmp_path.glob(".*"))


def test_output_publication_rejects_duplicate_paths(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="duplicate output publication path"):
        _publish_outputs([(output, b"first"), (output, b"second")])
    assert output.read_bytes() == b"preserve"
    assert not tuple(tmp_path.glob(".*"))


def test_output_publication_rejects_directory_destination(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="publication path is not a file"):
        _publish_outputs([(output, b"replacement")])
    assert output.is_dir()
    assert sentinel.read_bytes() == b"preserve"
    assert not tuple(tmp_path.glob(".*"))


def test_output_publication_syncs_backup_removal(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    synced: list[int] = []
    real_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        synced.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    output = tmp_path / "output"
    output.write_bytes(b"old")
    _atomic_write(output, b"new")
    assert output.read_bytes() == b"new"
    assert len(synced) == 3
    assert not tuple(tmp_path.glob(".*"))


def test_output_publication_continues_after_restore_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    paths = [tmp_path / name for name in ("first", "second", "third")]
    for path in paths:
        path.write_bytes(f"old {path.name}".encode())
    real_replace = Path.replace
    publication_failed = False

    def fail_publication_and_restore(source: Path, target: Path) -> Path:
        nonlocal publication_failed
        if (
            target == paths[2]
            and source.read_bytes() == b"new third"
            and not publication_failed
        ):
            publication_failed = True
            raise OSError("publication failed")
        if (
            target == paths[1]
            and source.read_bytes() == b"old second"
            and publication_failed
        ):
            raise OSError("restore failed")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_publication_and_restore)
    outputs = [(path, f"new {path.name}".encode()) for path in paths]
    with pytest.raises(OSError, match="publication failed") as captured:
        _publish_outputs(outputs)
    assert paths[0].read_bytes() == b"old first"
    assert not paths[1].exists()
    assert paths[2].read_bytes() == b"old third"
    backups = tuple(tmp_path.glob(".second.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"old second"
    assert captured.value.__notes__ == [f"restore {paths[1]}: restore failed"]


@pytest.mark.parametrize("nested", [False, True])
def test_extract_rejects_symlinked_output_directories_before_writing(
    tmp_path: Path, nested: bool
) -> None:
    demo = tmp_path / "demo"
    assert runner.invoke(app, ["demo", "-o", str(demo)]).exit_code == 0
    target = tmp_path / "target"
    target.mkdir()
    output = tmp_path / "output"
    if nested:
        output.mkdir()
        (output / "dashboard").symlink_to(target, target_is_directory=True)
    else:
        output.symlink_to(target, target_is_directory=True)

    result = runner.invoke(app, ["extract", str(demo / "demo.desc"), "-o", str(output)])

    assert result.exit_code == 2
    assert "recovery failed:" in result.output
    assert list(target.iterdir()) == []


def test_extract_rejects_symlinked_output_before_jadx(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    binary = tmp_path / "input.dex"
    binary.write_bytes(b"dex")
    target = tmp_path / "target"
    target.mkdir()
    output = tmp_path / "output"
    output.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(
        "protoloom.cli.decompile_with_jadx",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("called")),
    )

    result = runner.invoke(app, ["extract", str(binary), "-o", str(output), "--jadx"])

    assert result.exit_code == 2
    assert "recovery failed: output directory is a symlink" in result.output
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("name", ["dashboard", "jadx"])
def test_extract_rejects_output_subdirectory_files(tmp_path: Path, name: str) -> None:
    demo = tmp_path / "demo"
    assert runner.invoke(app, ["demo", "-o", str(demo)]).exit_code == 0
    output = tmp_path / "output"
    output.mkdir()
    (output / name).write_text("occupied", encoding="utf-8")

    result = runner.invoke(app, ["extract", str(demo / "demo.desc"), "-o", str(output)])

    assert result.exit_code == 2
    assert f"recovery failed: {name} path is not a directory" in result.output


def test_extract_rejects_directory_at_artifact_path(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    assert runner.invoke(app, ["demo", "-o", str(demo)]).exit_code == 0
    output = tmp_path / "output"
    output.mkdir()
    occupied = output / "demo.proto"
    occupied.mkdir()

    result = runner.invoke(app, ["extract", str(demo / "demo.desc"), "-o", str(output)])

    assert result.exit_code == 2
    assert "recovery failed: output file path is not a file" in result.output
    assert occupied.is_dir()


def test_extract_rejects_directory_at_dashboard_index(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    assert runner.invoke(app, ["demo", "-o", str(demo)]).exit_code == 0
    output = tmp_path / "output"
    occupied = output / "dashboard" / "index.html"
    occupied.mkdir(parents=True)
    sentinel = occupied / "sentinel"
    sentinel.write_bytes(b"preserve")

    result = runner.invoke(app, ["extract", str(demo / "demo.desc"), "-o", str(output)])

    assert result.exit_code == 2
    assert "recovery failed: output file path is not a file" in result.output
    assert occupied.is_dir()
    assert sentinel.read_bytes() == b"preserve"


def test_extract_removes_only_manifested_stale_artifacts(tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    assert runner.invoke(app, ["demo", "-o", str(demo)]).exit_code == 0
    output = tmp_path / "output"
    first = runner.invoke(app, ["extract", str(demo / "demo.desc"), "-o", str(output)])
    assert first.exit_code == 0, first.output
    unrelated = output / "notes.txt"
    unrelated.write_text("preserve", encoding="utf-8")
    descriptor = FileDescriptorSet.FromString((demo / "demo.desc").read_bytes())
    descriptor.file[0].name = "other.proto"
    other = tmp_path / "other.desc"
    other.write_bytes(descriptor.SerializeToString())

    second = runner.invoke(app, ["extract", str(other), "-o", str(output)])

    assert second.exit_code == 0, second.output
    assert not (output / "demo.proto").exists()
    assert not (output / "demo.desc").exists()
    assert (output / "other.proto").is_file()
    assert (output / "other.desc").is_file()
    assert unrelated.read_text(encoding="utf-8") == "preserve"


def test_stale_artifact_removal_syncs_output_directory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    stale = tmp_path / "stale.proto"
    current = tmp_path / "current.proto"
    stale.write_bytes(b"stale")
    current.write_bytes(b"current")
    synced: list[int] = []
    real_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        synced.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    _remove_stale_artifacts(tmp_path, {stale.name, current.name}, {current.name})
    assert not stale.exists()
    assert current.read_bytes() == b"current"
    assert len(synced) == 1


def test_stale_artifact_partial_failure_syncs_prior_deletions(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    first = tmp_path / "first.proto"
    second = tmp_path / "second.proto"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    real_unlink = Path.unlink
    synced = 0

    def fail_second(path: Path, missing_ok: bool = False) -> None:
        if path == second:
            raise OSError("unlink failed")
        real_unlink(path, missing_ok=missing_ok)

    def record_sync(descriptor: int) -> None:
        nonlocal synced
        synced += 1

    monkeypatch.setattr(Path, "unlink", fail_second)
    monkeypatch.setattr(os, "fsync", record_sync)
    with pytest.raises(OSError, match="unlink failed"):
        _remove_stale_artifacts(tmp_path, {first.name, second.name}, set())
    assert not first.exists()
    assert second.read_bytes() == b"second"
    assert synced == 1

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet

from protoloom.bench.corpus import CorpusError, load_manifest
from protoloom.bench.runner import render_report, run_corpus
from protoloom.container.apk import AndroidArchive
from protoloom.container.detect import ContainerKind, detect
from protoloom.container.dex import DexFile
from protoloom.container.elf import ElfFile
from protoloom.container.macho import MachOFile
from protoloom.decode.descpb import decode_file_descriptor
from protoloom.decode.lite import decode_lite_finding
from protoloom.emit.dashboard import emit_dashboard
from protoloom.emit.descset import emit_descriptor_set
from protoloom.emit.jsonout import emit_json
from protoloom.emit.proto import emit_proto
from protoloom.emit.report import emit_report
from protoloom.extract.descriptor import DescriptorFinding, scan_descriptors
from protoloom.extract.gozip import scan_gzip_descriptors
from protoloom.extract.lite import extract_lite
from protoloom.model import RecoveredSchema
from protoloom.reconcile import reconcile
from protoloom.validate.compile import compile_proto

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _scan_blob(data: bytes, source: str) -> list[DescriptorFinding]:
    return scan_descriptors(data, source) + scan_gzip_descriptors(data, source)


def _find(path: Path) -> list[DescriptorFinding]:
    detection = detect(path)
    findings: list[DescriptorFinding] = []
    if detection.kind in {ContainerKind.APK, ContainerKind.AAB, ContainerKind.JAR}:
        archive = AndroidArchive(path)
        for entry in archive.inventory().entries:
            if entry.kind in {"dex", "native", "asset", "class"}:
                findings.extend(_scan_blob(archive.read(entry.name), entry.name))
    elif detection.kind is ContainerKind.ELF:
        elf = ElfFile.from_path(path)
        for section in elf.sections:
            if section.name in {".rodata", ".data.rel.ro", ".go.buildinfo"}:
                findings.extend(
                    _scan_blob(bytes(elf.section_data(section)), section.name)
                )
    elif detection.kind is ContainerKind.MACHO:
        macho = MachOFile.from_path(path)
        for index, region in enumerate(macho.protobuf_regions()):
            findings.extend(_scan_blob(bytes(region), f"Mach-O region {index}"))
    else:
        findings.extend(_scan_blob(path.read_bytes(), path.name))
    deduped: dict[str, DescriptorFinding] = {}
    for finding in findings:
        current = deduped.get(finding.descriptor.name)
        if current is None or finding.length > current.length:
            deduped[finding.descriptor.name] = finding
    return sorted(deduped.values(), key=lambda item: item.descriptor.name)


def _dex_inputs(path: Path) -> list[tuple[str, bytes]]:
    detection = detect(path)
    if detection.kind is ContainerKind.DEX:
        return [(path.name, path.read_bytes())]
    if detection.kind not in {ContainerKind.APK, ContainerKind.AAB}:
        return []
    archive = AndroidArchive(path)
    return [(entry.name, data) for entry, data in archive.iter_dex()]


def _find_lite(path: Path) -> tuple[list[RecoveredSchema], list[str]]:
    schemas: list[RecoveredSchema] = []
    bailouts: list[str] = []
    for source, data in _dex_inputs(path):
        dex = DexFile(data)
        extraction = extract_lite(dex)
        for finding in extraction.findings:
            try:
                schemas.append(decode_lite_finding(dex, finding, source))
            except ValueError as error:
                bailouts.append(f"{source}: {error}")
        bailouts.extend(
            f"{source}: method {item.containing_method}: {item.reason}"
            for item in extraction.bailouts
        )
    return schemas, bailouts


def _compiled_descriptors(schema: RecoveredSchema) -> list[FileDescriptorProto]:
    result = compile_proto(emit_proto(schema), Path(schema.name).name)
    if not result.success or result.descriptor_set is None:
        raise ValueError(f"emitted schema did not compile: {result.stderr.strip()}")
    descriptor_set = FileDescriptorSet.FromString(result.descriptor_set)
    return list(descriptor_set.file)


@app.command()
def inspect(path: Path) -> None:
    if not path.is_file():
        raise typer.BadParameter(f"file does not exist: {path}")
    detection = detect(path)
    typer.echo(f"kind: {detection.kind.value}")
    if detection.detail:
        typer.echo(f"detail: {detection.detail}")
    if detection.kind in {ContainerKind.APK, ContainerKind.AAB, ContainerKind.JAR}:
        inventory = AndroidArchive(path).inventory()
        typer.echo(f"entries: {len(inventory.entries)}")
        typer.echo(f"dex: {len(inventory.dex_files)}")
        typer.echo(f"native: {len(inventory.native_libraries)}")
    elif detection.kind is ContainerKind.DEX:
        dex = DexFile.from_path(path)
        typer.echo(f"strings: {len(dex.strings)}")
        typer.echo(f"types: {len(dex.type_ids)}")
        typer.echo(f"methods: {len(dex.methods)}")
        typer.echo(f"classes: {len(dex.classes)}")
    elif detection.kind is ContainerKind.ELF:
        elf = ElfFile.from_path(path)
        typer.echo(f"bits: {elf.bits}")
        typer.echo(f"sections: {len(elf.sections)}")
        typer.echo(f"segments: {len(elf.segments)}")
        typer.echo(f"go: {'yes' if elf.is_go_binary else 'no'}")
    elif detection.kind is ContainerKind.MACHO:
        typer.echo(f"sections: {len(MachOFile.from_path(path).sections)}")


@app.command()
def extract(
    path: Path,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("out"),
) -> None:
    if not path.is_file():
        raise typer.BadParameter(f"file does not exist: {path}")
    findings = _find(path)
    lite_schemas, bailouts = _find_lite(path)
    if not findings and not lite_schemas:
        typer.echo("no recoverable schema evidence found", err=True)
        for reason in bailouts:
            typer.echo(f"bail-out: {reason}", err=True)
        raise typer.Exit(2)
    schemas: list[RecoveredSchema] = []
    for finding in findings:
        schema = decode_file_descriptor(
            finding.descriptor, finding.source, f"0x{finding.offset:x}"
        )
        schemas.append(schema)
    schemas.extend(lite_schemas)
    reconciled = reconcile(schemas)
    descriptors = [finding.descriptor for finding in findings]
    certain_names = {finding.descriptor.name for finding in findings}
    prepared: list[tuple[RecoveredSchema, str]] = []
    for schema in reconciled.schemas:
        source = emit_proto(schema)
        if schema.name not in certain_names:
            try:
                descriptors.extend(_compiled_descriptors(schema))
            except ValueError as error:
                typer.echo(f"recovery failed: {error}", err=True)
                raise typer.Exit(2) from error
        prepared.append((schema, source))
    output.mkdir(parents=True, exist_ok=True)
    for schema, source in prepared:
        destination = output / Path(schema.name).name
        destination.write_text(source, encoding="utf-8")
        typer.echo(f"recovered {schema.name} -> {destination}")
    descriptors_by_name = {item.name: item for item in descriptors}
    (output / f"{path.stem}.desc").write_bytes(
        emit_descriptor_set(list(descriptors_by_name.values()))
    )
    conflicts = [asdict(conflict) for conflict in reconciled.conflicts]
    (output / "recovery.json").write_text(
        emit_json(reconciled.schemas, conflicts), encoding="utf-8"
    )
    (output / "report.md").write_text(
        emit_report(reconciled.schemas, bailouts), encoding="utf-8"
    )
    dashboard = output / "dashboard"
    dashboard.mkdir(exist_ok=True)
    (dashboard / "index.html").write_text(
        emit_dashboard(reconciled.schemas, reconciled.conflicts), encoding="utf-8"
    )
    typer.echo(
        f"bail-outs: {len(bailouts)}; recovered files: {len(reconciled.schemas)}"
    )


@app.command()
def doctor() -> None:
    import shutil

    checks = {
        "protoc": shutil.which("protoc"),
        "jadx (optional)": shutil.which("jadx"),
        "docker (optional)": shutil.which("docker"),
    }
    result = {name: value or "missing" for name, value in checks.items()}
    typer.echo(json.dumps(result, indent=2))


@app.command()
def demo(
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    descriptor = FileDescriptorProto(name="demo.proto", package="protoloom.demo")
    descriptor.syntax = "proto3"
    message = descriptor.message_type.add(name="RecoveredMessage")
    field = message.field.add(name="payload", number=1)
    field.label = field.LABEL_OPTIONAL
    field.type = field.TYPE_BYTES
    blob = b"stripped-binary\x00" + descriptor.SerializeToString() + b"\xff"
    if output is None:
        output = Path(tempfile.mkdtemp(prefix="protoloom-demo-"))
    output.mkdir(parents=True, exist_ok=True)
    finding = scan_descriptors(blob, "built-in demo")[0]
    schema = decode_file_descriptor(finding.descriptor, finding.source, "0x10")
    (output / "demo.proto").write_text(emit_proto(schema), encoding="utf-8")
    (output / "demo.desc").write_bytes(emit_descriptor_set([finding.descriptor]))
    typer.echo(f"PASS: recovered 1/1 schema with certain confidence -> {output}")


@app.command()
def bench(
    corpus: Annotated[str, typer.Option("--corpus")],
    per_target: Annotated[bool, typer.Option("--per-target")] = False,
) -> None:
    manifest_path = _corpus_path(corpus)
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    try:
        manifest = load_manifest(manifest_path)
        report = run_corpus(
            manifest, cache_root / "protoloom" / "bench" / manifest.name
        )
    except (CorpusError, OSError, ValueError) as error:
        typer.echo(f"benchmark failed: {error}", err=True)
        raise typer.Exit(2) from error
    typer.echo(render_report(report, per_target))


def _corpus_path(value: str) -> Path:
    direct = Path(value)
    candidates = (
        direct,
        Path("benchmarks") / "corpora" / value / "manifest.json",
        Path("benchmarks") / "corpora" / f"{value}.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise typer.BadParameter(f"corpus does not exist: {value}")


@app.callback()
def main() -> None:
    pass


if __name__ == "__main__":
    app()

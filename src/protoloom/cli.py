import json
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

import typer
from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet

from protoloom import __version__
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
from protoloom.extract.gotags import GoTagExtraction, scan_go_struct_tags
from protoloom.extract.gozip import scan_gzip_descriptors
from protoloom.extract.jadx import JadxError, decompile_with_jadx
from protoloom.extract.lite import extract_lite
from protoloom.model import EnumType, Message, RecoveredSchema
from protoloom.reconcile import reconcile
from protoloom.validate.compile import compile_proto

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _version_callback(value: bool) -> None:
    if value:
        try:
            installed_version = package_version("protoloom")
        except PackageNotFoundError:
            installed_version = __version__
        typer.echo(installed_version)
        raise typer.Exit


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
            if section.name in {".rodata", ".data.rel.ro", ".go.buildinfo"} or (
                section.name.startswith("protodesc_")
            ):
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


def _find_go_tags(path: Path) -> GoTagExtraction:
    if detect(path).kind is not ContainerKind.ELF:
        return GoTagExtraction((), ())
    elf = ElfFile.from_path(path)
    if not elf.is_go_binary:
        return GoTagExtraction((), ())
    return scan_go_struct_tags(elf, path.name)


def _find_lite(
    path: Path, *, allow_heuristic: bool = False
) -> tuple[
    list[RecoveredSchema],
    list[str],
    dict[tuple[str, str], tuple[str, str | None]],
    dict[tuple[str, str], dict[str, str | None]],
]:
    schemas: list[RecoveredSchema] = []
    bailouts: list[str] = []
    # Keyed by (package, name): two unrelated classes can share a bare file
    # name across different packages (e.g. two distinct "Relay" classes).
    lineage: dict[tuple[str, str], tuple[str, str | None]] = {}
    enum_lineage: dict[tuple[str, str], dict[str, str | None]] = {}
    for source, data in _dex_inputs(path):
        dex = DexFile(data)
        extraction = extract_lite(dex, allow_heuristic=allow_heuristic)
        for finding in extraction.findings:
            try:
                decoded = decode_lite_finding(dex, finding, source)
            except ValueError as error:
                bailouts.append(f"{source}: {error}")
                continue
            schemas.append(decoded.schema)
            key = (decoded.schema.package, decoded.schema.name)
            lineage[key] = (decoded.class_descriptor, decoded.enclosing_descriptor)
            if decoded.enum_enclosing:
                enum_lineage[key] = decoded.enum_enclosing
        bailouts.extend(
            f"{source}: method {item.containing_method}: {item.reason}"
            for item in extraction.bailouts
        )
    return schemas, bailouts, lineage, enum_lineage


def _compiled_descriptors(schema: RecoveredSchema) -> list[FileDescriptorProto]:
    result = compile_proto(emit_proto(schema), Path(schema.name).name)
    if not result.success or result.descriptor_set is None:
        raise ValueError(f"emitted schema did not compile: {result.stderr.strip()}")
    descriptor_set = FileDescriptorSet.FromString(result.descriptor_set)
    return list(descriptor_set.file)


def _walk_messages(messages: list[Message]) -> list[Message]:
    result: list[Message] = []
    stack = list(messages)
    while stack:
        message = stack.pop()
        result.append(message)
        stack.extend(message.messages)
    return result


def _apply_nested_renames(top_level: list[Message], renames: dict[str, str]) -> None:
    if not renames:
        return
    pattern = re.compile(
        "|".join(re.escape(name) for name in sorted(renames, key=len, reverse=True))
    )
    for message in _walk_messages(top_level):
        for item in message.fields:
            if item.type_name in renames:
                item.type_name = renames[item.type_name]
            elif "<" in item.type_name:
                item.type_name = pattern.sub(
                    lambda match: renames[match.group(0)], item.type_name
                )


def _lite_message_index(
    items: list[RecoveredSchema],
    lineage: dict[tuple[str, str], tuple[str, str | None]],
) -> tuple[dict[str, Message], dict[str, str | None]]:
    by_descriptor: dict[str, Message] = {}
    enclosing_of: dict[str, str | None] = {}
    for item in items:
        info = lineage.get((item.package, item.name))
        if info is None or not item.messages:
            continue
        own_descriptor, enclosing_descriptor = info
        by_descriptor[own_descriptor] = item.messages[0]
        enclosing_of[own_descriptor] = enclosing_descriptor
    return by_descriptor, enclosing_of


def _nested_lite_messages(
    items: list[RecoveredSchema],
    by_descriptor: dict[str, Message],
    enclosing_of: dict[str, str | None],
) -> list[Message]:
    attached: set[int] = set()
    renames: dict[str, str] = {}
    for own_descriptor, message in by_descriptor.items():
        enclosing_descriptor = enclosing_of.get(own_descriptor)
        if (
            enclosing_descriptor is None
            or enclosing_descriptor == own_descriptor
            or enclosing_descriptor not in by_descriptor
        ):
            continue
        parent = by_descriptor[enclosing_descriptor]
        # Some generators name a nested class after its own enclosing class,
        # e.g. AccessMethod$AccessMethod_Bridges. Strip the redundant prefix
        # once the real parent is known, so the emitted nested message keeps
        # the bare local name a ground-truth .proto would use.
        prefix = f"{parent.name}_"
        if message.name.startswith(prefix) and len(message.name) > len(prefix):
            new_name = message.name[len(prefix) :]
            renames[message.name] = new_name
            message.name = new_name
        parent.messages.append(message)
        attached.add(id(message))
    top_level = [
        message
        for item in items
        for message in item.messages
        if id(message) not in attached
    ]
    _apply_nested_renames(top_level, renames)
    return top_level


def _nested_lite_enums(
    items: list[RecoveredSchema],
    by_descriptor: dict[str, Message],
    enum_lineage: dict[tuple[str, str], dict[str, str | None]],
) -> tuple[list[EnumType], dict[str, str]]:
    enums_by_name: dict[str, EnumType] = {}
    enum_owner: dict[str, str | None] = {}
    conflicting_enums: set[str] = set()
    for item in items:
        item_enum_lineage = enum_lineage.get((item.package, item.name), {})
        for enum in item.enums:
            current = enums_by_name.get(enum.name)
            if current is None:
                enums_by_name[enum.name] = enum
                enum_owner[enum.name] = item_enum_lineage.get(enum.name)
            elif current.values != enum.values:
                conflicting_enums.add(enum.name)
    top_level_enums: list[EnumType] = []
    enum_renames: dict[str, str] = {}
    for name, enum in enums_by_name.items():
        if name in conflicting_enums:
            continue
        owner_descriptor = enum_owner.get(name)
        owner = by_descriptor.get(owner_descriptor) if owner_descriptor else None
        if owner is None:
            top_level_enums.append(enum)
            continue
        # Same redundant-prefix shape as nested messages, e.g.
        # MigrationPayload_OtpType once MigrationPayload is the known real
        # parent.
        prefix = f"{owner.name}_"
        if enum.name.startswith(prefix) and len(enum.name) > len(prefix):
            new_name = enum.name[len(prefix) :]
            enum_renames[enum.name] = new_name
            enum.name = new_name
        owner.enums.append(enum)
    return top_level_enums, enum_renames


def _combined_lite_descriptors(
    schemas: list[RecoveredSchema],
    certain_names: set[str],
    lineage: dict[tuple[str, str], tuple[str, str | None]],
    enum_lineage: dict[tuple[str, str], dict[str, str | None]],
) -> list[FileDescriptorProto]:
    groups: dict[tuple[str, str], list[RecoveredSchema]] = defaultdict(list)
    for schema in schemas:
        if schema.name not in certain_names:
            groups[(schema.package, schema.syntax)].append(schema)
    descriptors: list[FileDescriptorProto] = []
    for index, ((package, syntax), items) in enumerate(sorted(groups.items())):
        by_descriptor, enclosing_of = _lite_message_index(items, lineage)
        messages = _nested_lite_messages(items, by_descriptor, enclosing_of)
        top_level_enums, enum_renames = _nested_lite_enums(
            items, by_descriptor, enum_lineage
        )
        _apply_nested_renames(messages, enum_renames)
        combined = RecoveredSchema(
            name=f"recovered_{index}.proto",
            package=package,
            syntax=syntax,
            messages=messages,
            enums=top_level_enums,
            dependencies=list(
                dict.fromkeys(
                    dependency for item in items for dependency in item.dependencies
                )
            ),
            evidence=[evidence for item in items for evidence in item.evidence],
        )
        descriptors.extend(_compiled_descriptors(combined))
    return descriptors


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
    allow_heuristic_lite: Annotated[
        bool,
        typer.Option(
            "--allow-heuristic-lite",
            help="Emit lite schemas when object-array order needs a guess.",
        ),
    ] = False,
    jadx: Annotated[
        bool,
        typer.Option(
            "--jadx", help="Keep jadx decompiled context beside recovery output."
        ),
    ] = False,
    jadx_timeout: Annotated[
        float,
        typer.Option(
            "--jadx-timeout", min=0.1, help="Maximum jadx runtime in seconds."
        ),
    ] = 120.0,
) -> None:
    if not path.is_file():
        raise typer.BadParameter(f"file does not exist: {path}")
    findings = _find(path)
    lite_schemas, bailouts, lineage, enum_lineage = _find_lite(
        path, allow_heuristic=allow_heuristic_lite
    )
    if jadx:
        if detect(path).kind not in {
            ContainerKind.APK,
            ContainerKind.AAB,
            ContainerKind.DEX,
        }:
            raise typer.BadParameter("--jadx only supports APK, AAB, and DEX inputs")
        try:
            result = decompile_with_jadx(
                path, output / "jadx", timeout_seconds=jadx_timeout
            )
        except (JadxError, OSError) as error:
            typer.echo(f"jadx fallback failed: {error}", err=True)
            raise typer.Exit(2) from error
        typer.echo(
            f"jadx fallback: retained {result.source_files} Java sources "
            f"and indexed {result.candidate_sites} protobuf metadata sites "
            f"-> {result.output}"
        )
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
                _compiled_descriptors(schema)
            except ValueError as error:
                typer.echo(f"recovery failed: {error}", err=True)
                raise typer.Exit(2) from error
        prepared.append((schema, source))
    try:
        descriptors.extend(
            _combined_lite_descriptors(
                reconciled.schemas, certain_names, lineage, enum_lineage
            )
        )
    except ValueError as error:
        typer.echo(f"descriptor-set assembly failed: {error}", err=True)
        raise typer.Exit(2) from error
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
def tui() -> None:
    from protoloom.tui.application import run

    run()


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
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed version and exit.",
        ),
    ] = False,
) -> None:
    pass


if __name__ == "__main__":
    app()

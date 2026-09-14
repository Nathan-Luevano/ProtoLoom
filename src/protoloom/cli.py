import json
import os
import re
import secrets
import tempfile
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import asdict
from functools import wraps
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, ParamSpec

import typer
from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet

from protoloom import __version__
from protoloom.bench.corpus import CorpusError, load_manifest
from protoloom.bench.runner import render_report, run_corpus
from protoloom.container.apk import AndroidArchive, ArchiveError, ArchiveInventory
from protoloom.container.detect import ContainerKind, Detection, detect
from protoloom.container.dex import DexError, DexFile
from protoloom.container.elf import ElfError, ElfFile
from protoloom.container.macho import MachOError, MachOFile
from protoloom.container.read import read_limited
from protoloom.decode.descpb import decode_file_descriptor
from protoloom.decode.grpc import decode_grpc_service
from protoloom.decode.lite import decode_lite_finding
from protoloom.decode.wire import (
    decode_wire_adapters,
    decode_wire_annotations,
    decode_wire_enums,
    decode_wire_messages,
)
from protoloom.doctor import diagnose
from protoloom.emit.dashboard import emit_dashboard
from protoloom.emit.descset import emit_descriptor_set
from protoloom.emit.jsonout import emit_json
from protoloom.emit.proto import emit_proto
from protoloom.emit.report import emit_report
from protoloom.extract.descriptor import DescriptorFinding, scan_descriptors
from protoloom.extract.gotags import GoTagExtraction, scan_go_struct_tags
from protoloom.extract.gozip import scan_gzip_descriptors
from protoloom.extract.grpc import scan_grpc_services
from protoloom.extract.jadx import JadxError, decompile_with_jadx
from protoloom.extract.lite import extract_lite
from protoloom.extract.wire import (
    WireAdapterFinding,
    extract_wire_adapter_writes,
    extract_wire_annotations,
    extract_wire_enums,
    extract_wire_messages,
    extract_wire_names,
    extract_wire_null_defaults,
    extract_wire_oneofs,
    extract_wire_syntaxes,
)
from protoloom.model import EnumType, Message, RecoveredSchema
from protoloom.reconcile import reconcile
from protoloom.validate.compile import compile_proto

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
P = ParamSpec("P")
MAX_OUTPUT_NAME_BYTES = 255
MAX_ARTIFACT_MANIFEST_SIZE = 16 * 1024 * 1024
MAX_PREVIOUS_ARTIFACTS = 10_000


def _handle_command_errors(
    label: str,
) -> Callable[[Callable[P, None]], Callable[P, None]]:
    def decorate(command: Callable[P, None]) -> Callable[P, None]:
        @wraps(command)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> None:
            try:
                command(*args, **kwargs)
            except (
                ArchiveError,
                DexError,
                ElfError,
                MachOError,
                OSError,
                UnicodeError,
            ) as error:
                typer.echo(f"{label} failed: {error}", err=True)
                raise typer.Exit(2) from error

        return wrapped

    return decorate


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


def _find(
    path: Path,
    *,
    dex_inputs: list[tuple[str, bytes]] | None = None,
    detection: Detection | None = None,
    inventory: ArchiveInventory | None = None,
) -> list[DescriptorFinding]:
    detection = detection if detection is not None else detect(path)
    findings: list[DescriptorFinding] = []
    cached = dict(dex_inputs or ())
    if detection.kind in {ContainerKind.APK, ContainerKind.AAB, ContainerKind.JAR}:
        archive = AndroidArchive(path)
        inv = inventory if inventory is not None else archive.inventory()
        entries = inv.select({"dex", "native", "asset", "class"})
        for entry, payload in archive.iter_read(entries, cached=cached):
            findings.extend(_scan_blob(payload, entry.name))
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
        raw_data = cached.get(path.name)
        findings.extend(
            _scan_blob(
                raw_data if raw_data is not None else read_limited(path), path.name
            )
        )
    # Dedupe by (name, canonical bytes), not name alone: two sources that
    # embed byte-identical copies of the same .proto are one redundant
    # finding, but differing content under the same name (e.g. two APK
    # modules bundling different versions of a shared dependency) must
    # stay distinct so reconcile() sees both and reports the conflict
    # instead of one version silently vanishing here.
    deduped: dict[tuple[str, bytes], DescriptorFinding] = {}
    for finding in findings:
        key = (finding.descriptor.name, finding.descriptor.SerializeToString())
        deduped.setdefault(key, finding)
    return sorted(deduped.values(), key=lambda item: item.descriptor.name)


def _dex_inputs(
    path: Path,
    *,
    detection: Detection | None = None,
    inventory: ArchiveInventory | None = None,
) -> list[tuple[str, bytes]]:
    detection = detection if detection is not None else detect(path)
    if detection.kind is ContainerKind.DEX:
        return [(path.name, read_limited(path))]
    if detection.kind not in {ContainerKind.APK, ContainerKind.AAB, ContainerKind.JAR}:
        return []
    archive = AndroidArchive(path)
    inv = inventory if inventory is not None else archive.inventory()
    return [
        (entry.name, data) for entry, data in archive.iter_read(inv.select({"dex"}))
    ]


def _find_go_tags(path: Path, *, detection: Detection | None = None) -> GoTagExtraction:
    detection = detection if detection is not None else detect(path)
    if detection.kind is not ContainerKind.ELF:
        return GoTagExtraction((), ())
    elf = ElfFile.from_path(path)
    if not elf.is_go_binary:
        return GoTagExtraction((), ())
    return scan_go_struct_tags(elf, path.name)


def _wire_parent(owner: str) -> str | None:
    if "$" not in owner:
        return None
    return f"{owner.rsplit('$', 1)[0]};"


def _cached_dex(
    inputs: list[tuple[str, bytes]], cache: dict[str, DexFile]
) -> list[tuple[str, DexFile]]:
    # _find_lite/_find_wire/_find_grpc each need a parsed DexFile for every
    # input; without a shared cache they'd each parse the same bytes from
    # scratch, so every dex in an APK got parsed three times over.
    result = []
    for source, data in inputs:
        dex = cache.get(source)
        if dex is None:
            dex = DexFile(data)
            cache[source] = dex
        result.append((source, dex))
    return result


def _find_wire(
    path: Path,
    *,
    dex_inputs: list[tuple[str, bytes]] | None = None,
    dex_cache: dict[str, DexFile] | None = None,
) -> tuple[
    list[RecoveredSchema],
    dict[tuple[str, str], tuple[str, str | None]],
    dict[tuple[str, str], dict[str, str | None]],
]:
    schemas = []
    lineage = {}
    enum_lineage = {}
    raw_inputs = dex_inputs if dex_inputs is not None else _dex_inputs(path)
    cache = dex_cache if dex_cache is not None else {}
    for source, dex in _cached_dex(raw_inputs, cache):
        message_types = set(extract_wire_messages(dex))
        annotations = extract_wire_annotations(dex)
        writes: tuple[WireAdapterFinding, ...] = ()
        if not annotations:
            writes = extract_wire_adapter_writes(dex)
        references = {
            f"L{item.adapter.partition('#')[0].replace('.', '/')};"
            for item in annotations
            if item.adapter.endswith("#ADAPTER")
        }
        references.update(dex.types[item.adapter.class_index] for item in writes)
        message_owners = tuple(
            sorted(owner for owner in message_types & references if "$" in owner)
        )
        owners = set(message_owners)
        owners.update(item.owner for item in annotations)
        owners.update(item.owner for item in writes)
        syntaxes = extract_wire_syntaxes(dex, owners)
        null_defaults = extract_wire_null_defaults(dex, owners)
        decoded = decode_wire_messages(message_owners, source, syntaxes)
        decoded.extend(
            decode_wire_annotations(dex, annotations, source, syntaxes, null_defaults)
        )
        if writes:
            names = extract_wire_names(dex)
            oneofs = extract_wire_oneofs(dex, owners)
            decoded.extend(
                decode_wire_adapters(dex, writes, names, oneofs, source, syntaxes)
            )
        enum_schemas, decoded_enum_lineage = decode_wire_enums(
            extract_wire_enums(dex, writes, annotations), source, syntaxes
        )
        decoded.extend(enum_schemas)
        enum_lineage.update(decoded_enum_lineage)
        for schema in decoded:
            if not schema.messages:
                continue
            owner = schema.evidence[0].location
            lineage[(schema.package, schema.name)] = (owner, _wire_parent(owner))
        schemas.extend(decoded)
    return schemas, lineage, enum_lineage


def _find_grpc(
    path: Path,
    *,
    dex_inputs: list[tuple[str, bytes]] | None = None,
    dex_cache: dict[str, DexFile] | None = None,
) -> list[RecoveredSchema]:
    schemas: list[RecoveredSchema] = []
    raw_inputs = dex_inputs if dex_inputs is not None else _dex_inputs(path)
    cache = dex_cache if dex_cache is not None else {}
    for source, dex in _cached_dex(raw_inputs, cache):
        for evidence in scan_grpc_services(dex):
            schemas.append(decode_grpc_service(dex, evidence, source))
    return schemas


def _find_lite(
    path: Path,
    *,
    allow_heuristic: bool = False,
    dex_inputs: list[tuple[str, bytes]] | None = None,
    dex_cache: dict[str, DexFile] | None = None,
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
    raw_inputs = dex_inputs if dex_inputs is not None else _dex_inputs(path)
    cache = dex_cache if dex_cache is not None else {}
    for source, dex in _cached_dex(raw_inputs, cache):
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


def _compiled_descriptors(
    schema: RecoveredSchema, siblings: dict[str, str] | None = None
) -> list[FileDescriptorProto]:
    result = compile_proto(
        emit_proto(schema), Path(schema.name).name, siblings=siblings
    )
    if not result.success or result.descriptor_set is None:
        raise ValueError(f"emitted schema did not compile: {result.stderr.strip()}")
    descriptor_set = FileDescriptorSet.FromString(result.descriptor_set)
    return list(descriptor_set.file)


def _compiled_descriptors_many(
    schemas: list[RecoveredSchema],
    siblings: dict[str, str] | None = None,
) -> list[list[FileDescriptorProto]]:
    # Each schema shells out to a real protoc subprocess purely to wait on
    # its exit; that wait releases the GIL, so running them on a thread
    # pool overlaps the waits instead of serializing them. Order is
    # preserved (pool.map) so output and first-failure error messages stay
    # identical to running them one at a time.
    if not schemas:
        return []
    workers = min(32, len(schemas))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(
            pool.map(lambda schema: _compiled_descriptors(schema, siblings), schemas)
        )


def _walk_messages(messages: list[Message]) -> list[Message]:
    result: list[Message] = []
    stack = list(messages)
    while stack:
        message = stack.pop()
        result.append(message)
        stack.extend(message.messages)
    return result


def _safe_output_name(name: str) -> None:
    if (
        name in {"", ".", ".."}
        or len(os.fsencode(name)) > MAX_OUTPUT_NAME_BYTES
        or any(unicodedata.category(character).startswith("C") for character in name)
    ):
        raise ValueError("unsafe schema output name")


def _output_names(schemas: list[RecoveredSchema], descriptor_name: str) -> list[str]:
    reserved = {
        "dashboard",
        "recovery.json",
        "report.md",
        descriptor_name.casefold(),
    }
    names: list[str] = []
    seen: set[str] = set()
    for schema in schemas:
        name = Path(schema.name).name
        _safe_output_name(name)
        key = name.casefold()
        if key in reserved or key in seen:
            # Two distinct classes in different packages can share a bare
            # file name (e.g. two unrelated "Relay" messages) -- reconcile()
            # deliberately keeps them separate by (package, name), so the
            # output step disambiguates the file name instead of failing
            # a real, otherwise-fully-recovered extraction.
            name = _disambiguated_output_name(schema, reserved, seen)
            key = name.casefold()
        names.append(name)
        seen.add(key)
    return names


def _disambiguated_output_name(
    schema: RecoveredSchema, reserved: set[str], seen: set[str]
) -> str:
    base = Path(schema.name).name
    stem, dot, extension = base.partition(".")
    if schema.package:
        package = re.sub(r"[^A-Za-z0-9_.]", "_", schema.package)
        candidate = f"{package}.{stem}{dot}{extension}"
        _safe_output_name(candidate)
        key = candidate.casefold()
        if key not in reserved and key not in seen:
            return candidate
        stem = f"{package}.{stem}"
    suffix = 2
    while True:
        candidate = f"{stem}_{suffix}{dot}{extension}"
        _safe_output_name(candidate)
        if candidate.casefold() not in reserved and candidate.casefold() not in seen:
            return candidate
        suffix += 1


def _validate_output(output: Path) -> None:
    if output.is_symlink():
        raise ValueError(f"output directory is a symlink: {output}")
    if output.exists() and not output.is_dir():
        raise ValueError(f"output path is not a directory: {output}")
    for name in ("dashboard", "jadx"):
        directory = output / name
        if directory.is_symlink():
            raise ValueError(f"{name} directory is a symlink: {directory}")
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"{name} path is not a directory: {directory}")


def _validate_output_files(output: Path, names: list[str]) -> None:
    for name in names:
        destination = output / name
        if destination.exists() and not destination.is_file():
            raise ValueError(f"output file path is not a file: {destination}")


def _previous_artifacts(output: Path) -> set[str]:
    manifest = output / "recovery.json"
    if manifest.is_symlink() or not manifest.is_file():
        return set()
    try:
        if manifest.stat().st_size > MAX_ARTIFACT_MANIFEST_SIZE:
            return set()
        with manifest.open("rb") as stream:
            encoded = stream.read(MAX_ARTIFACT_MANIFEST_SIZE + 1)
        if len(encoded) > MAX_ARTIFACT_MANIFEST_SIZE:
            return set()
        payload = json.loads(encoded)
    except (OSError, UnicodeError, ValueError, RecursionError):
        return set()
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        return set()
    artifacts = payload["artifacts"]
    if len(artifacts) > MAX_PREVIOUS_ARTIFACTS:
        return set()
    return {
        name
        for name in artifacts
        if isinstance(name, str)
        and Path(name).name == name
        and Path(name).suffix in {".proto", ".desc"}
    }


def _remove_stale_artifacts(
    output: Path, previous: set[str], current: set[str]
) -> None:
    removed = False
    try:
        for name in sorted(previous - current):
            destination = output / name
            if destination.is_symlink() or destination.is_file():
                destination.unlink()
                removed = True
    except BaseException:
        if removed:
            with suppress(OSError):
                _sync_directory(output)
        raise
    if removed:
        _sync_directory(output)


def _temporary_output(path: Path) -> tuple[Path, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for _ in range(100):
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}")
        try:
            descriptor = os.open(temporary, flags, 0o666)
        except FileExistsError:
            continue
        return temporary, descriptor
    raise OSError(f"cannot allocate temporary output for {path}")


def _atomic_write(path: Path, payload: bytes) -> None:
    _publish_outputs([(path, payload)])


def _stage_output(path: Path, payload: bytes) -> Path:
    temporary, descriptor = _temporary_output(path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _publish_outputs(outputs: list[tuple[Path, bytes]]) -> None:
    paths = [path for path, _ in outputs]
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate output publication path")
    for path in paths:
        if path.exists() and not path.is_file():
            raise ValueError(f"output publication path is not a file: {path}")
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    installed: list[Path] = []
    parents = {path.parent for path, _ in outputs}
    published = False
    try:
        for path, payload in outputs:
            staged[path] = _stage_output(path, payload)
        for path, _ in outputs:
            if path.exists() or path.is_symlink():
                backup, descriptor = _temporary_output(path)
                os.close(descriptor)
                backup.unlink()
                path.replace(backup)
                backups[path] = backup
            installed.append(path)
            staged[path].replace(path)
        for parent in parents:
            _sync_directory(parent)
        published = True
    except BaseException as error:
        rollback_failures: list[str] = []
        for path in reversed(installed):
            try:
                path.unlink(missing_ok=True)
            except OSError as rollback_error:
                rollback_failures.append(f"remove {path}: {rollback_error}")
            if path in backups:
                try:
                    backups[path].replace(path)
                except OSError as rollback_error:
                    rollback_failures.append(f"restore {path}: {rollback_error}")
        for parent in parents:
            with suppress(OSError):
                _sync_directory(parent)
        if rollback_failures:
            error.add_note("; ".join(rollback_failures))
        raise
    finally:
        for temporary in staged.values():
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
        if published:
            for backup in backups.values():
                with suppress(OSError):
                    backup.unlink(missing_ok=True)
            if backups:
                for parent in parents:
                    with suppress(OSError):
                        _sync_directory(parent)


def _sync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
    nested: list[tuple[str, str]] = []
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
        original_name = message.name
        # Some generators name a nested class after its own enclosing class,
        # e.g. AccessMethod$AccessMethod_Bridges. Strip the redundant prefix
        # once the real parent is known, so the emitted nested message keeps
        # the bare local name a ground-truth .proto would use.
        owner_name = enclosing_descriptor.removeprefix("L").removesuffix(";")
        prefixes = (
            f"{owner_name.rsplit('/', 1)[-1].replace('$', '_')}_",
            f"{parent.name}_",
        )
        prefix = next(
            (item for item in prefixes if message.name.startswith(item)), None
        )
        if prefix is not None and len(message.name) > len(prefix):
            new_name = message.name[len(prefix) :]
            message.name = new_name
        nested.append((own_descriptor, original_name))
        parent.messages.append(message)
        attached.add(id(message))
    for own_descriptor, original_name in nested:
        message = by_descriptor[own_descriptor]
        names = [message.name]
        descriptor = enclosing_of.get(own_descriptor)
        while descriptor in by_descriptor:
            names.append(by_descriptor[descriptor].name)
            descriptor = enclosing_of.get(descriptor)
        qualified = ".".join(reversed(names))
        flattened = (
            own_descriptor.removeprefix("L")
            .removesuffix(";")
            .rsplit("/", 1)[-1]
            .replace("$", "_")
        )
        renames[flattened] = qualified
        if original_name != message.name:
            renames[original_name] = qualified
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
        assert owner_descriptor is not None
        owner_name = owner_descriptor.removeprefix("L").removesuffix(";")
        prefixes = (
            f"{owner_name.rsplit('/', 1)[-1].replace('$', '_')}_",
            f"{owner.name}_",
        )
        prefix = next((item for item in prefixes if enum.name.startswith(item)), None)
        if prefix is not None and len(enum.name) > len(prefix):
            new_name = enum.name[len(prefix) :]
            names = [new_name]
            descriptor: str | None = owner_descriptor
            while descriptor in by_descriptor:
                names.append(by_descriptor[descriptor].name)
                descriptor = (
                    f"{descriptor.rsplit('$', 1)[0]};" if "$" in descriptor else None
                )
            enum_renames[enum.name] = ".".join(reversed(names))
            enum.name = new_name
        owner.enums.append(enum)
    return top_level_enums, enum_renames


def _combined_lite_descriptors(
    schemas: list[RecoveredSchema],
    certain_names: set[str],
    lineage: dict[tuple[str, str], tuple[str, str | None]],
    enum_lineage: dict[tuple[str, str], dict[str, str | None]],
) -> list[FileDescriptorProto]:
    # A real .proto file declares exactly one syntax; grouping by
    # (package, syntax) instead of package alone let a single mistakenly
    # syntax-flagged class split its own package into two files, each
    # missing the other's real declarations and synthesizing a hollow
    # placeholder for what the sibling group already declared correctly.
    groups: dict[str, list[RecoveredSchema]] = defaultdict(list)
    for schema in schemas:
        if schema.name not in certain_names:
            groups[schema.package].append(schema)
    combined_schemas: list[RecoveredSchema] = []
    for index, (package, items) in enumerate(sorted(groups.items())):
        syntax = Counter(item.syntax for item in items).most_common(1)[0][0]
        by_descriptor, enclosing_of = _lite_message_index(items, lineage)
        messages = _nested_lite_messages(items, by_descriptor, enclosing_of)
        top_level_enums, enum_renames = _nested_lite_enums(
            items, by_descriptor, enum_lineage
        )
        _apply_nested_renames(messages, enum_renames)
        combined_schemas.append(
            RecoveredSchema(
                name=f"recovered_{index}.proto",
                package=package,
                syntax=syntax,
                messages=messages,
                enums=top_level_enums,
                services=[service for item in items for service in item.services],
                dependencies=list(
                    dict.fromkeys(
                        dependency for item in items for dependency in item.dependencies
                    )
                ),
                evidence=[evidence for item in items for evidence in item.evidence],
            )
        )
    descriptors: list[FileDescriptorProto] = []
    for result in _compiled_descriptors_many(combined_schemas):
        descriptors.extend(result)
    return descriptors


@app.command()
@_handle_command_errors("inspection")
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
@_handle_command_errors("extraction")
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
    descriptor_name = f"{path.stem}.desc"
    try:
        _validate_output(output)
        _validate_output_files(output, [descriptor_name, "recovery.json", "report.md"])
    except ValueError as error:
        typer.echo(f"recovery failed: {error}", err=True)
        raise typer.Exit(2) from error
    previous_artifacts = _previous_artifacts(output)
    # detect() and inventory() each re-open and re-parse the archive's
    # central directory; computed once here instead of once per finder.
    detection = detect(path)
    inventory = (
        AndroidArchive(path).inventory()
        if detection.kind in {ContainerKind.APK, ContainerKind.AAB, ContainerKind.JAR}
        else None
    )
    dex_inputs = _dex_inputs(path, detection=detection, inventory=inventory)
    findings = _find(
        path, dex_inputs=dex_inputs, detection=detection, inventory=inventory
    )
    go_tags = (
        _find_go_tags(path, detection=detection)
        if not findings
        else GoTagExtraction((), ())
    )
    # Shared across the three finders below: each used to construct its own
    # DexFile(data) from the same bytes, so every input dex was parsed
    # three times over.
    dex_cache: dict[str, DexFile] = {}
    lite_schemas, bailouts, lineage, enum_lineage = _find_lite(
        path,
        allow_heuristic=allow_heuristic_lite,
        dex_inputs=dex_inputs,
        dex_cache=dex_cache,
    )
    wire_schemas, wire_lineage, wire_enum_lineage = _find_wire(
        path, dex_inputs=dex_inputs, dex_cache=dex_cache
    )
    grpc_schemas = _find_grpc(path, dex_inputs=dex_inputs, dex_cache=dex_cache)
    lineage.update(wire_lineage)
    enum_lineage.update(wire_enum_lineage)
    bailouts.extend(f"{path.name}: {reason}" for reason in go_tags.bailouts)
    if jadx:
        if detection.kind not in {
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
    if (
        not findings
        and not lite_schemas
        and not go_tags.schemas
        and not wire_schemas
        and not grpc_schemas
    ):
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
    schemas.extend(go_tags.schemas)
    schemas.extend(lite_schemas)
    schemas.extend(wire_schemas)
    schemas.extend(grpc_schemas)
    try:
        reconciled = reconcile(schemas)
    except ValueError as error:
        typer.echo(f"reconciliation failed: {error}", err=True)
        raise typer.Exit(2) from error
    try:
        output_names = _output_names(reconciled.schemas, descriptor_name)
        _validate_output_files(
            output,
            [*output_names, descriptor_name, "recovery.json", "report.md"],
        )
        _validate_output_files(output / "dashboard", ["index.html"])
    except ValueError as error:
        typer.echo(f"recovery failed: {error}", err=True)
        raise typer.Exit(2) from error
    # Two findings can now legitimately share a .proto name with different
    # content (e.g. two APK modules bundling different versions of a shared
    # dependency): feeding both raw descriptors into the same descriptor
    # set would hard-fail on the name collision, so a conflicting name is
    # routed through reconcile's merge/recompile path below instead of the
    # verbatim "certain" fast path, same as any other reported conflict.
    name_counts = Counter(finding.descriptor.name for finding in findings)
    conflicting_names = {name for name, count in name_counts.items() if count > 1}
    descriptors = [
        finding.descriptor
        for finding in findings
        if finding.descriptor.name not in conflicting_names
    ]
    certain_names = {
        finding.descriptor.name for finding in findings
    } - conflicting_names
    prepared = [(schema, emit_proto(schema)) for schema in reconciled.schemas]
    # Every recovered schema is compile-validated now, "certain" ones
    # included: a descriptor recovered whole from the binary can still
    # reference a type that was never actually found (corrupt data, or a
    # dependency that got stripped), and writing that out as a "recovered"
    # .proto with no diagnostic would hide a genuinely broken file. Sibling
    # sources sit on the import path (not as compile targets) so a
    # legitimate cross-file import still resolves without pulling in
    # unrelated files that happen to declare same-named nested types.
    siblings = dict(zip(output_names, (source for _, source in prepared), strict=True))
    try:
        _compiled_descriptors_many(reconciled.schemas, siblings)
    except ValueError as error:
        typer.echo(f"recovery failed: {error}", err=True)
        raise typer.Exit(2) from error
    try:
        descriptors.extend(
            _combined_lite_descriptors(
                reconciled.schemas, certain_names, lineage, enum_lineage
            )
        )
    except ValueError as error:
        typer.echo(f"descriptor-set assembly failed: {error}", err=True)
        raise typer.Exit(2) from error
    try:
        descriptor_set = emit_descriptor_set(descriptors)
    except ValueError as error:
        typer.echo(f"descriptor-set assembly failed: {error}", err=True)
        raise typer.Exit(2) from error
    conflicts = [asdict(conflict) for conflict in reconciled.conflicts]
    artifacts = [
        *output_names,
        descriptor_name,
        "dashboard/index.html",
        "recovery.json",
        "report.md",
    ]
    try:
        report = emit_report(reconciled.schemas, bailouts).encode()
        dashboard_page = emit_dashboard(
            reconciled.schemas, reconciled.conflicts
        ).encode()
        recovery_json = emit_json(reconciled.schemas, conflicts, artifacts).encode()
    except ValueError as error:
        typer.echo(f"output generation failed: {error}", err=True)
        raise typer.Exit(2) from error
    output.mkdir(parents=True, exist_ok=True)
    dashboard = output / "dashboard"
    dashboard.mkdir(exist_ok=True)
    outputs = [
        (output / name, source.encode())
        for (_, source), name in zip(prepared, output_names, strict=True)
    ]
    outputs.extend(
        [
            (output / descriptor_name, descriptor_set),
            (output / "report.md", report),
            (dashboard / "index.html", dashboard_page),
            (output / "recovery.json", recovery_json),
        ]
    )
    _publish_outputs(outputs)
    for (schema, _source), name in zip(prepared, output_names, strict=True):
        destination = output / name
        typer.echo(f"recovered {schema.name} -> {destination}")
    _remove_stale_artifacts(output, previous_artifacts, set(artifacts))
    typer.echo(
        f"bail-outs: {len(bailouts)}; recovered files: {len(reconciled.schemas)}"
    )


@app.command()
def doctor() -> None:
    import shutil

    report = diagnose()
    dependencies = {item.name: item for item in report.dependencies}
    checks = {
        "protoc": dependencies["protoc"].location,
        "jadx (optional)": dependencies["jadx"].location,
        "docker (optional)": shutil.which("docker"),
    }
    result = {name: value or "missing" for name, value in checks.items()}
    typer.echo(json.dumps(result, indent=2))
    # protoc is the only required dependency surfaced here; a caller
    # scripting against this command needs the exit code to actually
    # reflect that, not just the JSON body, to detect a broken install.
    if not report.healthy:
        raise typer.Exit(code=1)


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
    finding = scan_descriptors(blob, "built-in demo")[0]
    schema = decode_file_descriptor(finding.descriptor, finding.source, "0x10")
    outputs = [
        (output / "demo.proto", emit_proto(schema).encode()),
        (output / "demo.desc", emit_descriptor_set([finding.descriptor])),
    ]
    try:
        _validate_output(output)
        _validate_output_files(output, ["demo.proto", "demo.desc"])
        output.mkdir(parents=True, exist_ok=True)
        _publish_outputs(outputs)
    except (OSError, ValueError) as error:
        typer.echo(f"demo failed: {error}", err=True)
        raise typer.Exit(2) from error
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

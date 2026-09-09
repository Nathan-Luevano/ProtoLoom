import hashlib
import os
import shutil
import stat
import tarfile
import tempfile
import unicodedata
import urllib.request
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlparse

MAX_SOURCE_ARCHIVE_MEMBERS = 100_000
MAX_SOURCE_EXTRACTED_SIZE = 1024 * 1024 * 1024
MAX_SOURCE_DOWNLOAD_SIZE = 128 * 1024 * 1024
MAX_UPSTREAM_NAME_BYTES = 255
MAX_UPSTREAM_SOURCES = 100
MAX_UPSTREAM_FILES = 10_000
MAX_UPSTREAM_INCLUDES = 1_000
MAX_UPSTREAM_TARGETS = 10_000
LOWER_HEX = frozenset("0123456789abcdef")


def sha256(path: Path, max_size: int = MAX_SOURCE_DOWNLOAD_SIZE) -> str:
    if max_size <= 0:
        raise ValueError("hash size limit must be positive")
    digest = hashlib.sha256()
    total = 0
    with _open_regular(path) as stream:
        status = os.fstat(stream.fileno())
        if status.st_size > max_size:
            raise ValueError(f"source exceeds {max_size} bytes: {path}")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            if total > max_size:
                raise ValueError(f"source exceeds {max_size} bytes: {path}")
            digest.update(chunk)
    return digest.hexdigest()


def _open_regular(path: Path) -> IO[bytes]:
    stream = path.open("rb")
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        raise ValueError(f"source is not a regular file: {path}")
    return stream


def https_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username is not None:
        raise ValueError(f"archive URL must be unauthenticated HTTPS: {value}")
    return value


def validate_source_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
        raise ValueError("source manifest needs a sources array")
    if len(value["sources"]) > MAX_UPSTREAM_SOURCES:
        raise ValueError(f"source manifest exceeds {MAX_UPSTREAM_SOURCES} sources")
    names: set[str] = set()
    targets: set[str] = set()
    for source in value["sources"]:
        if not isinstance(source, dict):
            raise ValueError("source entry must be an object")
        name = _safe_name(source.get("name"), "source")
        commit = source.get("commit")
        if name in names:
            raise ValueError(f"unsafe or duplicate source name: {name}")
        names.add(name)
        if (
            not isinstance(commit, str)
            or len(commit) != 40
            or any(character not in LOWER_HEX for character in commit)
        ):
            raise ValueError(f"source {name} needs a full commit SHA")
        files = source.get("files")
        if files is None:
            _validate_remote(source, f"source {name}")
        elif isinstance(files, list) and files:
            if len(files) > MAX_UPSTREAM_FILES:
                raise ValueError(f"source {name} exceeds {MAX_UPSTREAM_FILES} files")
            file_paths: set[Path] = set()
            for artifact in files:
                if not isinstance(artifact, dict):
                    raise ValueError("source file must be an object")
                path = Path(_string(artifact.get("path"), "source file path"))
                if path.is_absolute() or ".." in path.parts or not path.name:
                    raise ValueError(f"unsafe source file path: {path}")
                if path in file_paths:
                    raise ValueError(f"duplicate source file path: {path}")
                file_paths.add(path)
                _validate_remote(artifact, f"source file {path}")
        else:
            raise ValueError(f"source {name} files must be a non-empty array")
        includes = source.get("includes")
        if not isinstance(includes, list) or not includes:
            raise ValueError(f"source {name} needs include roots")
        if len(includes) > MAX_UPSTREAM_INCLUDES:
            raise ValueError(f"source {name} exceeds {MAX_UPSTREAM_INCLUDES} includes")
        include_paths: set[Path] = set()
        for include in includes:
            path = Path(_string(include, "include root"))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe include root: {include}")
            if path in include_paths:
                raise ValueError(f"duplicate include root: {include}")
            include_paths.add(path)
        entries = source.get("targets")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"source {name} needs targets")
        if len(targets) + len(entries) > MAX_UPSTREAM_TARGETS:
            raise ValueError(f"source manifest exceeds {MAX_UPSTREAM_TARGETS} targets")
        for target in entries:
            if not isinstance(target, dict):
                raise ValueError("target entry must be an object")
            target_name = _safe_name(target.get("name"), "target")
            proto = Path(_string(target.get("proto"), "target proto"))
            if target_name in targets:
                raise ValueError(f"unsafe or duplicate target name: {target_name}")
            if proto.is_absolute() or ".." in proto.parts or proto.suffix != ".proto":
                raise ValueError(f"unsafe target proto: {proto}")
            if target.get("compiled_leg") not in {None, "cpp-object"}:
                raise ValueError(f"unsupported compiled leg: {target['compiled_leg']}")
            targets.add(target_name)
    return value


def _validate_remote(value: dict[str, Any], label: str) -> None:
    digest = value.get("sha256")
    size = value.get("size")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in LOWER_HEX for character in digest)
    ):
        raise ValueError(f"{label} needs a SHA-256")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError(f"{label} needs a positive pinned size")
    https_url(_string(value.get("url"), f"{label} URL"))


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _safe_name(value: object, label: str) -> str:
    name = _string(value, f"{label} name")
    if (
        name in {"", ".", ".."}
        or Path(name).name != name
        or len(name.encode("utf-8")) > MAX_UPSTREAM_NAME_BYTES
        or any(unicodedata.category(character).startswith("C") for character in name)
    ):
        raise ValueError(f"unsafe or duplicate {label} name: {name}")
    return name


def download(url: str, expected: str, size: int, destination: Path) -> None:
    https_url(url)
    if size <= 0 or size > MAX_SOURCE_DOWNLOAD_SIZE:
        raise ValueError(f"archive size is outside the 128 MiB limit: {size}")
    if destination.is_symlink():
        raise ValueError(f"archive cache path is a symlink: {destination}")
    if (
        destination.is_file()
        and destination.stat().st_size == size
        and sha256(destination) == expected
    ):
        return
    partial = destination.with_name(destination.name + ".part")
    if partial.is_symlink():
        raise ValueError(f"partial cache path is a symlink: {partial}")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "protoloom-corpus/1"})
    try:
        with (
            urllib.request.urlopen(request, timeout=60) as response,
            partial.open("xb") as out,
        ):
            https_url(response.geturl())
            written = 0
            while chunk := response.read(min(1024 * 1024, size + 1 - written)):
                written += len(chunk)
                if written > size:
                    raise ValueError(f"archive exceeds pinned size: {url}")
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if written != size:
            raise ValueError(f"archive size mismatch: expected {size}, got {written}")
        actual = sha256(partial)
        if actual != expected:
            raise ValueError(
                f"archive hash mismatch: expected {expected}, got {actual}"
            )
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def extract(
    archive: Path,
    destination: Path,
    *,
    max_members: int = MAX_SOURCE_ARCHIVE_MEMBERS,
    max_size: int = MAX_SOURCE_EXTRACTED_SIZE,
) -> Path:
    if max_members <= 0 or max_size <= 0:
        raise ValueError("source extraction limits must be positive")
    if destination.is_symlink():
        raise ValueError(f"source extraction path is a symlink: {destination}")
    if destination.exists():
        raise ValueError(f"source extraction path already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        with (
            _open_regular(archive) as archive_stream,
            tarfile.open(fileobj=archive_stream, mode="r:gz") as bundle,
        ):
            members = bundle.getmembers()
            if not members:
                raise ValueError(f"empty archive: {archive}")
            if len(members) > max_members:
                raise ValueError(f"archive contains more than {max_members} members")
            total_size = sum(member.size for member in members if member.isfile())
            if total_size > max_size:
                raise ValueError(f"archive expands beyond {max_size} bytes")
            paths: set[tuple[str, ...]] = set()
            roots: set[str] = set()
            for member in members:
                path = Path(member.name)
                if not path.parts or path.is_absolute() or ".." in path.parts:
                    raise ValueError(f"unsafe archive member: {member.name}")
                if not (member.isfile() or member.isdir()):
                    raise ValueError(f"non-file archive member refused: {member.name}")
                if path.parts in paths:
                    raise ValueError(f"duplicate archive member: {member.name}")
                paths.add(path.parts)
                roots.add(path.parts[0])
            if len(roots) != 1:
                raise ValueError(f"archive needs one root directory: {archive}")
            for member in members:
                output = staging / member.name
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise ValueError(f"cannot read archive member: {member.name}")
                with source, output.open("xb") as stream:
                    _copy_member(source, stream, member.size)
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination / roots.pop()


def _copy_member(source: IO[bytes], output: IO[bytes], expected_size: int) -> None:
    copied = 0
    while chunk := source.read(min(1024 * 1024, expected_size - copied + 1)):
        copied += len(chunk)
        if copied > expected_size:
            raise ValueError("archive member exceeds its declared size")
        output.write(chunk)
    if copied != expected_size:
        raise ValueError("archive member is shorter than its declared size")


def materialize_source(source: dict[str, Any], cache: Path, root: Path) -> Path:
    files = source.get("files")
    if files is None:
        archive = cache / f"{source['name']}-{source['commit']}.tar.gz"
        download(source["url"], source["sha256"], source["size"], archive)
        return extract(archive, root)
    root.mkdir(parents=True, exist_ok=True)
    for artifact in files:
        output = root / artifact["path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        download(artifact["url"], artifact["sha256"], artifact["size"], output)
    return root
